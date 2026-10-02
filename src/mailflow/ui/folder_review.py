"""Arborescence review: likely duplicate company folders and the texts around them.

Two sibling folders whose company names differ only by punctuation, accents or a legal
suffix ("Alu Profil" and "Alu-Profil SA") are offered for merging. Nothing is merged
without the user, and nothing is written to disk before archiving.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from mailflow.classifier.routing_context import primary_external_email
from mailflow.core.correspondence_hierarchy import CORRESPONDENCE_FOLDER, SUPPLIER_ROOT_FOLDER
from mailflow.models import PreviewAction, PreviewRow

LEGAL_SUFFIXES = frozenset({
    "sa", "sarl", "sas", "ag", "gmbh", "sagl", "ltd", "inc", "srl", "spa", "co", "cie",
    "et", "and", "kg",
})


@dataclass(frozen=True)
class DuplicateHint:
    source: str
    target: str
    source_count: int
    target_count: int
    shared_domain: str | None = None

    @property
    def source_name(self) -> str:
        return leaf_name(self.source)

    @property
    def target_name(self) -> str:
        return leaf_name(self.target)


def leaf_name(relative_folder: str) -> str:
    return relative_folder.rstrip("/").split("/")[-1]


def parent_path(relative_folder: str) -> str:
    head, _separator, _leaf = relative_folder.rstrip("/").rpartition("/")
    return head


def company_key(name: str) -> str:
    """Compare company names without accents, punctuation, case or legal suffixes."""
    normalized = unicodedata.normalize("NFKD", name)
    plain = "".join(char for char in normalized if not unicodedata.combining(char))
    words = re.findall(r"[a-z0-9]+", plain.casefold())
    kept = [word for word in words if word not in LEGAL_SUFFIXES] or words
    return "".join(kept)


def folder_rows(rows: Sequence[PreviewRow], relative_folder: str) -> list[PreviewRow]:
    prefix = f"{relative_folder}/"
    return [
        row for row in rows
        if row.decision.target_relative_folder == relative_folder
        or row.decision.target_relative_folder.startswith(prefix)
    ]


def _sender_domains(rows: Sequence[PreviewRow]) -> Counter[str]:
    domains: Counter[str] = Counter()
    for row in rows:
        email = primary_external_email(row.mail)
        if email and "@" in email:
            domains[email.rsplit("@", 1)[1].casefold()] += 1
    return domains


def find_duplicate_folders(
    rows: Sequence[PreviewRow],
    *,
    ignored: Collection[str] = (),
) -> list[DuplicateHint]:
    """Sibling company folders that name the same company, smallest one first."""
    counts = Counter(row.decision.target_relative_folder for row in rows)
    groups: dict[tuple[str, str], list[str]] = {}
    for folder in counts:
        parent = parent_path(folder)
        if not parent:
            continue
        key = company_key(leaf_name(folder))
        if key:
            groups.setdefault((parent, key), []).append(folder)
    hints: list[DuplicateHint] = []
    for folders in groups.values():
        if len(folders) < 2:
            continue
        # The folder holding most mails is the official one; ties keep the longest name,
        # which usually carries the legal form ("Alu-Profil SA" over "Alu Profil").
        target = max(folders, key=lambda folder: (counts[folder], len(folder), folder))
        target_domains = _sender_domains(folder_rows(rows, target))
        for source in sorted(folders):
            if source == target or source in ignored:
                continue
            source_domains = _sender_domains(folder_rows(rows, source))
            shared = sorted(set(source_domains) & set(target_domains))
            hints.append(
                DuplicateHint(
                    source=source,
                    target=target,
                    source_count=counts[source],
                    target_count=counts[target],
                    shared_domain=shared[0] if shared else None,
                )
            )
    return sorted(hints, key=lambda hint: hint.source)


def folder_role_tag(relative_folder: str) -> str:
    """The role a company folder implies: client under Correspondance, else supplier."""
    parent = parent_path(relative_folder)
    if not parent:
        return ""
    if parent == CORRESPONDENCE_FOLDER:
        return "client"
    if parent.startswith(f"{SUPPLIER_ROOT_FOLDER}/"):
        return "fournisseur"
    return ""


def breadcrumb_text(relative_folder: str) -> str:
    parent = parent_path(relative_folder)
    return f"{parent.replace('/', ' / ')} /" if parent else "Racine du projet"


def tree_header_text(rows: Sequence[PreviewRow]) -> str:
    if not rows:
        return "Organisez les destinations avant de lancer l'archivage."
    projects = sorted({row.mail.project_number for row in rows})
    project = projects[0] if len(projects) == 1 else f"{len(projects)} projets"
    folders = len({row.decision.target_relative_folder for row in rows})
    return f"{project} · {folders} dossiers · {len(rows)} mails"


def duplicate_note(hint: DuplicateHint) -> str:
    mails = "1 mail" if hint.source_count == 1 else f"{hint.source_count} mails"
    if hint.shared_domain:
        return (
            f"{mails} · même domaine @{hint.shared_domain} que « {hint.target_name} » "
            f"({hint.target_count} mails)."
        )
    return f"{mails} · nom proche de « {hint.target_name} » ({hint.target_count} mails)."


def merge_text(hint: DuplicateHint) -> str:
    mails = "1 mail sera déplacé" if hint.source_count == 1 else (
        f"{hint.source_count} mails seront déplacés"
    )
    return (
        f"{mails} vers le dossier officiel. Aucun fichier n'est créé avant l'archivage."
    )


def folder_text(rows: Sequence[PreviewRow]) -> str:
    archived = sum(row.action == PreviewAction.ARCHIVED for row in rows)
    mails = "1 mail" if len(rows) == 1 else f"{len(rows)} mails"
    text = f"{mails} dans ce dossier."
    if archived:
        text += f" {archived} déjà archivé(s), leur classement est conservé."
    return text + " Aucun fichier n'est créé avant l'archivage."
