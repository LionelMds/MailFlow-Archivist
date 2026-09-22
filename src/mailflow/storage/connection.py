from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def database_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Commit or roll back a unit of work and always release its file handle."""
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()
