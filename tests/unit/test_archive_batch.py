from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.core.archive_batch import ArchiveBatchExecutor, ArchiveCandidate
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
from mailflow.outlook.exporter import ExportResult


class FakeArchiveService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.orders: dict[str, int | None] = {}

    def archive(
        self,
        item: object,
        metadata: MailMetadata,
        decision: ArchiveDecision,
    ) -> ExportResult:
        self.calls.append(metadata.entry_id)
        self.orders[metadata.entry_id] = metadata.archive_order
        if self.fail:
            raise RuntimeError("boom")
        return ExportResult(
            msg_path=decision.target_path / f"{metadata.entry_id}.msg",
            attachment_paths=[],
        )


def make_row(
    tmp_path: Path,
    entry_id: str,
    action: PreviewAction,
    *,
    archive: bool,
) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id,
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


def test_archive_batch_exports_only_ready_rows(tmp_path: Path) -> None:
    service = FakeArchiveService()
    archive_row = make_row(tmp_path, "ENTRY-1", PreviewAction.ARCHIVE, archive=True)
    ignored_row = make_row(tmp_path, "ENTRY-2", PreviewAction.IGNORE, archive=False)

    result = ArchiveBatchExecutor(service).archive(
        [
            ArchiveCandidate(item=object(), row=archive_row),
            ArchiveCandidate(item=object(), row=ignored_row),
        ]
    )

    assert service.calls == ["ENTRY-1"]
    assert service.orders == {"ENTRY-1": 1}
    assert result.exported_count == 1
    assert result.exported_mail_ids == ["ENTRY-1"]
    assert result.skipped == ["ENTRY-2"]
    assert result.failure_count == 0


def test_archive_batch_skips_review_rows_by_default(tmp_path: Path) -> None:
    service = FakeArchiveService()
    review_row = make_row(tmp_path, "ENTRY-1", PreviewAction.REVIEW, archive=True)

    result = ArchiveBatchExecutor(service).archive([ArchiveCandidate(object(), review_row)])

    assert service.calls == []
    assert result.skipped == ["ENTRY-1"]


def test_archive_batch_can_include_confirmed_review_rows(tmp_path: Path) -> None:
    service = FakeArchiveService()
    review_row = make_row(tmp_path, "ENTRY-1", PreviewAction.REVIEW, archive=True)

    result = ArchiveBatchExecutor(service).archive(
        [ArchiveCandidate(object(), review_row)],
        include_review=True,
    )

    assert service.calls == ["ENTRY-1"]
    assert result.exported_count == 1


def test_archive_batch_records_failures(tmp_path: Path) -> None:
    service = FakeArchiveService(fail=True)
    row = make_row(tmp_path, "ENTRY-1", PreviewAction.ARCHIVE, archive=True)

    result = ArchiveBatchExecutor(service).archive([ArchiveCandidate(object(), row)])

    assert result.exported_count == 0
    assert result.failure_count == 1
    assert result.failures[0].mail_id == "ENTRY-1"


def test_archive_batch_numbers_ready_rows_chronologically(tmp_path: Path) -> None:
    service = FakeArchiveService()
    first = make_row(tmp_path, "ENTRY-1", PreviewAction.ARCHIVE, archive=True)
    second = make_row(tmp_path, "ENTRY-2", PreviewAction.ARCHIVE, archive=True)
    first = first.model_copy(
        update={"mail": first.mail.model_copy(update={"sent_at": datetime(2026, 5, 7)})}
    )
    second = second.model_copy(
        update={"mail": second.mail.model_copy(update={"sent_at": datetime(2026, 5, 6)})}
    )

    ArchiveBatchExecutor(service).archive(
        [
            ArchiveCandidate(object(), first),
            ArchiveCandidate(object(), second),
        ]
    )

    assert service.orders == {"ENTRY-1": 2, "ENTRY-2": 1}
