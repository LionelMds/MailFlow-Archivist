from __future__ import annotations

import sqlite3
from pathlib import Path

from mailflow.core.manual_review import LearnedClassificationRule, LearnedMisleadingTerm
from mailflow.models import InterlocutorType, MailType, ManualLearningSignal

LEARNING_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_learning_signals(
  id INTEGER PRIMARY KEY,
  mail_id TEXT NOT NULL,
  project_number TEXT NOT NULL,
  subject TEXT NOT NULL,
  selected_mail_type TEXT NOT NULL,
  selected_interlocutor TEXT NOT NULL,
  selected_target_folder TEXT NOT NULL,
  learning_term TEXT,
  misleading_term TEXT,
  manual_required INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_project
  ON manual_learning_signals(project_number);
CREATE INDEX IF NOT EXISTS idx_learning_term
  ON manual_learning_signals(learning_term);
"""

LEARNING_MIGRATIONS = """
CREATE INDEX IF NOT EXISTS idx_learning_misleading_term
  ON manual_learning_signals(misleading_term);
"""


class SQLiteLearningStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(LEARNING_SCHEMA)
            _ensure_column(connection, "manual_learning_signals", "misleading_term", "TEXT")
            connection.executescript(LEARNING_MIGRATIONS)

    def record(self, signal: ManualLearningSignal) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manual_learning_signals(
                    mail_id,
                    project_number,
                    subject,
                    selected_mail_type,
                    selected_interlocutor,
                    selected_target_folder,
                    learning_term,
                    misleading_term,
                    manual_required,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.mail_id,
                    signal.project_number,
                    signal.subject,
                    signal.selected_mail_type.value,
                    signal.selected_interlocutor.value,
                    signal.selected_target_folder,
                    signal.learning_term,
                    signal.misleading_term,
                    int(signal.manual_required),
                    signal.created_at.isoformat(),
                ),
            )

    def count(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM manual_learning_signals").fetchone()
        return int(row[0])

    def learned_rules(self) -> list[LearnedClassificationRule]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    learning_term,
                    selected_mail_type,
                    selected_interlocutor,
                    selected_target_folder
                FROM manual_learning_signals
                WHERE manual_required = 0
                  AND learning_term IS NOT NULL
                  AND TRIM(learning_term) != ''
                ORDER BY id DESC
                """
            ).fetchall()
        return [
            LearnedClassificationRule(
                term=str(row[0]),
                mail_type=MailType(str(row[1])),
                interlocutor=InterlocutorType(str(row[2])),
                target_relative_folder=str(row[3]),
            )
            for row in rows
        ]

    def misleading_terms(self) -> list[LearnedMisleadingTerm]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT misleading_term
                FROM manual_learning_signals
                WHERE misleading_term IS NOT NULL
                  AND TRIM(misleading_term) != ''
                ORDER BY id DESC
                """
            ).fetchall()
        return [LearnedMisleadingTerm(term=str(row[0])) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
