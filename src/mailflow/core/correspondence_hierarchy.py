from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from mailflow.core.project_paths import local_project_path
from mailflow.models import Direction, InterlocutorType, MailType, PreviewRow

CORRESPONDENCE_FOLDER = "CORRESPONDANCE"
SUPPLIER_REQUEST_FOLDER = "DEMANDE DE PRIX"
SUPPLIER_ORDER_FOLDER = "COMMANDE"
UNKNOWN_COMPANY = "Interlocuteur inconnu"
INTERNAL_DOMAINS = ("balzmetal.ch",)
LEGAL_SUFFIXES = {"ag", "gmbh", "sa", "sarl", "sagl", "ltd", "llc", "inc", "spa", "srl"}
GENERIC_CONTACT_WORDS = {"contact", "info", "office", "sales", "vente", "bureau"}
INVALID_PATH_CHARS = '<>:"/\\|?*'


@dataclass(frozen=True)
class HierarchicalFolder:
    company: str
    relative_folder: str


@dataclass(frozen=True)
class CompanyCandidate:
    name: str
    priority: int


class OrganizationDirectoryProtocol(Protocol):
    def organization_name_for_email(self, email: str) -> str | None:
        ...


def apply_correspondence_hierarchy(
    rows: Sequence[PreviewRow],
    *,
    projects_root: Path,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> list[PreviewRow]:
    folder_by_mail_id = _folder_plan(rows, organization_directory=organization_directory)
    return [
        _row_with_folder(row, folder_by_mail_id[row.mail.entry_id], projects_root=projects_root)
        for row in rows
    ]


def company_folder_for_row(
    row: PreviewRow,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> str:
    return company_from_mail(
        row.mail.direction,
        row.mail.sender_name,
        row.mail.sender_email,
        row.mail.recipients,
        organization_directory=organization_directory,
    )


def company_key_for_row(
    row: PreviewRow,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> str:
    return company_key_from_mail(
        row.mail.direction,
        row.mail.sender_name,
        row.mail.sender_email,
        row.mail.recipients,
        organization_directory=organization_directory,
    )


def company_from_mail(
    direction: Direction,
    sender_name: str,
    sender_email: str,
    recipients: Sequence[str],
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> str:
    return _select_company_candidate(
        _company_candidates_from_mail(
            direction,
            sender_name,
            sender_email,
            recipients,
            organization_directory=organization_directory,
        )
    ).name


def company_key_from_mail(
    direction: Direction,
    sender_name: str,
    sender_email: str,
    recipients: Sequence[str],
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> str:
    if direction == Direction.SENT:
        for recipient in recipients:
            if not _is_internal_contact(recipient):
                return _company_key_from_contact(
                    recipient,
                    organization_directory=organization_directory,
                )
    if sender_email and not _is_internal_contact(sender_email):
        return _company_key_from_contact(
            sender_email,
            organization_directory=organization_directory,
        )
    return _company_key_from_contact(sender_name, organization_directory=organization_directory)


def safe_folder_name(value: str) -> str:
    cleaned = value.strip()
    for char in INVALID_PATH_CHARS:
        cleaned = cleaned.replace(char, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:80] or UNKNOWN_COMPANY


def is_safe_relative_folder(value: str) -> bool:
    cleaned = value.replace("\\", "/").strip("/")
    if not cleaned or cleaned in {"A verifier", "Ne pas archiver"}:
        return bool(cleaned)
    if re.match(r"^[A-Za-z]:", cleaned):
        return False
    parts = [part.strip() for part in cleaned.split("/")]
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return all(safe_folder_name(part) == part for part in parts)


def _folder_plan(
    rows: Sequence[PreviewRow],
    *,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> dict[str, HierarchicalFolder]:
    plan: dict[str, HierarchicalFolder] = {}
    supplier_groups: dict[tuple[str, str], list[PreviewRow]] = defaultdict(list)
    for row in rows:
        company = company_folder_for_row(row, organization_directory)
        company_key = company_key_for_row(row, organization_directory)
        interlocutor = row.decision.interlocutor
        if row.decision.target_relative_folder in {"A verifier", "Ne pas archiver"}:
            plan[row.mail.entry_id] = HierarchicalFolder(
                company,
                row.decision.target_relative_folder,
            )
        elif interlocutor in {
            InterlocutorType.CLIENT,
            InterlocutorType.INTERVENANT_EXTERNE,
            InterlocutorType.INTERNE,
            InterlocutorType.INCONNU,
        }:
            plan[row.mail.entry_id] = HierarchicalFolder(
                company,
                f"{CORRESPONDENCE_FOLDER}/{company}",
            )
        elif interlocutor == InterlocutorType.FOURNISSEUR:
            supplier_groups[(row.mail.project_number, company_key)].append(row)
        else:
            plan[row.mail.entry_id] = HierarchicalFolder(
                company,
                f"{CORRESPONDENCE_FOLDER}/{company}",
            )

    for (_project_number, _company_key), supplier_rows in supplier_groups.items():
        company = _best_company_display(
            supplier_rows,
            organization_directory=organization_directory,
        )
        plan.update(_supplier_folder_plan(company, supplier_rows))
    return plan


def _supplier_folder_plan(
    company: str,
    rows: Sequence[PreviewRow],
) -> dict[str, HierarchicalFolder]:
    result: dict[str, HierarchicalFolder] = {}
    for segment in _supplier_request_segments(rows):
        latest_offer_at = max(
            (
                row.mail.sent_at
                for row in segment
                if row.decision.mail_type == MailType.DEVIS
            ),
            default=None,
        )
        for row in segment:
            folder = _supplier_folder_for_row(row, latest_offer_at=latest_offer_at)
            result[row.mail.entry_id] = HierarchicalFolder(company, f"{folder}/{company}")
    return result


def _supplier_request_segments(rows: Sequence[PreviewRow]) -> Iterable[list[PreviewRow]]:
    segment: list[PreviewRow] = []
    for row in sorted(rows, key=lambda item: (item.mail.sent_at, item.mail.entry_id)):
        if row.decision.mail_type == MailType.DEMANDE_DE_PRIX and segment:
            yield segment
            segment = []
        segment.append(row)
    if segment:
        yield segment


def _supplier_folder_for_row(row: PreviewRow, *, latest_offer_at: datetime | None) -> str:
    if row.decision.mail_type == MailType.DEMANDE_DE_PRIX:
        return SUPPLIER_REQUEST_FOLDER
    if latest_offer_at is not None:
        if row.mail.sent_at <= latest_offer_at:
            return SUPPLIER_REQUEST_FOLDER
        return SUPPLIER_ORDER_FOLDER
    if row.decision.mail_type == MailType.COMMANDE:
        return SUPPLIER_ORDER_FOLDER
    return SUPPLIER_REQUEST_FOLDER


def _row_with_folder(
    row: PreviewRow,
    folder: HierarchicalFolder,
    *,
    projects_root: Path,
) -> PreviewRow:
    if folder.relative_folder == row.decision.target_relative_folder:
        return row
    project_path = local_project_path(projects_root, row.mail.project_number)
    target_path = (
        project_path
        if folder.relative_folder in {"A verifier", "Ne pas archiver"}
        else project_path.joinpath(*folder.relative_folder.split("/"))
    )
    decision = row.decision.model_copy(
        update={
            "target_relative_folder": folder.relative_folder,
            "target_path": target_path,
            "reason": _append_hierarchy_reason(row.decision.reason, folder.relative_folder),
        }
    )
    return row.model_copy(update={"decision": decision})


def _append_hierarchy_reason(reason: str, relative_folder: str) -> str:
    note = f"Dossier hierarchise: {relative_folder}."
    if note in reason:
        return reason
    return f"{reason} {note}".strip()


def _company_from_contact(contact: str) -> str:
    cleaned = contact.strip()
    if not cleaned:
        return UNKNOWN_COMPANY
    parenthesized = re.findall(r"\(([^()]+)\)", cleaned)
    if parenthesized:
        return safe_folder_name(parenthesized[-1])
    email_match = re.search(r"[\w.+-]+@([\w.-]+)", cleaned)
    if email_match:
        return _company_from_domain(email_match.group(1))
    without_email = re.sub(r"<[^>]+>", "", cleaned).strip()
    return safe_folder_name(without_email or cleaned)


def _company_candidates_from_mail(
    direction: Direction,
    sender_name: str,
    sender_email: str,
    recipients: Sequence[str],
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> list[CompanyCandidate]:
    if direction == Direction.SENT:
        candidates: list[CompanyCandidate] = []
        for recipient in recipients:
            if not _is_internal_contact(recipient):
                candidates.extend(
                    _company_candidates_from_contact(
                        recipient,
                        organization_directory=organization_directory,
                    )
                )
        return candidates or [CompanyCandidate(UNKNOWN_COMPANY, 99)]
    return _company_candidates_from_contact(
        sender_name,
        sender_email,
        organization_directory=organization_directory,
    )


def _company_candidates_from_contact(
    contact: str,
    email: str = "",
    *,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> list[CompanyCandidate]:
    cleaned = contact.strip()
    email_cleaned = email.strip()
    if not cleaned and not email_cleaned:
        return [CompanyCandidate(UNKNOWN_COMPANY, 99)]

    candidates: list[CompanyCandidate] = []
    email_for_lookup = _email_from_contact(email_cleaned or cleaned)
    if organization_directory is not None and email_for_lookup is not None:
        directory_company = organization_directory.organization_name_for_email(email_for_lookup)
        if directory_company:
            candidates.append(CompanyCandidate(safe_folder_name(directory_company), -1))

    parenthesized = re.findall(r"\(([^()]+)\)", cleaned)
    candidates.extend(
        CompanyCandidate(safe_folder_name(value), 0)
        for value in parenthesized
        if value.strip()
    )

    domain = _domain_from_contact(email_cleaned or cleaned)
    domain_company = _company_from_domain(domain) if domain is not None else None
    display = re.sub(r"<[^>]+>", "", cleaned)
    display = re.sub(r"[\w.+-]+@[\w.-]+", "", display).strip()
    if display:
        display_name = safe_folder_name(display)
        priority = 1 if _looks_like_company_display(display_name, domain_company) else 4
        candidates.append(CompanyCandidate(display_name, priority))
    if domain_company is not None:
        candidates.append(CompanyCandidate(domain_company, 2))

    return candidates or [CompanyCandidate(UNKNOWN_COMPANY, 99)]


def _select_company_candidate(candidates: Sequence[CompanyCandidate]) -> CompanyCandidate:
    useful = [candidate for candidate in candidates if candidate.name != UNKNOWN_COMPANY]
    if not useful:
        return CompanyCandidate(UNKNOWN_COMPANY, 99)
    counts = {
        candidate.name: sum(1 for item in useful if item.name == candidate.name)
        for candidate in useful
    }
    return min(
        useful,
        key=lambda candidate: (
            candidate.priority,
            -counts[candidate.name],
            -len(candidate.name),
            candidate.name.casefold(),
        ),
    )


def _domain_from_contact(contact: str) -> str | None:
    email_match = re.search(r"[\w.+-]+@([\w.-]+)", contact)
    if email_match:
        return email_match.group(1)
    if "@" not in contact and "." in contact and not re.search(r"\s", contact):
        return contact
    return None


def _email_from_contact(contact: str) -> str | None:
    email_match = re.search(r"[\w.+-]+@[\w.-]+", contact)
    if email_match:
        return email_match.group(0).casefold()
    return None


def _looks_like_company_display(display_name: str, domain_company: str | None) -> bool:
    lowered_words = {word.casefold() for word in re.split(r"\W+", display_name) if word}
    if lowered_words & LEGAL_SUFFIXES:
        return True
    if domain_company is None:
        return False
    display_company_words = lowered_words - LEGAL_SUFFIXES - GENERIC_CONTACT_WORDS
    domain_words = {
        word.casefold()
        for word in re.split(r"\W+", domain_company)
        if word and word.casefold() not in LEGAL_SUFFIXES
    }
    return bool(domain_words and display_company_words == domain_words)


def _company_from_domain(domain: str) -> str:
    labels = [label for label in domain.lower().split(".") if label]
    if not labels:
        return UNKNOWN_COMPANY
    company_label = labels[-2] if len(labels) >= 2 else labels[0]
    words = [word for word in re.split(r"[-_]+", company_label) if word]
    if not words:
        return UNKNOWN_COMPANY
    if len(words) == 1 and len(words[0]) <= 4:
        return words[0].upper()
    return safe_folder_name(" ".join(_format_company_word(word) for word in words))


def _company_key_from_contact(
    contact: str,
    *,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> str:
    cleaned = contact.strip()
    email = _email_from_contact(cleaned)
    if organization_directory is not None and email is not None:
        directory_company = organization_directory.organization_name_for_email(email)
        if directory_company:
            return _normalize_company_key(directory_company)
    email_match = re.search(r"[\w.+-]+@([\w.-]+)", cleaned)
    if email_match:
        return _company_key_from_domain(email_match.group(1))
    return _normalize_company_key(_company_from_contact(cleaned))


def _company_key_from_domain(domain: str) -> str:
    labels = [label for label in domain.lower().split(".") if label]
    if not labels:
        return UNKNOWN_COMPANY.casefold()
    return labels[-2] if len(labels) >= 2 else labels[0]


def _best_company_display(
    rows: Sequence[PreviewRow],
    *,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> str:
    candidates = [
        candidate
        for row in rows
        for candidate in _company_candidates_from_mail(
            row.mail.direction,
            row.mail.sender_name,
            row.mail.sender_email,
            row.mail.recipients,
            organization_directory=organization_directory,
        )
    ]
    return _select_company_candidate(candidates).name


def _normalize_company_key(value: str) -> str:
    cleaned = value.casefold()
    words = [word for word in re.split(r"\W+", cleaned) if word and word not in LEGAL_SUFFIXES]
    return "-".join(words) or UNKNOWN_COMPANY.casefold()


def _format_company_word(word: str) -> str:
    if word in LEGAL_SUFFIXES:
        return word.upper()
    return word.capitalize()


def _is_internal_contact(contact: str) -> bool:
    lowered = contact.lower()
    return any(domain in lowered for domain in INTERNAL_DOMAINS)
