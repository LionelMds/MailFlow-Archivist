from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.core.archive_actions import mark_rows_ignored, rows_to_archive
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


def make_row(action: PreviewAction, *, archive: bool, tmp_path: Path) -> PreviewRow:
    mail = MailMetadata(
        entry_id=f"ENTRY-{action.value}",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont",
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=archive,
        requires_review=action == PreviewAction.REVIEW,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Demande de prix",
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="ok",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=MailType.DEVIS,
                suggested_interlocutor=InterlocutorType.FOURNISSEUR,
                likely_archive=archive,
                confidence=0.9,
                matched_rules=["devis"],
            )
        ),
        decision=decision,
        action=action,
    )


def test_rows_to_archive_keeps_only_archive_rows_by_default(tmp_path: Path) -> None:
    rows = [
        make_row(PreviewAction.ARCHIVE, archive=True, tmp_path=tmp_path),
        make_row(PreviewAction.REVIEW, archive=True, tmp_path=tmp_path),
        make_row(PreviewAction.IGNORE, archive=False, tmp_path=tmp_path),
    ]

    selected = rows_to_archive(rows)

    assert [row.action for row in selected] == [PreviewAction.ARCHIVE]


def test_rows_to_archive_can_include_review_after_user_confirmation(tmp_path: Path) -> None:
    rows = [
        make_row(PreviewAction.ARCHIVE, archive=True, tmp_path=tmp_path),
        make_row(PreviewAction.REVIEW, archive=True, tmp_path=tmp_path),
    ]

    selected = rows_to_archive(rows, include_review=True)

    assert [row.action for row in selected] == [PreviewAction.ARCHIVE, PreviewAction.REVIEW]


def test_rows_to_archive_never_archives_false_decisions(tmp_path: Path) -> None:
    rows = [make_row(PreviewAction.ARCHIVE, archive=False, tmp_path=tmp_path)]

    assert rows_to_archive(rows) == []


def test_mark_rows_ignored_returns_copies(tmp_path: Path) -> None:
    rows = [make_row(PreviewAction.ARCHIVE, archive=True, tmp_path=tmp_path)]

    ignored = mark_rows_ignored(rows)

    assert rows[0].action == PreviewAction.ARCHIVE
    assert ignored[0].action == PreviewAction.IGNORE
