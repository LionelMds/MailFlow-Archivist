from __future__ import annotations

from typing import Any

from mailflow.core.body_cleaner import clean_body
from mailflow.models import MailMetadata

SYSTEM_PROMPT = """\
Tu classes des e-mails Outlook de projets Balz Metal Sa pour archivage local.
Reponds uniquement avec le schema structure demande.
N'archive jamais un mail inutile.
Si la confiance est inferieure a 0.80, choisis target_folder = "A verifier".
Ne demande jamais le contenu complet des pieces jointes.
"""


def build_ai_payload(
    mail: MailMetadata,
    *,
    include_body: bool,
    privacy_mask_phone_numbers: bool,
    known_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    body_excerpt = ""
    if include_body:
        body_excerpt = clean_body(
            mail.body_excerpt,
            mask_phone_numbers=privacy_mask_phone_numbers,
        )
    return {
        "project_number": mail.project_number,
        "outlook_folder": mail.outlook_folder,
        "direction": mail.direction.value,
        "subject": mail.subject,
        "sender": mail.sender_email or mail.sender_name,
        "recipients": mail.recipients,
        "sent_at": mail.sent_at.isoformat(),
        "attachment_names": mail.attachment_names,
        "body_excerpt": body_excerpt,
        "known_context": known_context or {},
    }

