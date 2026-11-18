"""Mail domains of Balz Metal and company-name rules shared by directory and routing."""

from __future__ import annotations

import re
from collections.abc import Sequence

INTERNAL_EMAIL_DOMAINS = frozenset({"balzmetal.ch", "balzmetalsa.onmicrosoft.com"})
# Folder of the mails exchanged between colleagues: Correspondance/Balzmetal.
INTERNAL_COMPANY_FOLDER = "Balzmetal"
LEGAL_SUFFIXES = frozenset(
    {"ag", "gmbh", "sa", "sarl", "sagl", "ltd", "llc", "inc", "spa", "srl"}
)

_EMAIL_DOMAIN_RE = re.compile(r"[\w.+-]+@([\w.-]+)")


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


def is_internal_contact(contact: str) -> bool:
    """True when a contact text (name, address or both) holds an internal address."""
    return any(
        is_internal_domain(match.group(1).strip("."))
        for match in _EMAIL_DOMAIN_RE.finditer(contact)
    )


def is_exchange_address(address: str) -> bool:
    """True for an Exchange directory name (/o=...), kept when no SMTP address resolves."""
    return address.strip().casefold().startswith("/o=")


def is_internal_participant(contact: str) -> bool:
    """True when the contact holds addresses and all of them belong to Balz Metal."""
    domains = [match.group(1).strip(".") for match in _EMAIL_DOMAIN_RE.finditer(contact)]
    return bool(domains) and all(is_internal_domain(domain) for domain in domains)


def is_internal_mail(sender_email: str, recipients: Sequence[str]) -> bool:
    """True for a mail between colleagues: no external address takes part.

    Outlook only gives an Exchange directory name to a sender of the organisation, so an
    unresolved sender is a colleague. A recipient without an internal SMTP address is
    not proven internal (a shared contact can be external): the mail stays undecided.
    """
    sender_is_internal = is_internal_participant(sender_email) or is_exchange_address(
        sender_email
    )
    return sender_is_internal and all(
        is_internal_participant(recipient) for recipient in recipients
    )
