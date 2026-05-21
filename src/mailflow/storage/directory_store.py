from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from mailflow.core.contact_directory import ContactObservation, DirectoryUpsertOutcome

DIRECTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  default_role TEXT NOT NULL DEFAULT 'inconnu',
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS organization_domains(
  id INTEGER PRIMARY KEY,
  organization_id INTEGER NOT NULL,
  domain TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  confidence REAL NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(organization_id) REFERENCES organizations(id)
);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT,
  organization_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(organization_id) REFERENCES organizations(id)
);
CREATE TABLE IF NOT EXISTS project_participants(
  id INTEGER PRIMARY KEY,
  project_number TEXT NOT NULL,
  organization_id INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'inconnu',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 1,
  UNIQUE(project_number, organization_id),
  FOREIGN KEY(organization_id) REFERENCES organizations(id)
);
CREATE INDEX IF NOT EXISTS idx_organization_domains_org
  ON organization_domains(organization_id);
CREATE INDEX IF NOT EXISTS idx_contacts_org
  ON contacts(organization_id);
CREATE INDEX IF NOT EXISTS idx_project_participants_project
  ON project_participants(project_number);
"""


class SQLiteDirectoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(DIRECTORY_SCHEMA)

    def record_observation(self, observation: ContactObservation) -> DirectoryUpsertOutcome:
        self.initialize()
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            organization_id, new_organization = self._resolve_organization(
                connection,
                observation,
                now,
            )
            new_domain = False
            if observation.allow_domain_mapping:
                new_domain = self._upsert_domain(connection, organization_id, observation, now)
            new_contact = self._upsert_contact(connection, organization_id, observation, now)
            new_participant = self._upsert_project_participant(
                connection,
                organization_id,
                observation.project_number,
                now,
            )
        return DirectoryUpsertOutcome(
            new_organization=new_organization,
            new_domain=new_domain,
            new_contact=new_contact,
            new_project_participant=new_participant,
        )

    def organization_name_for_email(self, email: str) -> str | None:
        normalized_email = email.strip().casefold()
        if not normalized_email:
            return None
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT organizations.name
                FROM contacts
                JOIN organizations ON organizations.id = contacts.organization_id
                WHERE contacts.email = ?
                LIMIT 1
                """,
                (normalized_email,),
            ).fetchone()
            if row is not None:
                return str(row[0])
            domain = _domain_from_email(normalized_email)
            if domain is None:
                return None
            row = connection.execute(
                """
                SELECT organizations.name
                FROM organization_domains
                JOIN organizations ON organizations.id = organization_domains.organization_id
                WHERE organization_domains.domain = ?
                LIMIT 1
                """,
                (domain,),
            ).fetchone()
        return None if row is None else str(row[0])

    def count_organizations(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM organizations").fetchone()
        return int(row[0])

    def count_domains(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM organization_domains").fetchone()
        return int(row[0])

    def count_contacts(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM contacts").fetchone()
        return int(row[0])

    def _resolve_organization(
        self,
        connection: sqlite3.Connection,
        observation: ContactObservation,
        now: str,
    ) -> tuple[int, bool]:
        contact_org = _organization_id_for_contact(connection, observation.email)
        if contact_org is not None:
            return contact_org, False
        if observation.allow_domain_mapping:
            domain_org = _organization_id_for_domain(connection, observation.domain)
            if domain_org is not None:
                return domain_org, False
        return _get_or_create_organization(connection, observation.organization_name, now)

    def _upsert_domain(
        self,
        connection: sqlite3.Connection,
        organization_id: int,
        observation: ContactObservation,
        now: str,
    ) -> bool:
        row = connection.execute(
            "SELECT id FROM organization_domains WHERE domain = ?",
            (observation.domain,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO organization_domains(
                    organization_id,
                    domain,
                    source,
                    confidence,
                    first_seen_at,
                    last_seen_at,
                    observation_count
                )
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    organization_id,
                    observation.domain,
                    observation.source,
                    observation.confidence,
                    now,
                    now,
                ),
            )
            return True
        connection.execute(
            """
            UPDATE organization_domains
            SET last_seen_at = ?,
                confidence = MAX(confidence, ?),
                observation_count = observation_count + 1
            WHERE id = ?
            """,
            (now, observation.confidence, int(row[0])),
        )
        return False

    def _upsert_contact(
        self,
        connection: sqlite3.Connection,
        organization_id: int,
        observation: ContactObservation,
        now: str,
    ) -> bool:
        row = connection.execute(
            "SELECT id FROM contacts WHERE email = ?",
            (observation.email,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO contacts(
                    email,
                    display_name,
                    organization_id,
                    source,
                    first_seen_at,
                    last_seen_at,
                    observation_count
                )
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    observation.email,
                    observation.display_name,
                    organization_id,
                    observation.source,
                    now,
                    now,
                ),
            )
            return True
        connection.execute(
            """
            UPDATE contacts
            SET display_name = CASE
                    WHEN TRIM(COALESCE(display_name, '')) = '' THEN ?
                    ELSE display_name
                END,
                last_seen_at = ?,
                observation_count = observation_count + 1
            WHERE id = ?
            """,
            (observation.display_name, now, int(row[0])),
        )
        return False

    def _upsert_project_participant(
        self,
        connection: sqlite3.Connection,
        organization_id: int,
        project_number: str,
        now: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT id
            FROM project_participants
            WHERE project_number = ? AND organization_id = ?
            """,
            (project_number, organization_id),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO project_participants(
                    project_number,
                    organization_id,
                    role,
                    first_seen_at,
                    last_seen_at,
                    observation_count
                )
                VALUES (?, ?, 'inconnu', ?, ?, 1)
                """,
                (project_number, organization_id, now, now),
            )
            return True
        connection.execute(
            """
            UPDATE project_participants
            SET last_seen_at = ?,
                observation_count = observation_count + 1
            WHERE id = ?
            """,
            (now, int(row[0])),
        )
        return False

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


def _organization_id_for_contact(connection: sqlite3.Connection, email: str) -> int | None:
    row = connection.execute(
        "SELECT organization_id FROM contacts WHERE email = ? LIMIT 1",
        (email,),
    ).fetchone()
    return None if row is None else int(row[0])


def _organization_id_for_domain(connection: sqlite3.Connection, domain: str) -> int | None:
    row = connection.execute(
        "SELECT organization_id FROM organization_domains WHERE domain = ? LIMIT 1",
        (domain,),
    ).fetchone()
    return None if row is None else int(row[0])


def _get_or_create_organization(
    connection: sqlite3.Connection,
    name: str,
    now: str,
) -> tuple[int, bool]:
    normalized = _normalize_organization_name(name)
    row = connection.execute(
        "SELECT id FROM organizations WHERE normalized_name = ? LIMIT 1",
        (normalized,),
    ).fetchone()
    if row is not None:
        connection.execute(
            "UPDATE organizations SET updated_at = ? WHERE id = ?",
            (now, int(row[0])),
        )
        return int(row[0]), False
    cursor = connection.execute(
        """
        INSERT INTO organizations(name, normalized_name, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, normalized, now, now),
    )
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        msg = "Impossible de creer l'organisation"
        raise RuntimeError(msg)
    return int(lastrowid), True


def _normalize_organization_name(name: str) -> str:
    words = [word.casefold() for word in re.split(r"\W+", name) if word]
    return "-".join(words) or "inconnu"


def _domain_from_email(email: str) -> str | None:
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip(". ").casefold()
    return domain or None
