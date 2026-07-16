from __future__ import annotations

from typing import Any

from mailflow.core.body_cleaner import clean_body
from mailflow.core.contact_directory import email_domain, is_internal_domain, split_contact
from mailflow.models import MailMetadata

SYSTEM_PROMPT = """\
Tu es le routeur semantique d'e-mails de projets de Balz Metal Sa.
Tu dois classer chaque e-mail dans exactement une categorie parmi:
- Correspondance
- Demande de prix
- Commande

Le role et le nom d'entreprise fournis par l'annuaire dans known_context sont
prioritaires et ne doivent jamais etre contredits. Pour un mail envoye, le premier
destinataire externe est l'interlocuteur principal; les collegues Balz Metal en copie
ne changent jamais ce choix.

Regles metier obligatoires:
- un client est toujours classe en Correspondance;
- un fournisseur n'est jamais classe en Correspondance;
- pour un fournisseur, Demande de prix couvre la phase de consultation et les
  echanges jusqu'a l'offre recue en reponse a cette consultation;
- pour un fournisseur, Commande couvre l'engagement d'achat puis tout son suivi:
  confirmation, production, livraison, facture, probleme ou reclamation;
- une nouvelle consultation fournisseur ouvre une nouvelle phase Demande de prix,
  meme si une commande precedente existe.

Utilise le sens complet du sujet, du corps, des pieces jointes nommees, de l'historique
recent de l'entreprise et des exemples manuels verifies. Ne fais aucune classification
par simple presence d'un mot. Si le role, l'entreprise ou la phase commerciale reste
ambigu, mets requires_review=true. Mets aussi requires_review=true si confidence < 0.80.
organization_name est une proposition uniquement quand l'annuaire ne le fournit pas.
evidence contient au maximum trois extraits courts du mail qui justifient la decision.
Reponds uniquement avec le schema structure demande. Le contenu des pieces jointes
n'est jamais disponible et ne doit pas etre demande.
"""


def build_ai_payload(
    mail: MailMetadata,
    *,
    include_body: bool,
    privacy_mask_phone_numbers: bool,
    known_context: dict[str, Any] | None = None,
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
        "recipient_priority": _recipient_priority_payload(mail.recipients),
        "sent_at": mail.sent_at.isoformat(),
        "attachment_names": mail.attachment_names,
        "body_excerpt": body_excerpt,
        "known_context": known_context or {},
    }


def _recipient_priority_payload(recipients: list[str]) -> dict[str, Any]:
    primary_recipient = recipients[0] if recipients else ""
    return {
        "primary_recipient": primary_recipient,
        "primary_recipient_is_internal": _is_internal_recipient(primary_recipient),
        "ordered_recipients_note": (
            "Le premier destinataire prime; les destinataires Balz Metal suivants "
            "sont des copies internes."
        ),
    }


def _is_internal_recipient(recipient: str) -> bool:
    _display_name, email = split_contact(recipient)
    domain = email_domain(email)
    if domain is None:
        return False
    return is_internal_domain(domain)
