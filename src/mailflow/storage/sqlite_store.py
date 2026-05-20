from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from mailflow.models import ArchivedMailRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS archived_mails(
  id INTEGER PRIMARY KEY,
  outlook_entry_id TEXT NOT NULL,
  conversation_id TEXT,
  internet_message_id TEXT,
  project_number TEXT NOT NULL,
  subject TEXT,
  sender TEXT,
  sent_at TEXT,
  msg_path TEXT NOT NULL,
  target_folder TEXT NOT NULL,
  classification TEXT NOT NULL,
  confidence REAL NOT NULL,
  archived_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_archived_entry_id
  ON archived_mails(outlook_entry_id);
CREATE INDEX IF NOT EXISTS idx_archived_project
  ON archived_mails(project_number);
"""


class SQLiteArchiveStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def is_archived(self, outlook_entry_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM archived_mails WHERE outlook_entry_id = ? LIMIT 1",
                (outlook_entry_id,),
            ).fetchone()
        return row is not None

    def record_archived(self, record: ArchivedMailRecord) -> bool:
        self.initialize()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO archived_mails(
                    outlook_entry_id,
                    conversation_id,
                    internet_message_id,
                    project_number,
                    subject,
                    sender,
                    sent_at,
                    msg_path,
                    target_folder,
                    classification,
                    confidence,
                    archived_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.outlook_entry_id,
                    record.conversation_id,
                    record.internet_message_id,
                    record.project_number,
                    record.subject,
                    record.sender,
                    record.sent_at.isoformat(),
                    str(record.msg_path),
                    record.target_folder,
                    record.classification.value,
                    record.confidence,
                    record.archived_at.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def count_archived(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM archived_mails").fetchone()
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


def utc_now_naive() -> datetime:
    return datetime.utcnow()

