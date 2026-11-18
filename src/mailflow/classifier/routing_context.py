from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from mailflow.core.contact_directory import email_domain, is_internal_domain, split_contact
from mailflow.core.correspondence_hierarchy import UNKNOWN_COMPANY, company_for_mail
from mailflow.core.domains import INTERNAL_COMPANY_FOLDER, is_internal_mail
from mailflow.core.forwarded_mail import forwarded_counterparty_email
from mailflow.models import (
    Direction,
    InterlocutorType,
    MailMetadata,
    VerifiedRoutingExample,
)

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
# Only colleagues take part in the mail.
INTERNAL_MAIL_SOURCE = "internal_mail"
# A colleague forwarded an external mail to colleagues: its author is the counterparty.
INTERNAL_FORWARD_SOURCE = "internal_forward"
# The directory gives the role "interne" to the company of the counterparty.
INTERNAL_ROLE_SOURCE = "directory_internal_role"


class RoutingDirectoryProtocol(Protocol):
    def organization_name_for_email(self, email: str) -> str | None:
        ...

    def interlocutor_for_email(
        self,
        project_number: str,
        email: str,
    ) -> InterlocutorType | None:
        ...


@dataclass(frozen=True)
class ResolvedCounterparty:
    email: str | None
    organization_name: str
    role: InterlocutorType | None
    organization_locked: bool
    source: str

    @property
    def history_key(self) -> str:
        if self.organization_locked:
            return self.organization_name.casefold()
        if self.email:
            domain = email_domain(self.email)
            if domain:
                return domain
        return self.organization_name.casefold()


def resolve_counterparty(
    mail: MailMetadata,
    directory: RoutingDirectoryProtocol | None,
) -> ResolvedCounterparty:
    email, source = _primary_external_email(mail, directory)
    if source == INTERNAL_MAIL_SOURCE:
        return _internal_counterparty(None, source)
    organization_name = None
    role = None
    if email and directory is not None:
        organization_name = directory.organization_name_for_email(email)
        role = directory.interlocutor_for_email(mail.project_number, email)
    if role == InterlocutorType.INTERNE:
        return _internal_counterparty(email, INTERNAL_ROLE_SOURCE)
    locked = bool(organization_name)
    if not organization_name:
        organization_name = company_for_mail(mail, organization_directory=directory)
    return ResolvedCounterparty(
        email=email,
        organization_name=organization_name or UNKNOWN_COMPANY,
        role=role,
        organization_locked=locked,
        source=source,
    )


def _internal_counterparty(email: str | None, source: str) -> ResolvedCounterparty:
    """An internal mail has no company to find: its folder is always the same."""
    return ResolvedCounterparty(
        email=email,
        organization_name=INTERNAL_COMPANY_FOLDER,
        role=InterlocutorType.INTERNE,
        organization_locked=True,
        source=source,
    )


def build_routing_context(
    mail: MailMetadata,
    counterparty: ResolvedCounterparty,
    *,
    history: list[dict[str, str]],
    verified_examples: list[VerifiedRoutingExample],
) -> dict[str, Any]:
    role = counterparty.role.value if counterparty.role is not None else "inconnu"
    relevant_examples = [
        example.model_dump(mode="json")
        for example in verified_examples
        if example.organization_name.casefold() == counterparty.organization_name.casefold()
        or example.project_number == mail.project_number
    ][-5:]
    return {
        "routing_policy": {
            "allowed_categories": ["Correspondance", "Demande de prix", "Commande"],
            "client_destination": "Correspondance/<entreprise>",
            "supplier_destinations": [
                "Fournisseurs/Demande de prix/<entreprise>",
                "Fournisseurs/Commande/<entreprise>",
            ],
        },
        "counterparty": {
            "primary_email": counterparty.email or "",
            "organization_name": counterparty.organization_name,
            "project_role": role,
            "organization_locked_by_directory": counterparty.organization_locked,
            "source": counterparty.source,
        },
        "recent_company_history": history[-6:],
        "verified_manual_examples": relevant_examples,
    }


def primary_external_email(mail: MailMetadata) -> str | None:
    email, _source = _primary_external_email(mail, None)
    return email


def _primary_external_email(
    mail: MailMetadata,
    directory: RoutingDirectoryProtocol | None,
) -> tuple[str | None, str]:
    if is_internal_mail(mail.sender_email, mail.recipients):
        forwarded = forwarded_counterparty_email(mail)
        if forwarded is not None:
            return forwarded, INTERNAL_FORWARD_SOURCE
        # Decided on the addresses alone: an address quoted in the body changes nothing.
        return None, INTERNAL_MAIL_SOURCE
    if mail.direction == Direction.SENT:
        for recipient in mail.recipients:
            email = _external_email_from_text(recipient)
            if email:
                return email, "first_external_recipient"
        return None, "no_external_recipient"

    for value in (mail.sender_email, mail.sender_name):
        email = _external_email_from_text(value)
        if email:
            return email, "sender"

    sender_domain = email_domain(mail.sender_email)
    if sender_domain and is_internal_domain(sender_domain):
        # A colleague's mail read from a copy: the counterparty is whom it was sent to.
        for recipient in mail.recipients:
            email = _external_email_from_text(recipient)
            if email:
                return email, "first_external_recipient"

    body_emails = _external_emails_from_text(mail.body_excerpt)
    if directory is not None:
        for email in body_emails:
            if directory.organization_name_for_email(email):
                return email, "body_directory_match"
    if body_emails:
        return body_emails[0], "body_fallback"
    return None, "sender_unknown"


def _external_email_from_text(value: str) -> str | None:
    _display_name, parsed_email = split_contact(value)
    candidates = [parsed_email] if parsed_email else []
    candidates.extend(match.group(0).casefold() for match in EMAIL_PATTERN.finditer(value))
    for email in candidates:
        domain = email_domain(email)
        if domain and not is_internal_domain(domain):
            return email
    return None


def _external_emails_from_text(value: str) -> list[str]:
    result: list[str] = []
    for match in EMAIL_PATTERN.finditer(value):
        email = match.group(0).casefold()
        domain = email_domain(email)
        if domain and not is_internal_domain(domain) and email not in result:
            result.append(email)
    return result
