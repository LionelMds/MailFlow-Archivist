from __future__ import annotations

from datetime import datetime

from mailflow.classifier.routing_context import primary_external_email, resolve_counterparty
from mailflow.models import Direction, InterlocutorType, MailMetadata


class Directory:
    def organization_name_for_email(self, email: str) -> str | None:
        return "AIG" if email.endswith("@gva.ch") else None

    def interlocutor_for_email(
        self,
        project_number: str,
        email: str,
    ) -> InterlocutorType | None:
        if project_number == "2025-4788" and email.endswith("@gva.ch"):
            return InterlocutorType.CLIENT
        return None


def mail(**updates: object) -> MailMetadata:
    values: dict[str, object] = {
        "entry_id": "ENTRY-1",
        "project_number": "2025-4788",
        "outlook_folder": "Inbox/2025/2025-4788",
        "direction": Direction.SENT,
        "subject": "Plan",
        "sender_email": "lionel@balzmetal.ch",
        "recipients": ["blaise.riva@gva.ch", "andre@balzmetal.ch"],
        "sent_at": datetime(2026, 4, 15, 9, 30),
    }
    values.update(updates)
    return MailMetadata.model_validate(values)


def test_first_external_recipient_has_priority_over_internal_copy() -> None:
    assert primary_external_email(mail()) == "blaise.riva@gva.ch"


def test_received_mail_can_resolve_known_sender_from_body_fallback() -> None:
    item = mail(
        direction=Direction.RECEIVED,
        sender_email="",
        sender_name="RIVA Blaise",
        recipients=["lionel@balzmetal.ch"],
        body_excerpt="Geneve Aeroport - blaise.riva@gva.ch",
    )

    counterparty = resolve_counterparty(item, Directory())

    assert counterparty.email == "blaise.riva@gva.ch"
    assert counterparty.organization_name == "AIG"
    assert counterparty.role == InterlocutorType.CLIENT
    assert counterparty.organization_locked
