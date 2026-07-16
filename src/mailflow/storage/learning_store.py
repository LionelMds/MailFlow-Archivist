from __future__ import annotations

import sqlite3
from pathlib import Path

from mailflow.models import (
    InterlocutorType,
    ManualLearningSignal,
    RoutingCategory,
    VerifiedRoutingExample,
    routing_category_for_mail_type,
)

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
CREATE TABLE IF NOT EXISTS verified_routing_examples(
  id INTEGER PRIMARY KEY,
  mail_id TEXT NOT NULL UNIQUE,
  project_number TEXT NOT NULL,
  subject TEXT NOT NULL,
  organization_name TEXT NOT NULL,
  organization_role TEXT NOT NULL,
  category TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verified_routing_project
  ON verified_routing_examples(project_number);
CREATE INDEX IF NOT EXISTS idx_verified_routing_organization
  ON verified_routing_examples(organization_name);
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
            if (
                signal.organization_name
                and signal.selected_interlocutor
                in {InterlocutorType.CLIENT, InterlocutorType.FOURNISSEUR}
            ):
                connection.execute(
                    """
                    INSERT INTO verified_routing_examples(
                        mail_id,
                        project_number,
                        subject,
                        organization_name,
                        organization_role,
                        category,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mail_id) DO UPDATE SET
                        project_number = excluded.project_number,
                        subject = excluded.subject,
                        organization_name = excluded.organization_name,
                        organization_role = excluded.organization_role,
                        category = excluded.category,
                        created_at = excluded.created_at
                    """,
                    (
                        signal.mail_id,
                        signal.project_number,
                        signal.subject,
                        signal.organization_name,
                        signal.selected_interlocutor.value,
                        routing_category_for_mail_type(signal.selected_mail_type).value,
                        signal.created_at.isoformat(),
                    ),
                )

    def count(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM manual_learning_signals").fetchone()
        return int(row[0])

    def verified_examples(self) -> list[VerifiedRoutingExample]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    project_number,
                    subject,
                    organization_name,
                    organization_role,
                    category
                FROM verified_routing_examples
                ORDER BY id DESC
                LIMIT 200
                """
            ).fetchall()
        return [
            VerifiedRoutingExample(
                project_number=str(row[0]),
                subject=str(row[1]),
                organization_name=str(row[2]),
                organization_role=InterlocutorType(str(row[3])),
                category=RoutingCategory(str(row[4])),
            )
            for row in rows
        ]

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
