from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mailflow.storage.connection import database_connection
from mailflow.storage.sqlite_store import SQLiteArchiveStore


def test_database_connection_commits_and_releases_connection(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"
    with database_connection(path) as connection:
        connection.execute("CREATE TABLE records (value INTEGER)")
        connection.execute("INSERT INTO records VALUES (1)")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with database_connection(path) as reopened:
        assert reopened.execute("SELECT value FROM records").fetchall() == [(1,)]


def test_database_connection_rolls_back_failed_work_and_releases_connection(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"
    with database_connection(path) as setup:
        setup.execute("CREATE TABLE records (value INTEGER)")
    with pytest.raises(RuntimeError), database_connection(path) as connection:
        connection.execute("INSERT INTO records VALUES (1)")
        raise RuntimeError("failure")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with database_connection(path) as reopened:
        assert reopened.execute("SELECT value FROM records").fetchall() == []


def test_archive_lookup_initializes_new_database(tmp_path: Path) -> None:
    store = SQLiteArchiveStore(tmp_path / "nested" / "store.sqlite")
    assert not store.is_archived("MISSING")
