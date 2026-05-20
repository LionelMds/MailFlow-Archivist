from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.models import ArchivedMailRecord, MailType
from mailflow.storage.sqlite_store import SQLiteArchiveStore


def record(tmp_path: Path) -> ArchivedMailRecord:
    return ArchivedMailRecord(
        outlook_entry_id="ENTRY-1",
        conversation_id="CONV-1",
        internet_message_id="<id@test>",
        project_number="2025-4893",
        subject="Offre",
        sender="dupont@example.com",
        sent_at=datetime(2026, 5, 6, 10, 30),
        msg_path=tmp_path / "mail.msg",
        target_folder="Correspondance",
        classification=MailType.DEVIS,
        confidence=0.9,
        archived_at=datetime(2026, 5, 6, 11, 0),
    )


def test_sqlite_store_initializes_and_records(tmp_path: Path) -> None:
    store = SQLiteArchiveStore(tmp_path / "mailflow_archivist.sqlite")

    assert store.record_archived(record(tmp_path)) is True
    assert store.is_archived("ENTRY-1")
    assert store.count_archived() == 1


def test_sqlite_store_is_idempotent(tmp_path: Path) -> None:
    store = SQLiteArchiveStore(tmp_path / "mailflow_archivist.sqlite")

    assert store.record_archived(record(tmp_path)) is True
    assert store.record_archived(record(tmp_path)) is False
    assert store.count_archived() == 1

