"""Numbered schema migrations, tracked per store in the shared SQLite file.

Each store owns an ordered list of migrations; its version is the number already
applied. Append new migrations at the end and never edit a released one. Migrations
must stay idempotent: `executescript` commits on its own, so an interrupted upgrade
can replay the step that was running.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence

Migration = Callable[[sqlite3.Connection], None]

VERSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_versions(
  component TEXT PRIMARY KEY,
  version INTEGER NOT NULL
);
"""


def script(sql: str) -> Migration:
    def run(connection: sqlite3.Connection) -> None:
        connection.executescript(sql)

    return run


def schema_version(connection: sqlite3.Connection, component: str) -> int:
    connection.executescript(VERSIONS_SCHEMA)
    row = connection.execute(
        "SELECT version FROM schema_versions WHERE component = ?",
        (component,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def apply_migrations(
    connection: sqlite3.Connection,
    component: str,
    migrations: Sequence[Migration],
) -> None:
    current = schema_version(connection, component)
    # A newer database (after a downgrade) is left untouched: migrations only add.
    for version in range(current + 1, len(migrations) + 1):
        migrations[version - 1](connection)
        connection.execute(
            """
            INSERT INTO schema_versions(component, version) VALUES (?, ?)
            ON CONFLICT(component) DO UPDATE SET version = excluded.version
            """,
            (component, version),
        )
        connection.commit()
