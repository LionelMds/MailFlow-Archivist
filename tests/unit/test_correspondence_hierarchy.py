from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.core.correspondence_hierarchy import (
    CORRESPONDENCE_FOLDER,
    SUPPLIER_ORDER_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
    apply_correspondence_hierarchy,
    company_from_mail,
    is_safe_relative_folder,
)
from mailflow.models import (
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)


def make_row(
    tmp_path: Path,
    *,
    entry_id: str,
    sent_at: datetime,
    mail_type: MailType,
    interlocutor: InterlocutorType,
    direction: Direction = Direction.RECEIVED,
    sender_name: str = "Metal Factory",
    sender_email: str = "sales@metal-factory.ch",
    recipients: list[str] | None = None,
    target_relative_folder: str = CORRESPONDENCE_FOLDER,
) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=direction,
        subject=mail_type.value,
        sender_name=sender_name,
        sender_email=sender_email,
        recipients=recipients or ["lionel@balzmetal.ch"],
        sent_at=sent_at,
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=True,
        requires_review=False,
        mail_type=mail_type,
        interlocutor=interlocutor,
        target_relative_folder=target_relative_folder,
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="ok",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=mail_type,
                suggested_interlocutor=interlocutor,
                likely_archive=True,
                confidence=0.9,
                matched_rules=[mail_type.value],
            )
        ),
        decision=decision,
        action=PreviewAction.ARCHIVE,
    )


def test_company_from_mail_uses_received_sender_name() -> None:
    assert (
        company_from_mail(
            Direction.RECEIVED,
            "Lorenzo D'Angelo (HANS KOHLER AG)",
            "l.dangelo@kohler.ch",
            ["lionel@balzmetal.ch"],
        )
        == "HANS KOHLER AG"
    )


def test_company_from_mail_uses_external_recipient_for_sent_mail() -> None:
    assert (
        company_from_mail(
            Direction.SENT,
            "Lionel",
            "lionel@balzmetal.ch",
            ["bureau@balzmetal.ch", "sales@metal-factory.ch"],
        )
        == "Metal Factory"
    )


def test_client_rows_are_grouped_in_company_approval_folder(tmp_path: Path) -> None:
    row = make_row(
        tmp_path,
        entry_id="CLIENT-1",
        sent_at=datetime(2026, 5, 1, 9),
        mail_type=MailType.ADMINISTRATIF,
        interlocutor=InterlocutorType.CLIENT,
        sender_name="AIG",
        sender_email="chef@aig.ch",
    )

    updated = apply_correspondence_hierarchy([row], projects_root=tmp_path)[0]

    assert updated.decision.target_relative_folder == "Correspondance/AIG"
    assert updated.decision.target_path == (
        tmp_path / "2025" / "2025-4893" / "Correspondance" / "AIG"
    )


def test_client_rows_with_multiple_people_are_grouped_by_domain(tmp_path: Path) -> None:
    rows = [
        make_row(
            tmp_path,
            entry_id="CLIENT-1",
            sent_at=datetime(2026, 5, 1, 9),
            mail_type=MailType.ADMINISTRATIF,
            interlocutor=InterlocutorType.CLIENT,
            sender_name="Jean Dupont",
            sender_email="jean.dupont@gva.ch",
        ),
        make_row(
            tmp_path,
            entry_id="CLIENT-2",
            sent_at=datetime(2026, 5, 1, 10),
            mail_type=MailType.TECHNIQUE,
            interlocutor=InterlocutorType.CLIENT,
            sender_name="Marie Martin",
            sender_email="marie.martin@gva.ch",
        ),
    ]

    by_id = {
        row.mail.entry_id: row.decision.target_relative_folder
        for row in apply_correspondence_hierarchy(rows, projects_root=tmp_path)
    }

    assert by_id == {
        "CLIENT-1": "Correspondance/GVA",
        "CLIENT-2": "Correspondance/GVA",
    }


