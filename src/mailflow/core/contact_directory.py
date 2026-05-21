from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Protocol

from mailflow.core.correspondence_hierarchy import safe_folder_name
from mailflow.models import MailMetadata

GENERIC_EMAIL_DOMAINS = {
    "bluewin.ch",
    "gmail.com",
    "hotmail.com",
    "icloud.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "yahoo.com",
}
INTERNAL_EMAIL_DOMAINS = {"balzmetal.ch", "balzmetalsa.onmicrosoft.com"}
LEGAL_SUFFIXES = {"ag", "gmbh", "sa", "sarl", "sagl", "ltd", "llc", "inc", "spa", "srl"}


@dataclass(frozen=True)
class ContactObservation:
    project_number: str
    email: str
    display_name: str
    organization_name: str
    domain: str
    source: str
    confidence: float
    allow_domain_mapping: bool


@dataclass(frozen=True)
class DirectoryImportResult:
    scanned_mail_count: int
    observed_contact_count: int
    imported_contact_count: int
    skipped_internal_count: int
    skipped_generic_domain_count: int
    new_organizations: int
    new_domains: int
    new_contacts: int
    new_project_participants: int


@dataclass(frozen=True)
class DirectoryUpsertOutcome:
    new_organization: bool = False
    new_domain: bool = False
    new_contact: bool = False
    new_project_participant: bool = False


class ContactDirectoryStoreProtocol(Protocol):
    def record_observation(self, observation: ContactObservation) -> DirectoryUpsertOutcome:
        ...


def import_contact_directory_from_mails(
    mails: Sequence[MailMetadata],
    store: ContactDirectoryStoreProtocol,
) -> DirectoryImportResult:
    observed = 0
    imported = 0
    skipped_internal = 0
    skipped_generic = 0
    new_organizations = 0
    new_domains = 0
    new_contacts = 0
    new_participants = 0
    for mail in mails:
        for contact in contact_references_from_mail(mail):
            observation = contact_observation_from_reference(
                project_number=mail.project_number,
                contact=contact,
            )
            if observation is None:
                skipped_internal += 1
                continue
            observed += 1
            if _should_skip_generic_contact(observation):
                skipped_generic += 1
                continue
            outcome = store.record_observation(observation)
            imported += 1
            new_organizations += int(outcome.new_organization)
            new_domains += int(outcome.new_domain)
            new_contacts += int(outcome.new_contact)
            new_participants += int(outcome.new_project_participant)
    return DirectoryImportResult(
        scanned_mail_count=len(mails),
        observed_contact_count=observed,
        imported_contact_count=imported,
        skipped_internal_count=skipped_internal,
        skipped_generic_domain_count=skipped_generic,
        new_organizations=new_organizations,
        new_domains=new_domains,
        new_contacts=new_contacts,
        new_project_participants=new_participants,
    )


def contact_references_from_mail(mail: MailMetadata) -> list[str]:
    references = [format_contact_reference(mail.sender_name, mail.sender_email)]
    references.extend(mail.recipients)
    return [reference for reference in references if reference.strip()]


def format_contact_reference(display_name: str, email: str) -> str:
    if display_name and email:
        return f"{display_name} <{email}>"
    return email or display_name


def contact_observation_from_reference(
    *,
    project_number: str,
    contact: str,
) -> ContactObservation | None:
    display_name, email = split_contact(contact)
    if not email:
        return None
    domain = email_domain(email)
    if domain is None or is_internal_domain(domain):
        return None
    organization_name, confidence = infer_organization_name(display_name, domain)
    allow_domain_mapping = not is_generic_email_domain(domain)
    return ContactObservation(
        project_number=project_number,
        email=email,
        display_name=display_name,
        organization_name=organization_name,
        domain=domain,
        source="outlook_history",
        confidence=confidence,
        allow_domain_mapping=allow_domain_mapping,
    )


def split_contact(contact: str) -> tuple[str, str]:
    display_name, email = parseaddr(contact.strip())
    if not email:
        match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", contact)
        if match:
            email = match.group(0)
    normalized_email = email.strip().strip("<>").casefold()
    cleaned_display = display_name.strip().strip('"')
    if not cleaned_display:
        cleaned_display = re.sub(r"<[^>]+>", "", contact).strip()
        cleaned_display = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "", cleaned_display).strip()
    return cleaned_display, normalized_email


def email_domain(email: str) -> str | None:
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip(". ").casefold()
    return domain or None


def is_internal_domain(domain: str) -> bool:
    normalized = domain.casefold()
    return any(
        normalized == item or normalized.endswith(f".{item}")
        for item in INTERNAL_EMAIL_DOMAINS
    )


def is_generic_email_domain(domain: str) -> bool:
    normalized = domain.casefold()
    return normalized in GENERIC_EMAIL_DOMAINS


def infer_organization_name(display_name: str, domain: str) -> tuple[str, float]:
    parenthesized = [
        value.strip()
        for value in re.findall(r"\(([^()]+)\)", display_name)
        if value.strip()
    ]
    if parenthesized:
        return safe_folder_name(parenthesized[-1]), 0.95
    if _looks_like_company_display(display_name):
        return safe_folder_name(_strip_email(display_name)), 0.90
    return _organization_from_domain(domain), 0.70


def _should_skip_generic_contact(observation: ContactObservation) -> bool:
    return not observation.allow_domain_mapping and observation.confidence < 0.90


def _looks_like_company_display(display_name: str) -> bool:
    cleaned = _strip_email(display_name)
    words = {word.casefold() for word in re.split(r"\W+", cleaned) if word}
    return bool(words & LEGAL_SUFFIXES)


def _organization_from_domain(domain: str) -> str:
    labels = [label for label in domain.casefold().split(".") if label]
    label = labels[-2] if len(labels) >= 2 else labels[0] if labels else "inconnu"
    words = [word for word in re.split(r"[-_]+", label) if word]
    if len(words) == 1 and len(words[0]) <= 4:
        return words[0].upper()
    return safe_folder_name(" ".join(word.capitalize() for word in words))


def _strip_email(value: str) -> str:
    without_angle = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "", without_angle).strip()


def unique_observations(observations: Iterable[ContactObservation]) -> list[ContactObservation]:
    seen: set[tuple[str, str]] = set()
    result: list[ContactObservation] = []
    for observation in observations:
        key = (observation.project_number, observation.email)
        if key in seen:
            continue
        seen.add(key)
        result.append(observation)
    return result
