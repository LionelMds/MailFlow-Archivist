from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.core.background_watcher import WatchState
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


def make_row(entry_id: str, tmp_path: Path) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    decision = ArchiveDecision(
        mail_id=entry_id,
        project_number=mail.project_number,
        archive=True,
        requires_review=False,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Correspondance",
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="ok",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(confidence=0.9, matched_rules=["devis"])
        ),
        decision=decision,
        action=PreviewAction.ARCHIVE,
    )


def test_watch_state_reports_only_new_entry_ids(tmp_path: Path) -> None:
    first = make_row("ENTRY-1", tmp_path)
    second = make_row("ENTRY-2", tmp_path)
    state = WatchState()

    state.reset([first])
    change = state.update([first, second])

    assert change.new_entry_ids == ["ENTRY-2"]
    assert change.new_count == 1
    assert change.total_count == 2


def test_watch_state_updates_baseline_after_each_scan(tmp_path: Path) -> None:
    first = make_row("ENTRY-1", tmp_path)
    second = make_row("ENTRY-2", tmp_path)
    state = WatchState()

    assert state.update([first]).new_entry_ids == ["ENTRY-1"]
    assert state.update([first, second]).new_entry_ids == ["ENTRY-2"]
    assert state.update([first, second]).new_entry_ids == []
