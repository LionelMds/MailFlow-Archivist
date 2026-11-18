"""Texts and rules of the review queue: one mail at a time, the decision beside it.

Nothing here imports Qt, so the queue behaviour is tested without a window.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass

from mailflow.core.correspondence_hierarchy import (
    CORRESPONDENCE_FOLDER,
    SUPPLIER_ORDER_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
)
from mailflow.models import (
    Direction,
    InterlocutorType,
    MailType,
    ManualClassificationUpdate,
    PreviewAction,
    PreviewRow,
)
from mailflow.ui.preview_table import ACTION_LABELS, DESTINATION_OPTIONS

ACTION_TAG_KINDS = {
    PreviewAction.REVIEW: "outline",
    PreviewAction.ARCHIVE: "accent",
    PreviewAction.IGNORE: "neutral",
    PreviewAction.ARCHIVED: "neutral",
}
ENGINE_LABELS = {"openai": "OpenAI", "ollama": "Ollama", "jev": "Jev"}
QUEUE_ROLES = (InterlocutorType.CLIENT, InterlocutorType.FOURNISSEUR)
MAIL_TYPE_BY_DESTINATION = {
    CORRESPONDENCE_FOLDER: MailType.CORRESPONDANCE_GENERALE,
    SUPPLIER_REQUEST_FOLDER: MailType.DEMANDE_DE_PRIX,
    SUPPLIER_ORDER_FOLDER: MailType.COMMANDE,
}
SHORTCUTS_HINT = "Raccourcis : C client · F fournisseur · Entrée valider"


@dataclass(frozen=True)
class QueueItemView:
    correspondent: str
    date_label: str
    subject: str
    tag_text: str
    tag_kind: str


@dataclass(frozen=True)
class QueueProgress:
    total: int
    review_count: int
    ready_count: int

    @property
    def treated_count(self) -> int:
        return self.total - self.review_count


@dataclass(frozen=True)
class DecisionView:
    kicker: str
    confidence: float
    summary: str
    reason: str


@dataclass(frozen=True)
class BulkSelectionView:
    editable_count: int
    archived_count: int
    companies: tuple[str, ...]

    @property
    def company_line(self) -> str:
        if len(self.companies) == 1:
            return f"Même entreprise : {self.companies[0]}"
        if not self.companies:
            return "Entreprise à confirmer"
        return f"{len(self.companies)} entreprises"


def queue_item_view(row: PreviewRow) -> QueueItemView:
    mail = row.mail
    return QueueItemView(
        correspondent=mail.sender_name or mail.sender_email or "Expéditeur inconnu",
        date_label=f"{mail.sent_at:%d.%m.%Y}",
        subject=mail.subject or "(Sans objet)",
        tag_text=ACTION_LABELS[row.action],
        tag_kind=ACTION_TAG_KINDS[row.action],
    )


def queue_progress(rows: Sequence[PreviewRow]) -> QueueProgress:
    return QueueProgress(
        total=len(rows),
        review_count=sum(row.action == PreviewAction.REVIEW for row in rows),
        ready_count=sum(row.action == PreviewAction.ARCHIVE for row in rows),
    )


def project_label(rows: Sequence[PreviewRow]) -> str:
    projects = sorted({row.mail.project_number for row in rows})
    if not projects:
        return "Aucun projet"
    return projects[0] if len(projects) == 1 else f"{len(projects)} projets"


def queue_header_text(rows: Sequence[PreviewRow]) -> str:
    if not rows:
        return "Aucun mail analysé"
    progress = queue_progress(rows)
    return (
        f"{project_label(rows)} · {progress.review_count} à vérifier · "
        f"{progress.treated_count} traités"
    )


def archive_button_text(ready_count: int) -> str:
    if ready_count <= 0:
        return "Archiver"
    return "Archiver 1 mail" if ready_count == 1 else f"Archiver {ready_count} mails"


def first_queue_index(rows: Sequence[PreviewRow]) -> int | None:
    """The mail to open first: the first one waiting for review, else the first one."""
    if not rows:
        return None
    for index, row in enumerate(rows):
        if row.action == PreviewAction.REVIEW:
            return index
    return 0


def next_review_index(rows: Sequence[PreviewRow], after: int) -> int | None:
    """The next mail waiting for review after `after`, wrapping around the queue."""
    count = len(rows)
    for offset in range(1, count + 1):
        index = (after + offset) % count
        if index != after and rows[index].action == PreviewAction.REVIEW:
            return index
    return None


def mail_meta_text(row: PreviewRow) -> str:
    mail = row.mail
    direction = "Envoyé" if mail.direction == Direction.SENT else "Reçu"
    return f"{mail.project_number} · {mail.sent_at:%d.%m.%Y à %H:%M} · {direction}"


def sender_html(row: PreviewRow) -> str:
    mail = row.mail
    name = html.escape(mail.sender_name or mail.sender_email or "Expéditeur inconnu")
    address = (
        f" &lt;{html.escape(mail.sender_email)}&gt;"
        if mail.sender_name and mail.sender_email
        else ""
    )
    recipients = html.escape(", ".join(mail.recipients) or "non renseigné")
    return f"<b>De</b> {name}{address} &nbsp; <b>À</b> {recipients}"


def attachments_text(row: PreviewRow) -> str:
    names = row.mail.attachment_names
    if not names:
        return "Aucune pièce jointe"
    return ", ".join(names)


def engine_label(provider: str) -> str:
    return ENGINE_LABELS.get(provider, provider)


def decision_view(row: PreviewRow, provider: str) -> DecisionView:
    classification = row.classification
    ai = classification.ai
    if ai is not None:
        return DecisionView(
            kicker=f"Proposition IA · {engine_label(provider)}",
            confidence=ai.confidence,
            summary=ai.short_summary,
            reason=ai.reason,
        )
    if row.action == PreviewAction.ARCHIVED:
        return DecisionView(
            kicker="Déjà archivé",
            confidence=row.decision.confidence,
            summary="Ce mail est déjà archivé. Son classement est conservé.",
            reason=row.decision.reason,
        )
    if classification.ai_error:
        return DecisionView(
            kicker="Analyse à relancer",
            confidence=row.decision.confidence,
            summary="L'IA n'a pas pu classer ce mail : il reste à vérifier.",
            reason=classification.ai_error,
        )
    return DecisionView(
        kicker="Proposition MailFlow",
        confidence=row.decision.confidence,
        summary="IA non appelée pour ce mail.",
        reason=row.decision.reason,
    )


def decision_text_html(view: DecisionView) -> str:
    parts = []
    if view.summary:
        parts.append(f"<b>Résumé</b><br>{html.escape(view.summary)}")
    if view.reason:
        parts.append(f"<b>Pourquoi</b><br>{html.escape(view.reason)}")
    return "<br><br>".join(parts)


def percent_html(value: float) -> str:
    return f"{value:.0%}".replace("%", "") + "<span style='font-size:30px'> %</span>"


def destination_category(row: PreviewRow) -> str | None:
    """Which of the three business folders the current decision targets, if any."""
    target = row.decision.target_relative_folder
    for option in sorted(DESTINATION_OPTIONS, key=len, reverse=True):
        if target == option or target.startswith(f"{option}/"):
            return option
    return None


def role_choice(row: PreviewRow) -> InterlocutorType | None:
    role = row.decision.interlocutor
    return role if role in QUEUE_ROLES else None


def validation_problem(destination: str | None, role: InterlocutorType | None) -> str | None:
    """Why the decision panel cannot validate yet, or None when it can."""
    if destination is None:
        return "Choisissez une destination."
    if role == InterlocutorType.FOURNISSEUR and destination == CORRESPONDENCE_FOLDER:
        return (
            "Un fournisseur ne va jamais en Correspondance : choisissez Demande de prix "
            "ou Commande."
        )
    if role == InterlocutorType.CLIENT and destination != CORRESPONDENCE_FOLDER:
        return "Un client va toujours en Correspondance."
    return None


def bulk_validation_problem(
    destination: str | None,
    role: InterlocutorType | None,
) -> str | None:
    """A group decision always names the role: one update carries one role for all."""
    if role is None:
        return "Choisissez le rôle de l'entreprise."
    return validation_problem(destination, role)


def destination_for_role(
    destination: str | None,
    role: InterlocutorType | None,
) -> str | None:
    """Follow the business rule when the role changes, like the manual dialog does."""
    if role == InterlocutorType.CLIENT:
        return CORRESPONDENCE_FOLDER
    if role == InterlocutorType.FOURNISSEUR and destination == CORRESPONDENCE_FOLDER:
        return None
    return destination


def build_queue_update(
    destination: str,
    role: InterlocutorType | None,
    fallback_role: InterlocutorType,
) -> ManualClassificationUpdate:
    return ManualClassificationUpdate(
        mail_type=MAIL_TYPE_BY_DESTINATION[destination],
        interlocutor=role or fallback_role,
        target_relative_folder=destination,
    )


def company_name(row: PreviewRow) -> str:
    """The company folder of a decision, else the sender as a readable fallback."""
    category = destination_category(row)
    target = row.decision.target_relative_folder
    if category is not None and target != category:
        return target[len(category) + 1:].split("/")[0]
    ai = row.classification.ai
    if ai is not None and ai.organization_name:
        return ai.organization_name
    return row.mail.sender_name or row.mail.sender_email or "Entreprise inconnue"


def bulk_selection_view(rows: Sequence[PreviewRow]) -> BulkSelectionView:
    editable = [row for row in rows if row.action != PreviewAction.ARCHIVED]
    companies = tuple(dict.fromkeys(company_name(row) for row in editable))
    return BulkSelectionView(
        editable_count=len(editable),
        archived_count=len(rows) - len(editable),
        companies=companies,
    )


def bulk_destination_text(destination: str | None, companies: Sequence[str]) -> str:
    if destination is None:
        return "À choisir"
    if len(companies) == 1:
        return f"{destination}/{companies[0]}"
    return f"{destination}/<entreprise de chaque mail>"


def role_text(role: InterlocutorType | None) -> str:
    if role == InterlocutorType.CLIENT:
        return "Client"
    if role == InterlocutorType.FOURNISSEUR:
        return "Fournisseur"
    return "Inchangé"
