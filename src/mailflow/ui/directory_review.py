"""Annuaire review: one company at a time, with the role Jev suggests beside it."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mailflow.core.role_suggestions import ROLE_LABELS
from mailflow.models import InterlocutorType

BUSINESS_ROLES = (InterlocutorType.CLIENT, InterlocutorType.FOURNISSEUR)
ROLE_TAGS = {
    InterlocutorType.CLIENT: ("Client", "accent"),
    InterlocutorType.FOURNISSEUR: ("Fournisseur", "accent"),
    InterlocutorType.INTERVENANT_EXTERNE: ("Intervenant externe", "neutral"),
    InterlocutorType.INTERNE: ("Interne", "neutral"),
    InterlocutorType.INCONNU: ("Sans rôle", "outline"),
}


@dataclass(frozen=True)
class DirectoryItemView:
    organization_id: int
    name: str
    project_text: str
    domain_text: str
    suggestion_text: str


def entry_role(entry: Any) -> InterlocutorType:
    role = getattr(entry, "default_role", InterlocutorType.INCONNU)
    return role if isinstance(role, InterlocutorType) else InterlocutorType.INCONNU


def has_business_role(entry: Any) -> bool:
    return entry_role(entry) in BUSINESS_ROLES


def role_tag(role: InterlocutorType) -> tuple[str, str]:
    return ROLE_TAGS[role]


def projects_text(count: int) -> str:
    return "1 projet" if count == 1 else f"{count} projets"


def domains_text(domains: Sequence[str]) -> str:
    cleaned = [f"@{domain}" for domain in domains if domain.strip()]
    if not cleaned:
        return "sans domaine"
    if len(cleaned) > 3:
        return ", ".join(cleaned[:3]) + f", +{len(cleaned) - 3}"
    return ", ".join(cleaned)


def suggestion_line(suggestion: Any | None) -> str:
    if suggestion is None or suggestion.role is None:
        return ""
    label = ROLE_LABELS.get(suggestion.key, suggestion.key).casefold()
    return f"Jev suggère : {label} · {suggestion.probability:.0%}"


def directory_item_view(entry: Any, suggestion: Any | None) -> DirectoryItemView:
    shown_suggestion = None if has_business_role(entry) else suggestion
    return DirectoryItemView(
        organization_id=int(entry.organization_id),
        name=str(entry.name),
        project_text=projects_text(int(getattr(entry, "project_count", 0))),
        domain_text=domains_text(tuple(str(domain) for domain in entry.domains)),
        suggestion_text=suggestion_line(shown_suggestion),
    )


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def matches_directory_filter(entry: Any, query: str, *, without_role_only: bool) -> bool:
    if without_role_only and has_business_role(entry):
        return False
    terms = _plain(query).split()
    if not terms:
        return True
    searchable = _plain(
        " ".join([str(entry.name), *map(str, entry.domains), *map(str, entry.contacts)])
    )
    return all(term in searchable for term in terms)


def next_without_role(
    entries: Sequence[Any],
    current_id: int | None,
) -> int | None:
    """The next company still waiting for a role after `current_id`, wrapping around."""
    ids = [int(entry.organization_id) for entry in entries]
    if not ids:
        return None
    start = ids.index(current_id) if current_id in ids else -1
    for offset in range(1, len(ids) + 1):
        index = (start + offset) % len(ids)
        if ids[index] != current_id and not has_business_role(entries[index]):
            return ids[index]
    return None


def suggestion_headline(suggestion: Any | None) -> str:
    if suggestion is None:
        return ""
    label = ROLE_LABELS.get(suggestion.key, suggestion.key).casefold()
    return f"Probablement <b>{label}</b>."


def split_contact(contact: str) -> tuple[str, str]:
    """Split "Name <address>" into its parts; a bare address keeps an empty name."""
    match = re.fullmatch(r"\s*(.*?)\s*<([^>]+)>\s*", contact)
    if match:
        return match.group(1).strip('"'), match.group(2)
    if "@" in contact:
        return "", contact.strip()
    return contact.strip(), ""


def without_role_count(entries: Sequence[Any]) -> int:
    return sum(not has_business_role(entry) for entry in entries)


def selected_suggestion(
    suggestions: Mapping[int, Any],
    entry: Any,
) -> Any | None:
    """The suggestion to show: only for companies whose role is not set yet."""
    if has_business_role(entry):
        return None
    return suggestions.get(int(entry.organization_id))
