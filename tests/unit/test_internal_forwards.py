"""An external mail forwarded between colleagues is filed with its external author."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mailflow.classifier.pipeline import ClassificationPipeline
from mailflow.classifier.routing_context import primary_external_email, resolve_counterparty
from mailflow.core.contact_directory import observation_for_email, scanned_contact_observations
from mailflow.core.forwarded_mail import find_forwarded_message, find_internal_forward
from mailflow.core.manual_review import apply_manual_classification
from mailflow.models import (
    AiMailClassification,
    AiMode,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualClassificationUpdate,
    PreviewAction,
)
from mailflow.outlook.scanner import OutlookScanner

PROJECT = "2025-4893"
SUPPLIER = "jean.dupont@aciers.ch"

# Body as Outlook gives it, with the non-breaking space French puts before a colon.
OUTLOOK_FORWARD = (
    "Salut Lionel,\r\n"
    "Voici l'offre pour les profils, à valider.\r\n"
    "\r\n"
    "Cordialement\r\n"
    "André\r\n"
    "\r\n"
    "De : Jean Dupont <jean.dupont@aciers.ch> \r\n"
    "Envoyé : mardi 5 mai 2026 14:12\r\n"
    "À : André Atelier <andre@balzmetal.ch>\r\n"
    "Objet : Offre profils 2025-4893\r\n"
    "\r\n"
    "Bonjour,\r\n"
    "Veuillez trouver notre offre no 4411 en annexe.\r\n"
    "\r\n"
    "Meilleures salutations\r\n"
    "Jean Dupont\r\n"
    "\r\n"
    "De : André Atelier <andre@balzmetal.ch>\r\n"
    "Envoyé : lundi 4 mai 2026 09:00\r\n"
    "Objet : Demande de prix\r\n"
    "\r\n"
    "Ancienne demande citée.\r\n"
).replace(" :", "\N{NO-BREAK SPACE}:")


def header(sender: str, *, to: str = "André <andre@balzmetal.ch>") -> str:
    return f"De : {sender}\nEnvoyé : mardi 5 mai 2026 14:12\nÀ : {to}\nObjet : Offre\n\nTexte.\n"


def make_mail(**updates: Any) -> MailMetadata:
    values: dict[str, Any] = {
        "entry_id": "ENTRY-1",
        "project_number": PROJECT,
        "outlook_folder": f"Boite de reception/2025/{PROJECT}",
        "direction": Direction.RECEIVED,
        "subject": "TR: Offre profils",
        "sender_name": "Andre Atelier",
        "sender_email": "andre@balzmetal.ch",
        "recipients": ["Lionel <lionel@balzmetal.ch>"],
        "sent_at": datetime(2026, 5, 6, 10, 30),
        "forwarded_external_email": SUPPLIER,
    }
    return MailMetadata(**(values | updates))


class CountingClassifier:
    """Engine double: counts its calls and keeps the context it was given."""

    def __init__(self) -> None:
        self.calls = 0
        self.contexts: list[dict[str, Any]] = []

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        del mail, include_body, privacy_mask_phone_numbers
        self.calls += 1
        self.contexts.append(known_context or {})
        return AiMailClassification.model_validate({
            "category": "Commande", "organization_role": "fournisseur",
            "organization_name": None, "confidence": 0.95, "requires_review": False,
            "short_summary": "Resume", "reason": "Raison du moteur.", "evidence": [],
        })


class Directory:
    """A supplier (aciers.ch), a client (archi.ch) and a sister company (interne)."""

    names = {
        "aciers.ch": "Aciers SA", "archi.ch": "Archi Sarl", "balz-holding.ch": "Balz Holding",
    }
    roles = {
        "aciers.ch": InterlocutorType.FOURNISSEUR,
        "archi.ch": InterlocutorType.CLIENT,
        "balz-holding.ch": InterlocutorType.INTERNE,
    }

    def organization_name_for_email(self, email: str) -> str | None:
        return self.names.get(email.rsplit("@", 1)[-1])

    def interlocutor_for_email(self, project_number: str, email: str) -> InterlocutorType | None:
        del project_number
        return self.roles.get(email.rsplit("@", 1)[-1])


def make_pipeline(tmp_path: Path, classifier: CountingClassifier | None) -> ClassificationPipeline:
    (tmp_path / "2025" / PROJECT).mkdir(parents=True, exist_ok=True)
    return ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=classifier,
        organization_directory=Directory(),
    )


# --- detection -----------------------------------------------------------------------


def test_outlook_forward_gives_the_external_author_and_the_forwarded_text() -> None:
    forwarded = find_forwarded_message("TR: Offre profils 2025-4893", OUTLOOK_FORWARD)

    assert forwarded is not None
    assert forwarded.external_email == SUPPLIER
    excerpt = forwarded.excerpt()
    assert excerpt.startswith("Salut Lionel,\nVoici l'offre pour les profils, à valider.")
    assert f"Message transféré de {SUPPLIER} :" in excerpt
    assert "Veuillez trouver notre offre no 4411 en annexe." in excerpt
    # Signatures and the older quoted message are still cut.
    assert "André" not in excerpt
    assert "Meilleures salutations" not in excerpt
    assert "Ancienne demande" not in excerpt


@pytest.mark.parametrize("subject", ["TR: Offre", "TR : Offre", "FW: Offre", "Fwd: Offre",
                                     "WG: Angebot", "tr: offre", "TR: RE: Offre"])
def test_forward_prefixes_are_recognised(subject: str) -> None:
    forwarded = find_forwarded_message(subject, header("Jean <jean.dupont@aciers.ch>"))

    assert forwarded is not None
    assert forwarded.external_email == SUPPLIER


@pytest.mark.parametrize(
    ("subject", "body"),
    [
        # A reply quotes the same block: only the subject tells a transfer apart.
        ("RE: Offre", header("Jean <jean.dupont@aciers.ch>")),
        ("RE: TR: Offre", header("Jean <jean.dupont@aciers.ch>")),
        ("Offre", header("Jean <jean.dupont@aciers.ch>")),
        # No header block at all.
        ("TR: Offre", "Voir l'offre de jean.dupont@aciers.ch en annexe."),
        # A sentence that starts like a header, without date nor subject.
        ("TR: Offre", "De : jean.dupont@aciers.ch, rien de neuf.\n\nSalutations"),
        # Forwarded message between colleagues only.
        ("TR: Planning", header("Marc <marc@balzmetal.ch>")),
        # Names without addresses prove nothing.
        ("TR: Offre", header("Jean Dupont", to="André Atelier")),
    ],
)
def test_without_a_forwarded_external_message_nothing_is_found(subject: str, body: str) -> None:
    assert find_forwarded_message(subject, body) is None


def test_message_sent_by_a_colleague_gives_its_first_external_recipient() -> None:
    body = header(
        "André Atelier <andre@balzmetal.ch>",
        to="Marc <marc@balzmetal.ch>; Jean Dupont <jean.dupont@aciers.ch>",
    )

    forwarded = find_forwarded_message("TR: Commande 4411", body)

    assert forwarded is not None
    assert forwarded.external_email == SUPPLIER


def test_mail_forwarded_twice_inside_the_company_reaches_the_external_author() -> None:
    body = (
        "Pour toi.\n\n"
        + header("Marc Bureau", to="André Atelier")
        + "\n"
        + header("Jean Dupont [mailto:Jean.Dupont@Aciers.ch]")
    )

    forwarded = find_forwarded_message("TR: TR: Offre", body)

    assert forwarded is not None
    assert forwarded.external_email == SUPPLIER


def test_english_and_long_recipient_headers_are_read() -> None:
    body = (
        "---------- Forwarded message ---------\n"
        "From: Andre <andre@balzmetal.ch>\n"
        "Date: Tue, 5 May 2026 at 14:12\n"
        "Subject: Order 4411\n"
        "To: Marc <marc@balzmetal.ch>,\n"
        "    Jean Dupont <jean.dupont@aciers.ch>\n"
        "\n"
        "Please confirm.\n"
    )

    forwarded = find_forwarded_message("Fwd: Order 4411", body)

    assert forwarded is not None
    assert forwarded.external_email == SUPPLIER
    assert forwarded.excerpt() == f"Message transféré de {SUPPLIER} :\nPlease confirm."


def test_only_a_mail_between_colleagues_is_read_as_an_internal_forward() -> None:
    body = header("Jean <jean.dupont@aciers.ch>")

    assert find_internal_forward(
        "TR: Offre", body, "andre@balzmetal.ch", ["lionel@balzmetal.ch"],
    ) is not None
    assert find_internal_forward(
        "TR: Offre", body, "andre@balzmetal.ch", ["lionel@balzmetal.ch", "chef@archi.ch"],
    ) is None
    assert find_internal_forward(
        "TR: Offre", body, "vente@autre.ch", ["lionel@balzmetal.ch"],
    ) is None


# --- scan ----------------------------------------------------------------------------


def outlook_item(*, sender: str, subject: str, body: str) -> object:
    return SimpleNamespace(
        EntryID="ENTRY-1",
        MessageClass="IPM.Note",
        Subject=subject,
        SenderName="Expediteur",
        SenderEmailAddress=sender,
        Recipients=[SimpleNamespace(Type=1, Address="lionel@balzmetal.ch")],
        SentOn=datetime(2026, 5, 6, 10, 30),
        Attachments=[],
        Body=body,
        Categories="",
    )


def test_scan_keeps_the_forwarded_author_and_text_of_an_internal_forward() -> None:
    item = outlook_item(
        sender="andre@balzmetal.ch", subject="TR: Offre profils", body=OUTLOOK_FORWARD,
    )

    mail = OutlookScanner().mail_item_to_metadata(
        item, project_number=PROJECT, outlook_folder="Inbox",
    )

    assert mail.forwarded_external_email == SUPPLIER
    assert "Veuillez trouver notre offre no 4411 en annexe." in mail.body_excerpt
    assert primary_external_email(mail) == SUPPLIER


def test_scan_leaves_other_mails_unchanged() -> None:
    scanner = OutlookScanner()
    reply = outlook_item(
        sender="andre@balzmetal.ch", subject="RE: Offre profils", body=OUTLOOK_FORWARD,
    )
    external = outlook_item(
        sender="vente@autre.ch", subject="TR: Offre profils", body=OUTLOOK_FORWARD,
    )

    for item in (reply, external):
        mail = scanner.mail_item_to_metadata(item, project_number=PROJECT, outlook_folder="Inbox")

        assert mail.forwarded_external_email == ""
        assert mail.body_excerpt == "Salut Lionel,\nVoici l'offre pour les profils, à valider."


# --- routing -------------------------------------------------------------------------


def test_forwarded_supplier_mail_is_filed_in_the_supplier_folder(tmp_path: Path) -> None:
    classifier = CountingClassifier()
    pipeline = make_pipeline(tmp_path, classifier)
    mail = make_mail()

    counterparty = resolve_counterparty(mail, Directory())
    row = pipeline.preview([mail])[0]

    assert counterparty.source == "internal_forward"
    assert counterparty.email == SUPPLIER
    assert counterparty.organization_name == "Aciers SA"
    assert counterparty.role == InterlocutorType.FOURNISSEUR
    # The engine still chooses the phase, knowing whose message was forwarded.
    assert classifier.calls == 1
    assert classifier.contexts[0]["counterparty"]["source"] == "internal_forward"
    assert classifier.contexts[0]["counterparty"]["primary_email"] == SUPPLIER
    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.interlocutor == InterlocutorType.FOURNISSEUR
    assert row.decision.target_relative_folder == "Fournisseurs/Commande/Aciers SA"
    assert f"Transfert interne: classe chez l'auteur du message transfere ({SUPPLIER})." in (
        row.decision.reason
    )


def test_forward_shares_the_folder_of_the_mails_received_from_the_same_supplier(
    tmp_path: Path,
) -> None:
    pipeline = make_pipeline(tmp_path, CountingClassifier())
    direct = make_mail(
        entry_id="ENTRY-2", subject="Confirmation de commande", sender_name="Jean Dupont",
        sender_email=SUPPLIER, forwarded_external_email="",
    )

    rows = pipeline.preview([make_mail(), direct])

    assert {row.decision.target_relative_folder for row in rows} == {
        "Fournisseurs/Commande/Aciers SA"
    }


def test_forwarded_client_mail_goes_to_the_client_correspondence(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path, CountingClassifier())

    row = pipeline.preview([make_mail(forwarded_external_email="chef@archi.ch")])[0]

    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.interlocutor == InterlocutorType.CLIENT
    assert row.decision.confidence == 1.0
    assert row.decision.target_relative_folder == "Correspondance/Archi Sarl"


def test_forward_from_a_company_without_role_stays_to_review_under_its_name(
    tmp_path: Path,
) -> None:
    pipeline = make_pipeline(tmp_path, CountingClassifier())

    row = pipeline.preview([make_mail(forwarded_external_email="vente@inconnu-metal.ch")])[0]

    # A transfer never guesses a role: like any mail, the directory must confirm it.
    assert row.action == PreviewAction.REVIEW
    assert row.decision.target_relative_folder == "A verifier"
    assert "Balzmetal" not in row.decision.target_relative_folder


def test_forward_from_a_company_declared_internal_stays_internal(tmp_path: Path) -> None:
    classifier = CountingClassifier()
    pipeline = make_pipeline(tmp_path, classifier)

    row = pipeline.preview([make_mail(forwarded_external_email="direction@balz-holding.ch")])[0]

    assert classifier.calls == 0
    assert row.decision.target_relative_folder == "Correspondance/Balzmetal"


def test_internal_mail_without_forwarded_message_still_goes_to_balzmetal(tmp_path: Path) -> None:
    classifier = CountingClassifier()
    pipeline = make_pipeline(tmp_path, classifier)

    row = pipeline.preview([make_mail(subject="RE: Offre", forwarded_external_email="")])[0]

    assert classifier.calls == 0
    assert row.decision.target_relative_folder == "Correspondance/Balzmetal"


def test_forwarded_author_is_ignored_when_an_external_address_takes_part(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path, CountingClassifier())
    mail = make_mail(recipients=["lionel@balzmetal.ch", "chef@archi.ch"])

    row = pipeline.preview([mail])[0]

    # The addresses of the mail itself always come first.
    assert primary_external_email(mail) == "chef@archi.ch"
    assert row.decision.target_relative_folder == "Correspondance/Archi Sarl"


def test_forwarded_author_becomes_a_contact_of_the_project_in_the_directory() -> None:
    mail = make_mail()

    observations = scanned_contact_observations([mail, make_mail(forwarded_external_email="")])
    observation = observation_for_email(mail, SUPPLIER)

    assert [item.email for item in observations] == [SUPPLIER]
    assert observation is not None
    assert observation.project_number == PROJECT


def test_manual_choice_interne_on_a_forward_files_it_in_balzmetal(tmp_path: Path) -> None:
    row = make_pipeline(tmp_path, CountingClassifier()).preview([make_mail()])[0]

    updated, signal = apply_manual_classification(
        row,
        ManualClassificationUpdate(
            mail_type=MailType.COMMANDE,
            interlocutor=InterlocutorType.INTERNE,
            target_relative_folder="A verifier",
        ),
        projects_root=tmp_path,
    )

    assert updated.decision.target_relative_folder == "Correspondance/Balzmetal"
    assert signal.organization_name == "Balzmetal"
