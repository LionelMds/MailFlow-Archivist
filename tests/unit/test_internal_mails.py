"""Mails between colleagues: detected on their addresses, filed in Correspondance/Balzmetal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

from mailflow.classifier.pipeline import ClassificationPipeline
from mailflow.classifier.routing_context import primary_external_email, resolve_counterparty
from mailflow.core.app_controller import apply_directory_roles_to_rows
from mailflow.core.correspondence_hierarchy import company_from_mail, company_key_from_mail
from mailflow.core.domains import is_internal_mail
from mailflow.core.manual_review import (
    apply_manual_classification,
    suggested_manual_destination,
)
from mailflow.models import (
    AiMailClassification,
    AiMode,
    ArchivedMailRecord,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualClassificationUpdate,
    PreviewAction,
)
from mailflow.outlook.categories import ARCHIVED_CATEGORY

PROJECT = "2025-4893"
INTERNAL_FOLDER = "Correspondance/Balzmetal"
EXCHANGE_NAME = (
    "/O=EXCHANGELABS/OU=EXCHANGE ADMINISTRATIVE GROUP (FYDIBOHF23SPDLT)"
    "/CN=RECIPIENTS/CN=0123456789-ANDRE"
)


def make_mail(**updates: Any) -> MailMetadata:
    values: dict[str, Any] = {
        "entry_id": "ENTRY-1",
        "project_number": PROJECT,
        "outlook_folder": f"Boite de reception/2025/{PROJECT}",
        "direction": Direction.RECEIVED,
        "subject": "Planning atelier",
        "sender_name": "Andre Atelier",
        "sender_email": "andre@balzmetal.ch",
        "recipients": ["Lionel <lionel@balzmetal.ch>"],
        "sent_at": datetime(2026, 5, 6, 10, 30),
    }
    return MailMetadata(**(values | updates))


class CountingClassifier:
    """Engine double: counts its calls and answers like an engine sure of itself."""

    def __init__(self) -> None:
        self.calls = 0

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        del mail, include_body, privacy_mask_phone_numbers, known_context
        self.calls += 1
        return AiMailClassification.model_validate({
            "category": "Commande", "organization_role": "fournisseur",
            "organization_name": None, "confidence": 0.95, "requires_review": False,
            "short_summary": "Resume", "reason": "Raison du moteur.", "evidence": [],
        })


class Directory:
    """Directory with a client (archi.ch) and a sister company declared internal."""

    names = {"archi.ch": "Archi Sarl", "balz-holding.ch": "Balz Holding"}
    roles = {"archi.ch": InterlocutorType.CLIENT, "balz-holding.ch": InterlocutorType.INTERNE}

    def organization_name_for_email(self, email: str) -> str | None:
        return self.names.get(email.rsplit("@", 1)[-1])

    def interlocutor_for_email(self, project_number: str, email: str) -> InterlocutorType | None:
        del project_number
        return self.roles.get(email.rsplit("@", 1)[-1])


def make_pipeline(
    tmp_path: Path,
    classifier: CountingClassifier | None,
    *,
    ai_mode: AiMode = AiMode.ALL,
    with_project_folder: bool = True,
) -> ClassificationPipeline:
    if with_project_folder:
        (tmp_path / "2025" / PROJECT).mkdir(parents=True, exist_ok=True)
    return ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=ai_mode,
        ai_classifier=classifier,
        organization_directory=Directory(),
    )


# --- detection -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sender", "recipients", "expected"),
    [
        ("andre@balzmetal.ch", ["lionel@balzmetal.ch"], True),
        ("andre@balzmetal.ch", ["Lionel <lionel@balzmetal.ch>", "info@balzmetal.ch"], True),
        ("ANDRE@BALZMETAL.CH", ["lionel@balzmetalsa.onmicrosoft.com"], True),
        ("andre@balzmetalsa.onmicrosoft.com", ["lionel@balzmetal.ch"], True),
        # A colleague whose SMTP address Outlook could not resolve.
        (EXCHANGE_NAME, ["lionel@balzmetal.ch"], True),
        # One external address, even in copy, makes the mail an external exchange.
        ("andre@balzmetal.ch", ["lionel@balzmetal.ch", "vente@aciers.ch"], False),
        ("vente@aciers.ch", ["lionel@balzmetal.ch"], False),
        ("andre@notbalzmetal.ch", ["lionel@balzmetal.ch"], False),
        # Nothing proves that an unresolved recipient is a colleague.
        ("andre@balzmetal.ch", [EXCHANGE_NAME], False),
        ("andre@balzmetal.ch", ["Jean Dupont"], False),
        ("", ["lionel@balzmetal.ch"], False),
    ],
)
def test_internal_mail_is_detected_on_its_addresses(
    sender: str, recipients: list[str], expected: bool,
) -> None:
    assert is_internal_mail(sender, recipients) is expected


def test_internal_mail_has_no_external_counterparty_even_when_its_body_quotes_one() -> None:
    mail = make_mail(body_excerpt="Voir la demande de chef@archi.ch ci-dessous.")

    counterparty = resolve_counterparty(mail, Directory())

    assert primary_external_email(mail) is None
    assert counterparty.role == InterlocutorType.INTERNE
    assert counterparty.organization_name == "Balzmetal"
    assert counterparty.source == "internal_mail"


def test_internal_folder_name_does_not_depend_on_the_address_used() -> None:
    for direction, sender, recipients in (
        (Direction.RECEIVED, "andre@balzmetal.ch", ["lionel@balzmetal.ch"]),
        (Direction.SENT, "lionel@balzmetal.ch", ["andre@balzmetal.ch"]),
        (Direction.RECEIVED, "andre@balzmetalsa.onmicrosoft.com", ["lionel@balzmetal.ch"]),
        (Direction.RECEIVED, EXCHANGE_NAME, ["lionel@balzmetal.ch"]),
    ):
        assert company_from_mail(direction, "Andre", sender, recipients) == "Balzmetal"
        assert company_key_from_mail(direction, "Andre", sender, recipients) == "balzmetal"


# --- routing -------------------------------------------------------------------------


def test_internal_mail_is_filed_in_correspondance_balzmetal_without_any_engine_call(
    tmp_path: Path,
) -> None:
    classifier = CountingClassifier()
    pipeline = make_pipeline(tmp_path, classifier)
    received = make_mail()
    sent = make_mail(
        entry_id="ENTRY-2", direction=Direction.SENT,
        sender_email="lionel@balzmetal.ch", recipients=["andre@balzmetal.ch"],
    )

    rows = pipeline.preview([received, sent])

    assert classifier.calls == 0
    for row in rows:
        assert row.action == PreviewAction.ARCHIVE
        assert row.decision.target_relative_folder == INTERNAL_FOLDER
        assert row.decision.target_path == tmp_path / "2025" / PROJECT / "Correspondance" / (
            "Balzmetal"
        )
        assert row.decision.interlocutor == InterlocutorType.INTERNE
        assert row.decision.mail_type == MailType.CORRESPONDANCE_GENERALE
        assert row.decision.confidence == 1.0
        assert "Mail interne" in row.decision.reason
        assert row.classification.ai is None


def test_internal_mail_is_filed_even_when_the_engine_is_disabled(tmp_path: Path) -> None:
    pipeline = make_pipeline(tmp_path, None, ai_mode=AiMode.DISABLED)

    row = pipeline.preview([make_mail()])[0]

    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.target_relative_folder == INTERNAL_FOLDER


def test_mail_with_an_external_address_in_copy_still_goes_to_the_engine(tmp_path: Path) -> None:
    classifier = CountingClassifier()
    pipeline = make_pipeline(tmp_path, classifier)
    mail = make_mail(recipients=["lionel@balzmetal.ch", "chef@archi.ch"])

    row = pipeline.preview([mail])[0]

    assert classifier.calls == 1
    assert row.decision.interlocutor == InterlocutorType.CLIENT
    assert row.decision.target_relative_folder == "Correspondance/Archi Sarl"


def test_internal_mail_keeps_the_usual_safety_checks(tmp_path: Path) -> None:
    without_folder = make_pipeline(tmp_path / "vide", None, with_project_folder=False)
    with_folder = make_pipeline(tmp_path, None)

    missing = without_folder.preview([make_mail()])[0]
    archived = with_folder.preview([make_mail(categories=[ARCHIVED_CATEGORY])])[0]

    assert missing.action == PreviewAction.REVIEW
    assert "Dossier projet local absent" in missing.decision.reason
    assert archived.action == PreviewAction.ARCHIVED
    assert archived.decision.archive is False


def test_company_declared_internal_in_the_directory_is_filed_like_a_colleague(
    tmp_path: Path,
) -> None:
    classifier = CountingClassifier()
    pipeline = make_pipeline(tmp_path, classifier)
    mail = make_mail(sender_name="Direction", sender_email="direction@balz-holding.ch")

    row = pipeline.preview([mail])[0]

    assert classifier.calls == 0
    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.target_relative_folder == INTERNAL_FOLDER


def test_internal_role_saved_in_directory_files_the_mails_already_displayed(
    tmp_path: Path,
) -> None:
    class EmptyDirectory(Directory):
        roles: dict[str, InterlocutorType] = {}

    (tmp_path / "2025" / PROJECT).mkdir(parents=True)
    classifier = CountingClassifier()
    pipeline = ClassificationPipeline(
        projects_root=tmp_path, ai_mode=AiMode.ALL, ai_classifier=classifier,
        organization_directory=EmptyDirectory(),
    )
    mail = make_mail(sender_name="Direction", sender_email="direction@balz-holding.ch")
    rows = pipeline.preview([mail])
    assert rows[0].action == PreviewAction.REVIEW

    updated = apply_directory_roles_to_rows(rows, cast(Any, Directory()), tmp_path)

    assert classifier.calls == 1
    assert updated[0].action == PreviewAction.ARCHIVE
    assert updated[0].decision.interlocutor == InterlocutorType.INTERNE
    assert updated[0].decision.target_relative_folder == INTERNAL_FOLDER
    assert updated[0].classification.ai is None


# --- manual choice and archives ------------------------------------------------------


def test_manual_choice_interne_files_the_mail_in_correspondance_balzmetal(
    tmp_path: Path,
) -> None:
    (tmp_path / "2025" / PROJECT).mkdir(parents=True)
    # A colleague writing from a private address: only the user can tell.
    mail = make_mail(sender_name="Andre", sender_email="andre@bluewin.ch")
    row = make_pipeline(tmp_path, CountingClassifier()).preview([mail])[0]
    assert row.action == PreviewAction.REVIEW

    updated, signal = apply_manual_classification(
        row,
        ManualClassificationUpdate(
            mail_type=MailType.COMMANDE,
            interlocutor=InterlocutorType.INTERNE,
            target_relative_folder="A verifier",
        ),
        projects_root=tmp_path,
    )

    assert suggested_manual_destination(MailType.COMMANDE, InterlocutorType.INTERNE) == (
        "Correspondance"
    )
    assert updated.action == PreviewAction.ARCHIVE
    assert updated.decision.mail_type == MailType.CORRESPONDANCE_GENERALE
    assert updated.decision.target_relative_folder == INTERNAL_FOLDER
    assert signal.organization_name == "Balzmetal"


def test_mail_archived_in_the_internal_folder_is_shown_as_internal(tmp_path: Path) -> None:
    mail = make_mail()
    record = ArchivedMailRecord(
        outlook_entry_id=mail.entry_id,
        project_number=PROJECT,
        sent_at=mail.sent_at,
        msg_path=tmp_path / "2025" / PROJECT / "Correspondance" / "Balzmetal" / "mail.msg",
        target_folder=INTERNAL_FOLDER,
        classification=MailType.CORRESPONDANCE_GENERALE,
        confidence=1.0,
        archived_at=datetime(2026, 5, 7, 8, 0),
    )

    class Journal:
        def is_archived(self, outlook_entry_id: str) -> bool:
            return outlook_entry_id == record.outlook_entry_id

        def get_archived_record(self, outlook_entry_id: str) -> ArchivedMailRecord | None:
            return record if outlook_entry_id == record.outlook_entry_id else None

    pipeline = ClassificationPipeline(projects_root=tmp_path, archive_state=Journal())

    row = pipeline.preview([mail])[0]

    assert row.action == PreviewAction.ARCHIVED
    assert row.decision.interlocutor == InterlocutorType.INTERNE
    assert row.decision.target_relative_folder == INTERNAL_FOLDER
