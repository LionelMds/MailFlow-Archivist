from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.core.project_digest import build_project_digest
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
    subject: str,
    target_folder: str,
    interlocutor: InterlocutorType,
    mail_type: MailType,
    direction: Direction = Direction.RECEIVED,
    sent_at: datetime = datetime(2026, 5, 6, 10, 30),
    body: str = "",
) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id,
        project_number="2025-4788",
        outlook_folder="Boite de reception/2025/2025-4788",
        direction=direction,
        subject=subject,
        sender_name="AIG" if interlocutor == InterlocutorType.CLIENT else "Metal Factory",
        sender_email="contact@gva.ch"
        if interlocutor == InterlocutorType.CLIENT
        else "sales@metalfactory.test",
        recipients=["lionel@balzmetal.ch"],
        sent_at=sent_at,
        body_excerpt=body,
    )
    decision = ArchiveDecision(
        mail_id=entry_id,
        project_number=mail.project_number,
        archive=True,
        requires_review=False,
        mail_type=mail_type,
        interlocutor=interlocutor,
        target_relative_folder=target_folder,
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="Test.",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=mail_type,
                suggested_interlocutor=interlocutor,
                likely_archive=True,
                confidence=0.9,
                matched_rules=[],
            )
        ),
        decision=decision,
        action=PreviewAction.ARCHIVE,
    )


def test_project_digest_summarizes_clients_suppliers_orders_and_issues(
    tmp_path: Path,
) -> None:
    rows = [
        make_row(
            tmp_path,
            entry_id="CLIENT-1",
            subject="Approbation du plan PIF",
            target_folder="Correspondance/AIG",
            interlocutor=InterlocutorType.CLIENT,
            mail_type=MailType.PLAN,
            sent_at=datetime(2026, 4, 20, 9, 0),
        ),
        make_row(
            tmp_path,
            entry_id="SUPPLIER-1",
            subject="Offre serrurerie",
            target_folder="Fournisseurs/Demande de prix/Metal Factory",
            interlocutor=InterlocutorType.FOURNISSEUR,
            mail_type=MailType.DEVIS,
            sent_at=datetime(2026, 4, 21, 9, 0),
        ),
        make_row(
            tmp_path,
            entry_id="ORDER-1",
            subject="Commande confirmee",
            target_folder="Fournisseurs/Commande/Metal Factory",
            interlocutor=InterlocutorType.FOURNISSEUR,
            mail_type=MailType.COMMANDE,
            sent_at=datetime(2026, 4, 22, 9, 0),
            body="Probleme signale sur le delai de livraison.",
        ),
    ]

    digest = build_project_digest(rows)

    assert digest.project_number == "2025-4788"
    assert digest.mail_count == 3
    assert digest.sent_count == 0
    assert digest.received_count == 3
    assert any("3 mails analyses" in point for point in digest.global_points)
    assert any("AIG: Approbation du plan PIF" in point for point in digest.client_points)
    assert any("Metal Factory" in point for point in digest.supplier_points)
    assert any("Metal Factory: Commande confirmee" in point for point in digest.order_points)
    assert any("Probleme" in point for point in digest.issue_points)
