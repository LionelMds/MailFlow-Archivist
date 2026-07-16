from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from mailflow.core.contact_directory import (
    ContactObservation,
    DirectoryUpsertOutcome,
    OrganizationDirectoryEntry,
    ProjectParticipantEntry,
)
from mailflow.core.correspondence_hierarchy import safe_folder_name
from mailflow.models import InterlocutorType

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

    def organization_id_for_email(self, email: str) -> int | None:
        normalized_email = email.strip().casefold()
        if not normalized_email:
            return None
        self.initialize()
        with self._connect() as connection:
            return _organization_id_for_email(connection, normalized_email)

    def interlocutor_for_email(
        self,
        project_number: str,
        email: str,
    ) -> InterlocutorType | None:
        normalized_email = email.strip().casefold()
        project = project_number.strip()
        if not normalized_email or not project:
            return None
        self.initialize()
        with self._connect() as connection:
            organization_id = _organization_id_for_email(connection, normalized_email)
            if organization_id is None:
                return None
            row = connection.execute(
                """
                SELECT role
                FROM project_participants
                WHERE project_number = ? AND organization_id = ?
                LIMIT 1
                """,
                (project, organization_id),
            ).fetchone()
            if row is not None and str(row[0]) != InterlocutorType.INCONNU.value:
                return InterlocutorType(str(row[0]))
            row = connection.execute(
                """
                SELECT default_role
                FROM organizations
                WHERE id = ?
                LIMIT 1
                """,
                (organization_id,),
            ).fetchone()
        if row is None or str(row[0]) == InterlocutorType.INCONNU.value:
            return None
        return InterlocutorType(str(row[0]))

    def list_organizations(self) -> list[OrganizationDirectoryEntry]:
        self.initialize()
        with self._connect() as connection:
            organization_rows = connection.execute(
                """
                SELECT id, name
                FROM organizations
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
            return [
                OrganizationDirectoryEntry(
                    organization_id=int(row[0]),
                    name=str(row[1]),
                    domains=tuple(_domains_for_organization(connection, int(row[0]))),
                    contacts=tuple(_contacts_for_organization(connection, int(row[0]))),
                    project_count=_project_count_for_organization(connection, int(row[0])),
                )
                for row in organization_rows
            ]

    def list_project_numbers(self) -> list[str]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT project_number
                FROM project_participants
                WHERE TRIM(project_number) <> ''
                ORDER BY project_number DESC
                """
            ).fetchall()
        return [str(row[0]) for row in rows]

    def add_organization(
        self,
        name: str,
        *,
        domain: str | None = None,
        project_number: str | None = None,
        role: InterlocutorType = InterlocutorType.INCONNU,
    ) -> int:
        cleaned_name = safe_folder_name(name.strip())
        if not cleaned_name:
            msg = "Le nom d'entreprise est obligatoire"
            raise ValueError(msg)
        normalized_name = _normalize_organization_name(cleaned_name)
        normalized_domain = _normalize_directory_domain(domain)
        project = "" if project_number is None else project_number.strip()
        if role != InterlocutorType.INCONNU and not project:
            msg = "Un projet est obligatoire pour attribuer un role projet"
            raise ValueError(msg)

        self.initialize()
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM organizations WHERE normalized_name = ? LIMIT 1",
                (normalized_name,),
            ).fetchone()
            if existing is not None:
                msg = (
                    "Une entreprise avec ce nom existe deja. "
                    "Utiliser la fusion pour regrouper les doublons."
                )
                raise ValueError(msg)
            if normalized_domain is not None:
                domain_owner = connection.execute(
                    "SELECT organization_id FROM organization_domains WHERE domain = ? LIMIT 1",
                    (normalized_domain,),
                ).fetchone()
                if domain_owner is not None:
                    msg = f"Le domaine {normalized_domain} appartient deja a une entreprise"
                    raise ValueError(msg)

            cursor = connection.execute(
                """
                INSERT INTO organizations(name, normalized_name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (cleaned_name, normalized_name, now, now),
            )
            lastrowid = cursor.lastrowid
            if lastrowid is None:
                msg = "Impossible de creer l'entreprise"
                raise RuntimeError(msg)
            organization_id = int(lastrowid)
            if normalized_domain is not None:
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
                    VALUES (?, ?, 'manual', 1.0, ?, ?, 0)
                    """,
                    (organization_id, normalized_domain, now, now),
                )
            if project:
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
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (project, organization_id, role.value, now, now),
                )
        return organization_id

    def delete_organization(self, organization_id: int) -> None:
        self.initialize()
        with self._connect() as connection:
            _ensure_organization_exists(connection, organization_id)
            connection.execute(
                "DELETE FROM project_participants WHERE organization_id = ?",
                (organization_id,),
            )
            connection.execute(
                "DELETE FROM contacts WHERE organization_id = ?",
                (organization_id,),
            )
            connection.execute(
                "DELETE FROM organization_domains WHERE organization_id = ?",
                (organization_id,),
            )
            connection.execute(
                "DELETE FROM organizations WHERE id = ?",
                (organization_id,),
            )

    def list_project_participants(self, project_number: str) -> list[ProjectParticipantEntry]:
        project = project_number.strip()
        if not project:
            return []
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    organizations.id,
                    organizations.name,
                    project_participants.role,
                    project_participants.observation_count
                FROM project_participants
                JOIN organizations ON organizations.id = project_participants.organization_id
                WHERE project_participants.project_number = ?
                ORDER BY organizations.name COLLATE NOCASE
                """,
                (project,),
            ).fetchall()
            return [
                ProjectParticipantEntry(
                    organization_id=int(row[0]),
                    name=str(row[1]),
                    domains=tuple(_domains_for_organization(connection, int(row[0]))),
                    contacts=tuple(_contacts_for_organization(connection, int(row[0]))),
                    role=InterlocutorType(str(row[2])),
                    mail_count=int(row[3]),
                )
                for row in rows
            ]

    def set_project_participant_role(
        self,
        project_number: str,
        organization_id: int,
        role: InterlocutorType,
    ) -> None:
        project = project_number.strip()
        if not project:
            msg = "Le numero de projet est obligatoire"
            raise ValueError(msg)
        now = datetime.now(UTC).isoformat()
        self.initialize()
        with self._connect() as connection:
            _ensure_organization_exists(connection, organization_id)
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
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(project_number, organization_id) DO UPDATE SET
                    role = excluded.role,
                    last_seen_at = excluded.last_seen_at
                """,
                (project, organization_id, role.value, now, now),
            )

    def rename_organization(self, organization_id: int, name: str) -> None:
        if not name.strip():
            msg = "Le nom d'entreprise est obligatoire"
            raise ValueError(msg)
        cleaned = safe_folder_name(name)
        normalized = _normalize_organization_name(cleaned)
        now = datetime.now(UTC).isoformat()
        self.initialize()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id
                FROM organizations
                WHERE normalized_name = ? AND id <> ?
                LIMIT 1
                """,
                (normalized, organization_id),
            ).fetchone()
            if existing is not None:
                msg = (
                    "Une entreprise avec ce nom existe deja. "
                    "Utiliser la fusion pour regrouper les doublons."
                )
                raise ValueError(msg)
            cursor = connection.execute(
                """
                UPDATE organizations
                SET name = ?,
                    normalized_name = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (cleaned, normalized, now, organization_id),
            )
            if cursor.rowcount == 0:
                msg = f"Entreprise introuvable: {organization_id}"
                raise ValueError(msg)

    def merge_organizations(self, source_organization_id: int, target_organization_id: int) -> None:
        if source_organization_id == target_organization_id:
            msg = "Selectionner deux entreprises differentes pour fusionner"
            raise ValueError(msg)
        now = datetime.now(UTC).isoformat()
        self.initialize()
        with self._connect() as connection:
            _ensure_organization_exists(connection, source_organization_id)
            _ensure_organization_exists(connection, target_organization_id)
            connection.execute(
                """
                UPDATE organization_domains
                SET organization_id = ?,
                    last_seen_at = ?
                WHERE organization_id = ?
                """,
                (target_organization_id, now, source_organization_id),
            )
            connection.execute(
                """
                UPDATE contacts
                SET organization_id = ?,
                    last_seen_at = ?
                WHERE organization_id = ?
                """,
                (target_organization_id, now, source_organization_id),
            )
            _merge_project_participants(
                connection,
                source_organization_id=source_organization_id,
                target_organization_id=target_organization_id,
                now=now,
            )
            connection.execute(
                "UPDATE organizations SET updated_at = ? WHERE id = ?",
                (now, target_organization_id),
            )
            connection.execute(
                "DELETE FROM organizations WHERE id = ?",
                (source_organization_id,),
            )

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
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _organization_id_for_contact(connection: sqlite3.Connection, email: str) -> int | None:
    row = connection.execute(
        "SELECT organization_id FROM contacts WHERE email = ? LIMIT 1",
        (email,),
    ).fetchone()
    return None if row is None else int(row[0])


