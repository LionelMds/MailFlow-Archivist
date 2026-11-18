"""Transfer of an external mail between colleagues: who wrote the forwarded message.

A colleague who forwards a supplier's offer produces a mail whose addresses are all
internal. Its counterparty is the author of the forwarded message, read from the header
block Outlook writes above it (De / Envoyé / À / Objet). Nothing here classifies the
mail: the directory and the engine still decide the company, its role and the phase.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from mailflow.core.body_cleaner import DEFAULT_EXCERPT_LIMIT, clean_body, plain_line
from mailflow.core.domains import email_domain, is_internal_domain, is_internal_mail
from mailflow.models import MailMetadata

# TR (French), FW and Fwd (English), WG (German), I (Italian).
_FORWARD_SUBJECT_RE = re.compile(r"^\s*(?:TR|FW|FWD|WG|I)\s*:", re.IGNORECASE)
# A header line once reduced to plain letters: "À :" reads "A :".
_HEADER_FIELD_RE = re.compile(r"^[>*\s]*([A-Za-z]+)\s*:\s*(.*)$")
_SENDER_FIELDS = frozenset({"de", "from", "von", "da"})
_RECIPIENT_FIELDS = frozenset({"a", "to", "an", "cc"})
# A date or a subject proves a header block: "De : ..." alone can be a sentence.
_PROOF_FIELDS = frozenset(
    {
        "envoye", "sent", "gesendet", "inviato", "date", "datum", "data",
        "objet", "subject", "betreff", "oggetto",
    }
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_MAX_HEADER_LINES = 15
FORWARD_EXCERPT_LABEL = "Message transféré de"


@dataclass(frozen=True)
class ForwardedMessage:
    external_email: str
    # What the colleague wrote above the forwarded message.
    note: str
    # The forwarded message, after its header block.
    text: str

    def excerpt(self, *, limit: int = DEFAULT_EXCERPT_LIMIT) -> str:
        """The colleague's note then the forwarded message, both cleaned.

        The usual cleaning stops at the first "De :" line and would keep the note only,
        which is often empty: the forwarded message is what the mail is about.
        """
        note = clean_body(self.note, limit=limit)
        forwarded = clean_body(self.text, limit=limit)
        label = f"{FORWARD_EXCERPT_LABEL} {self.external_email} :"
        parts = [note, f"{label}\n{forwarded}".strip()]
        return "\n\n".join(part for part in parts if part)[:limit].rstrip()


def is_forward_subject(subject: str) -> bool:
    return _FORWARD_SUBJECT_RE.match(subject) is not None


def find_forwarded_message(subject: str, body: str) -> ForwardedMessage | None:
    """The forwarded message closest to the top in which an external address takes part.

    A reply quotes the same header block as a transfer: only the subject prefix tells
    them apart, so both are required. A block between colleagues is skipped, which
    follows a mail forwarded several times inside the company.
    """
    if not is_forward_subject(subject):
        return None
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, line in enumerate(lines):
        if _field_name(line) not in _SENDER_FIELDS:
            continue
        end = _header_end(lines, index)
        fields = _header_fields(lines[index:end])
        if not any(name in _PROOF_FIELDS for name, _value in fields):
            continue
        email = _external_participant(fields)
        if email is not None:
            return ForwardedMessage(
                external_email=email,
                note="\n".join(lines[:index]),
                text="\n".join(lines[end:]),
            )
    return None


def find_internal_forward(
    subject: str,
    body: str,
    sender_email: str,
    recipients: Sequence[str],
) -> ForwardedMessage | None:
    """The forwarded external message of a mail exchanged between colleagues only."""
    if not is_internal_mail(sender_email, recipients):
        return None
    return find_forwarded_message(subject, body)


def forwarded_counterparty_email(mail: MailMetadata) -> str | None:
    """External author of a message forwarded between colleagues, else None."""
    email = mail.forwarded_external_email.strip().casefold()
    if email and is_internal_mail(mail.sender_email, mail.recipients):
        return email
    return None


def _field_name(line: str) -> str | None:
    match = _HEADER_FIELD_RE.match(plain_line(line))
    return match.group(1).casefold() if match else None


def _header_end(lines: list[str], start: int) -> int:
    limit = min(len(lines), start + _MAX_HEADER_LINES)
    for index in range(start, limit):
        if not lines[index].strip():
            return index
    return limit


def _header_fields(header: list[str]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for line in header:
        match = _HEADER_FIELD_RE.match(plain_line(line))
        if match:
            fields.append((match.group(1).casefold(), match.group(2)))
        elif fields:
            # A long recipient list continues on the next line.
            name, value = fields[-1]
            fields[-1] = (name, f"{value} {line.strip()}")
    return fields


def _external_participant(fields: list[tuple[str, str]]) -> str | None:
    """The external author; for a message a colleague sent, its first external recipient."""
    for wanted in (_SENDER_FIELDS, _RECIPIENT_FIELDS):
        for name, value in fields:
            if name not in wanted:
                continue
            email = _first_external_email(value)
            if email is not None:
                return email
    return None


def _first_external_email(value: str) -> str | None:
    for match in _EMAIL_RE.finditer(value):
        email = match.group(0).strip(".").casefold()
        domain = email_domain(email)
        if domain and not is_internal_domain(domain):
            return email
    return None
