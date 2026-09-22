from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mailflow.core.archive_batch import ArchiveBatchResult
from mailflow.core.archive_service import ArchiveService
from mailflow.models import (
    ArchiveDecision,
    ArchivedMailRecord,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
)
from mailflow.outlook.exporter import OutlookExporter
from mailflow.storage.sqlite_store import SQLiteArchiveStore


class FakeMail:
    Attachments: list[object] = []
    Categories = ""

    def SaveAs(self, path: str, _format: int) -> None:
        Path(path).write_bytes(b"complete mail")

    def Save(self) -> None:
        raise RuntimeError("read-only shared mailbox")


def make_metadata() -> MailMetadata:
    return MailMetadata(
        entry_id="MAIL-1", project_number="2025-1234", outlook_folder="Inbox",
        direction=Direction.RECEIVED, subject="A message", sender_name="Sender",
        sent_at=datetime(2026, 1, 1),
    )


def make_decision(path: Path) -> ArchiveDecision:
    return ArchiveDecision(
        mail_id="MAIL-1", project_number="2025-1234", archive=True, requires_review=False,
        mail_type=MailType.CORRESPONDANCE_GENERALE, interlocutor=InterlocutorType.CLIENT,
        target_relative_folder="Correspondance", target_path=path, confidence=1.0,
        duplicate_status="none", reason="reviewed",
    )


def test_outlook_mark_failure_keeps_successful_archive_and_reports_warning(tmp_path: Path) -> None:
    store = SQLiteArchiveStore(tmp_path / "archive.sqlite")
    service = ArchiveService(exporter=OutlookExporter(), store=store)

    result = service.archive(FakeMail(), make_metadata(), make_decision(tmp_path))

    assert store.is_archived("MAIL-1")
    assert result.msg_path.read_bytes() == b"complete mail"
    assert len(result.warnings) == 1
    assert "categorie Outlook" in result.warnings[0]
    assert ArchiveBatchResult(exported=[result]).warnings == result.warnings


def test_failed_database_record_does_not_mark_outlook_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteArchiveStore(tmp_path / "archive.sqlite")
    item = FakeMail()

    def fail(_record: ArchivedMailRecord) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(store, "record_archived", fail)
    service = ArchiveService(exporter=OutlookExporter(), store=store)

    with pytest.raises(OSError):
        service.archive(item, make_metadata(), make_decision(tmp_path))

    assert item.Categories == ""
