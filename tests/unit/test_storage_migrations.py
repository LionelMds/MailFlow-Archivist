from __future__ import annotations

import sqlite3
from pathlib import Path

from mailflow.storage.connection import database_connection
from mailflow.storage.directory_store import SQLiteDirectoryStore
from mailflow.storage.learning_store import SQLiteLearningStore
from mailflow.storage.migrations import apply_migrations, schema_version, script
from mailflow.storage.sqlite_store import SQLiteArchiveStore


def versions(path: Path) -> dict[str, int]:
    with database_connection(path) as connection:
        rows = connection.execute("SELECT component, version FROM schema_versions").fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def test_stores_share_one_file_and_record_their_versions(tmp_path: Path) -> None:
    path = tmp_path / "mailflow.sqlite"

    SQLiteArchiveStore(path).initialize()
    SQLiteDirectoryStore(path).initialize()
    SQLiteLearningStore(path).initialize()

    assert versions(path) == {"archive": 1, "directory": 2, "learning": 2}


def test_migrations_run_once_in_order(tmp_path: Path) -> None:
    calls: list[int] = []
    migrations = [lambda _c: calls.append(1), lambda _c: calls.append(2)]

    with database_connection(tmp_path / "db.sqlite") as connection:
        apply_migrations(connection, "demo", migrations[:1])
        apply_migrations(connection, "demo", migrations)
        apply_migrations(connection, "demo", migrations)
        assert schema_version(connection, "demo") == 2

    assert calls == [1, 2]


def test_newer_database_is_left_untouched_after_downgrade(tmp_path: Path) -> None:
    with database_connection(tmp_path / "db.sqlite") as connection:
        apply_migrations(connection, "demo", [script("CREATE TABLE IF NOT EXISTS a(x)")] * 3)
        apply_migrations(connection, "demo", [script("CREATE TABLE b(x)")])

        assert schema_version(connection, "demo") == 3
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert "b" not in tables


def test_learning_database_created_before_versioning_is_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE manual_learning_signals(
          id INTEGER PRIMARY KEY,
          mail_id TEXT NOT NULL,
          project_number TEXT NOT NULL,
          subject TEXT NOT NULL,
          selected_mail_type TEXT NOT NULL,
          selected_interlocutor TEXT NOT NULL,
          selected_target_folder TEXT NOT NULL,
          learning_term TEXT,
          manual_required INTEGER NOT NULL,
          created_at TEXT NOT NULL
        );
        """
    )
    legacy.close()

    store = SQLiteLearningStore(path)
    store.initialize()

    with database_connection(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(manual_learning_signals)")
        }
    assert "misleading_term" in columns
    assert versions(path)["learning"] == 2
    assert store.count() == 0
