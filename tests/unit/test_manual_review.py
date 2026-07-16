from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mailflow.core.manual_review import apply_manual_classification, suggested_manual_destination
from mailflow.models import (
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualClassificationUpdate,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)


class Directory:
    def organization_name_for_email(self, email: str) -> str | None:
        return "AIG" if email.endswith("@gva.ch") else "Metal Factory"


def make_row(tmp_path: Path, *, email: str = "contact@gva.ch") -> PreviewRow:
    mail = MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Inbox/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Echange projet",
        sender_name="Contact",
        sender_email=email,
        recipients=["lionel@balzmetal.ch"],
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=False,
        requires_review=True,
        mail_type=MailType.A_VERIFIER,
        interlocutor=InterlocutorType.INCONNU,
        target_relative_folder="A verifier",
        target_path=tmp_path,
        confidence=0.4,
        duplicate_status="none",
        reason="A verifier.",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(rule=RuleClassification(confidence=0.0)),
        decision=decision,
        action=PreviewAction.REVIEW,
    )


def test_manual_client_correction_enforces_correspondence_and_company(tmp_path: Path) -> None:
    row, signal = apply_manual_classification(
        make_row(tmp_path),
        ManualClassificationUpdate(
            mail_type=MailType.COMMANDE,
            interlocutor=InterlocutorType.CLIENT,
            target_relative_folder="Fournisseurs/Commande",
        ),
        projects_root=tmp_path,
        organization_directory=Directory(),
    )

    assert row.decision.mail_type == MailType.CORRESPONDANCE_GENERALE
    assert row.decision.target_relative_folder == "Correspondance/AIG"
    assert signal.organization_name == "AIG"
    assert signal.primary_email == "contact@gva.ch"
    assert signal.learning_term is None
    assert signal.misleading_term is None


@pytest.mark.parametrize(
    ("mail_type", "expected"),
    [
        (MailType.DEMANDE_DE_PRIX, "Fournisseurs/Demande de prix/Metal Factory"),
        (MailType.COMMANDE, "Fournisseurs/Commande/Metal Factory"),
    ],
)
def test_manual_supplier_correction_has_only_two_destinations(
    tmp_path: Path,
    mail_type: MailType,
    expected: str,
) -> None:
    row, _signal = apply_manual_classification(
        make_row(tmp_path, email="sales@metal.test"),
        ManualClassificationUpdate(
            mail_type=mail_type,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder="Correspondance",
        ),
        projects_root=tmp_path,
        organization_directory=Directory(),
    )

    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.target_relative_folder == expected


def test_manual_supplier_role_is_kept_when_business_category_is_missing(
    tmp_path: Path,
) -> None:
    row, signal = apply_manual_classification(
        make_row(tmp_path, email="sales@metal.test"),
        ManualClassificationUpdate(
            mail_type=MailType.CORRESPONDANCE_GENERALE,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder="Correspondance",
        ),
        projects_root=tmp_path,
        organization_directory=Directory(),
    )

    assert row.action == PreviewAction.REVIEW
    assert row.decision.interlocutor == InterlocutorType.FOURNISSEUR
    assert row.decision.mail_type == MailType.A_VERIFIER
    assert row.decision.target_relative_folder == "A verifier"
    assert signal.selected_interlocutor == InterlocutorType.FOURNISSEUR


def test_unknown_role_remains_in_review(tmp_path: Path) -> None:
    row, _signal = apply_manual_classification(
        make_row(tmp_path),
        ManualClassificationUpdate(
            mail_type=MailType.CORRESPONDANCE_GENERALE,
            interlocutor=InterlocutorType.INCONNU,
            target_relative_folder="A verifier",
        ),
        projects_root=tmp_path,
        organization_directory=Directory(),
    )

    assert row.action == PreviewAction.REVIEW
    assert row.decision.requires_review


def test_manual_destination_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalide"):
        apply_manual_classification(
            make_row(tmp_path),
            ManualClassificationUpdate(
                mail_type=MailType.CORRESPONDANCE_GENERALE,
                interlocutor=InterlocutorType.INCONNU,
                target_relative_folder="../ailleurs",
            ),
            projects_root=tmp_path,
        )


def test_suggested_destination_follows_minimal_business_model() -> None:
    assert suggested_manual_destination(
        MailType.CORRESPONDANCE_GENERALE,
        InterlocutorType.CLIENT,
    ) == "Correspondance"
    assert suggested_manual_destination(
        MailType.DEMANDE_DE_PRIX,
        InterlocutorType.FOURNISSEUR,
    ) == "Fournisseurs/Demande de prix"
    assert suggested_manual_destination(
        MailType.COMMANDE,
        InterlocutorType.FOURNISSEUR,
    ) == "Fournisseurs/Commande"