def _normalize_directory_domain(domain: str | None) -> str | None:
    if domain is None:
        return None
    normalized = domain.strip().casefold()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    normalized = normalized.strip(". ")
    if not normalized:
        return None
    if (
        len(normalized) > 253
        or "." not in normalized
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", normalized)
        or any(not label or len(label) > 63 for label in normalized.split("."))
    ):
        msg = f"Domaine invalide: {domain.strip()}"
        raise ValueError(msg)
    return normalized


def _organization_id_for_email(connection: sqlite3.Connection, email: str) -> int | None:
    contact_org = _organization_id_for_contact(connection, email)
    if contact_org is not None:
        return contact_org
    domain = _domain_from_email(email)
    if domain is None:
        return None
    return _organization_id_for_domain(connection, domain)


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


def _domains_for_organization(connection: sqlite3.Connection, organization_id: int) -> list[str]:
    rows = connection.execute(
        """
        SELECT domain
        FROM organization_domains
        WHERE organization_id = ?
        ORDER BY domain COLLATE NOCASE
        """,
        (organization_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _contacts_for_organization(connection: sqlite3.Connection, organization_id: int) -> list[str]:
    rows = connection.execute(
        """
        SELECT email, display_name
        FROM contacts
        WHERE organization_id = ?
        ORDER BY email COLLATE NOCASE
        """,
        (organization_id,),
    ).fetchall()
    contacts: list[str] = []
    for email, display_name in rows:
        email_value = str(email)
        display_value = "" if display_name is None else str(display_name).strip()
        if display_value and display_value.casefold() != email_value.casefold():
            contacts.append(f"{display_value} <{email_value}>")
        else:
            contacts.append(email_value)
    return contacts


def _project_count_for_organization(
    connection: sqlite3.Connection,
    organization_id: int,
) -> int:
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT project_number)
        FROM project_participants
        WHERE organization_id = ?
        """,
        (organization_id,),
    ).fetchone()
    return int(row[0])


def _ensure_organization_exists(connection: sqlite3.Connection, organization_id: int) -> None:
    row = connection.execute(
        "SELECT id FROM organizations WHERE id = ? LIMIT 1",
        (organization_id,),
    ).fetchone()
    if row is None:
        msg = f"Entreprise introuvable: {organization_id}"
        raise ValueError(msg)


def _merge_project_participants(
    connection: sqlite3.Connection,
    *,
    source_organization_id: int,
    target_organization_id: int,
    now: str,
) -> None:
    source_rows = connection.execute(
        """
        SELECT id, project_number, role, observation_count
        FROM project_participants
        WHERE organization_id = ?
        """,
        (source_organization_id,),
    ).fetchall()
    for source_id, project_number, _role, observation_count in source_rows:
        target_row = connection.execute(
            """
            SELECT id
            FROM project_participants
            WHERE project_number = ? AND organization_id = ?
            LIMIT 1
            """,
            (str(project_number), target_organization_id),
        ).fetchone()
        if target_row is None:
            connection.execute(
                """
                UPDATE project_participants
                SET organization_id = ?,
                    last_seen_at = ?
                WHERE id = ?
                """,
                (target_organization_id, now, int(source_id)),
            )
            continue
        connection.execute(
            """
            UPDATE project_participants
            SET last_seen_at = ?,
                observation_count = observation_count + ?
            WHERE id = ?
            """,
            (now, int(observation_count), int(target_row[0])),
        )
        connection.execute(
            "DELETE FROM project_participants WHERE id = ?",
            (int(source_id),),
        )


def _normalize_organization_name(name: str) -> str:
    words = [word.casefold() for word in re.split(r"\W+", name) if word]
    return "-".join(words) or "inconnu"


def _domain_from_email(email: str) -> str | None:
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip(". ").casefold()
    return domain or None