def test_supplier_rows_follow_decided_destination_without_timeline_switching(
    tmp_path: Path,
) -> None:
    rows = [
        make_row(
            tmp_path,
            entry_id="RFQ-1",
            sent_at=datetime(2026, 5, 1, 8),
            mail_type=MailType.DEMANDE_DE_PRIX,
            interlocutor=InterlocutorType.FOURNISSEUR,
            direction=Direction.SENT,
            recipients=["sales@metal-factory.ch"],
            target_relative_folder=SUPPLIER_REQUEST_FOLDER,
        ),
        make_row(
            tmp_path,
            entry_id="DISCUSSION-1",
            sent_at=datetime(2026, 5, 2, 8),
            mail_type=MailType.TECHNIQUE,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder=CORRESPONDENCE_FOLDER,
        ),
        make_row(
            tmp_path,
            entry_id="OFFER-1",
            sent_at=datetime(2026, 5, 3, 8),
            mail_type=MailType.DEVIS,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder=SUPPLIER_REQUEST_FOLDER,
        ),
        make_row(
            tmp_path,
            entry_id="ORDER-1",
            sent_at=datetime(2026, 5, 4, 8),
            mail_type=MailType.COMMANDE,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder=SUPPLIER_ORDER_FOLDER,
        ),
        make_row(
            tmp_path,
            entry_id="RFQ-2",
            sent_at=datetime(2026, 5, 5, 8),
            mail_type=MailType.DEMANDE_DE_PRIX,
            interlocutor=InterlocutorType.FOURNISSEUR,
            direction=Direction.SENT,
            recipients=["sales@metal-factory.ch"],
            target_relative_folder=SUPPLIER_REQUEST_FOLDER,
        ),
        make_row(
            tmp_path,
            entry_id="DISCUSSION-2",
            sent_at=datetime(2026, 5, 6, 8),
            mail_type=MailType.TECHNIQUE,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder=CORRESPONDENCE_FOLDER,
        ),
    ]

    by_id = {
        row.mail.entry_id: row.decision.target_relative_folder
        for row in apply_correspondence_hierarchy(rows, projects_root=tmp_path)
    }

    assert by_id["RFQ-1"] == "Fournisseurs/Demande de prix/Metal Factory"
    assert by_id["DISCUSSION-1"] == "Correspondance/Metal Factory"
    assert by_id["OFFER-1"] == "Fournisseurs/Demande de prix/Metal Factory"
    assert by_id["ORDER-1"] == "Fournisseurs/Commande/Metal Factory"
    assert by_id["RFQ-2"] == "Fournisseurs/Demande de prix/Metal Factory"
    assert by_id["DISCUSSION-2"] == "Correspondance/Metal Factory"


def test_supplier_grouping_uses_email_domain_across_sent_and_received(tmp_path: Path) -> None:
    rows = [
        make_row(
            tmp_path,
            entry_id="SENT-RFQ",
            sent_at=datetime(2026, 5, 1, 8),
            mail_type=MailType.DEMANDE_DE_PRIX,
            interlocutor=InterlocutorType.FOURNISSEUR,
            direction=Direction.SENT,
            sender_name="Lionel",
            sender_email="lionel@balzmetal.ch",
            recipients=["l.dangelo@kohler.ch"],
            target_relative_folder=SUPPLIER_REQUEST_FOLDER,
        ),
        make_row(
            tmp_path,
            entry_id="RECEIVED-OFFER",
            sent_at=datetime(2026, 5, 2, 8),
            mail_type=MailType.DEVIS,
            interlocutor=InterlocutorType.FOURNISSEUR,
            sender_name="Lorenzo D'Angelo (HANS KOHLER AG)",
            sender_email="l.dangelo@kohler.ch",
            target_relative_folder=SUPPLIER_REQUEST_FOLDER,
        ),
    ]

    by_id = {
        row.mail.entry_id: row.decision.target_relative_folder
        for row in apply_correspondence_hierarchy(rows, projects_root=tmp_path)
    }

    assert by_id["SENT-RFQ"] == "Fournisseurs/Demande de prix/HANS KOHLER AG"
    assert by_id["RECEIVED-OFFER"] == "Fournisseurs/Demande de prix/HANS KOHLER AG"


def test_supplier_folder_prefers_company_domain_over_contact_person(tmp_path: Path) -> None:
    row = make_row(
        tmp_path,
        entry_id="RECEIVED-OFFER",
        sent_at=datetime(2026, 5, 2, 8),
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        sender_name="Lorenzo D'Angelo",
        sender_email="l.dangelo@kohler.ch",
        target_relative_folder=SUPPLIER_REQUEST_FOLDER,
    )

    updated = apply_correspondence_hierarchy([row], projects_root=tmp_path)[0]

    assert updated.decision.target_relative_folder == (
        "Fournisseurs/Demande de prix/Kohler"
    )


def test_company_resolution_prefers_directory_domain(tmp_path: Path) -> None:
    class Directory:
        def organization_name_for_email(self, email: str) -> str | None:
            return "AIG" if email.endswith("@gva.ch") else None

    row = make_row(
        tmp_path,
        entry_id="CLIENT-GVA",
        sent_at=datetime(2026, 5, 2, 8),
        mail_type=MailType.ADMINISTRATIF,
        interlocutor=InterlocutorType.CLIENT,
        sender_name="Jean Dupont",
        sender_email="jean.dupont@gva.ch",
    )

    updated = apply_correspondence_hierarchy(
        [row],
        projects_root=tmp_path,
        organization_directory=Directory(),
    )[0]

    assert updated.decision.target_relative_folder == "Correspondance/AIG"


def test_safe_relative_folder_rejects_unsafe_paths() -> None:
    assert is_safe_relative_folder("Fournisseurs/Commande/Metal Factory")
    assert is_safe_relative_folder("A verifier")
    assert not is_safe_relative_folder("../Foo")
    assert not is_safe_relative_folder("C:/Foo")
    assert not is_safe_relative_folder("Foo/Bar*")
