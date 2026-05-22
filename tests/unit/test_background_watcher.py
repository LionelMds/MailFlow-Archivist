from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.core.background_watcher import ReviewQueue, WatchState, review_entry_ids
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
    entry_id: str,
    tmp_path: Path,
    action: PreviewAction = PreviewAction.ARCHIVE,
) -> PreviewRow:
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
        archive=action == PreviewAction.ARCHIVE,
        requires_review=action == PreviewAction.REVIEW,
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
        action=action,
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


def test_review_queue_tracks_pending_review_rows(tmp_path: Path) -> None:
    ready = make_row("READY", tmp_path)
    review = make_row("REVIEW", tmp_path, PreviewAction.REVIEW)
    queue = ReviewQueue()

    assert review_entry_ids([ready, review]) == {"REVIEW"}
    assert queue.sync([ready, review]) == 1
    assert queue.count == 1
    assert queue.sync([ready]) == 0
    assert queue.count == 0
