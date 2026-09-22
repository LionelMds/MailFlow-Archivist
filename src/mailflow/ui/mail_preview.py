from __future__ import annotations

import html
import unicodedata
from dataclasses import dataclass

from mailflow.models import Direction, PreviewAction, PreviewRow

HIGHLIGHT_STYLE = "background-color: #fff176; color: #1f2933; font-weight: 600;"


@dataclass(frozen=True)
class HighlightRange:
    start: int
    end: int


def preview_row_to_html(row: PreviewRow) -> str:
    mail = row.mail
    terms = classification_highlight_terms(row)
    subject = highlight_terms_as_html(mail.subject or "(Sans objet)", terms)
    body = highlight_terms_as_html(mail.body_excerpt or "Aucun extrait disponible.", terms)
    reason = html.escape(row.decision.reason)
    ai_details = ai_decision_html(row)
    direction = "Envoyé" if mail.direction == Direction.SENT else "Reçu"
    sender = html.escape(mail.sender_name or mail.sender_email or "Expéditeur inconnu")
    sender_address = html.escape(mail.sender_email)
    recipients = html.escape(", ".join(mail.recipients) or "Non renseignés")
    attachments = html.escape(", ".join(mail.attachment_names) or "Aucune")
    target = html.escape(row.decision.target_relative_folder)
    return (
        "<div style='font-family: Segoe UI, Arial, sans-serif; font-size:10pt; color:#243247;'>"
        f"<p style='color:#52647a;'>{html.escape(mail.project_number)} · "
        f"{mail.sent_at:%d.%m.%Y à %H:%M} · {direction}</p>"
        f"<h2 style='font-size:15pt; color:#182c40; margin-bottom:12px;'>{subject}</h2>"
        f"<p><b>De :</b> {sender}<br><span style='color:#52647a;'>{sender_address}</span><br>"
        f"<b>À :</b> {recipients}</p>"
        f"<p><b>Pièces jointes :</b> {attachments}</p>"
        "<hr style='color:#dce5ec;'>"
        f"<p style='line-height:145%;'>{body}</p>"
        "<hr style='color:#dce5ec;'>"
        f"<p><b>Destination :</b> {target}<br><b>Raison:</b> {reason}</p>"
        f"{ai_details}"
        "</div>"
    )


def preview_row_to_text(row: PreviewRow) -> str:
    mail = row.mail
    direction = "Envoye" if mail.direction == Direction.SENT else "Recu"
    attachments = ", ".join(mail.attachment_names) if mail.attachment_names else "Aucune"
    recipients = ", ".join(mail.recipients) if mail.recipients else "-"
    return "\n".join(
        [
            f"Projet: {mail.project_number}",
            f"Date: {mail.sent_at:%Y-%m-%d %H:%M}",
            f"Sens: {direction}",
            f"Expediteur: {mail.sender_name or mail.sender_email}",
            f"Destinataires: {recipients}",
            f"Sujet: {mail.subject}",
            f"Pieces jointes: {attachments}",
            "",
            mail.body_excerpt or "(Aucun extrait disponible)",
        ]
    )


def classification_highlight_terms(row: PreviewRow) -> list[str]:
    ai = row.classification.ai
    return [] if ai is None else _dedupe_terms(list(ai.evidence))


def ai_decision_html(row: PreviewRow) -> str:
    ai = row.classification.ai
    if ai is None:
        if row.classification.ai_error:
            return (
                "<p style='color:#8a4b16; background:#fff4e3; padding:8px;'>"
                f"<b>Analyse à relancer :</b> {html.escape(row.classification.ai_error)}</p>"
            )
        if row.action == PreviewAction.ARCHIVED:
            return (
                "<p style='color:#52647a;'>Mail déjà archivé. "
                "Aucun nouvel appel IA nécessaire.</p>"
            )
        return (
            "<p style='color:#5f6b7a;'>"
            "<b>Décision IA :</b> IA non appelée pour cette ligne."
            "</p>"
        )
    action = "À vérifier" if ai.requires_review else "Classement proposé"
    category = html.escape(ai.category)
    role = html.escape(ai.organization_role)
    short_summary = html.escape(ai.short_summary)
    reason = html.escape(ai.reason)
    return (
        "<div style='padding:8px 10px; margin:12px 0; background:#edf3f9;'>"
        f"<p><b>Décision IA :</b> {category}</p>"
        f"<p style='color:#52647a;'>{action} · {ai.confidence:.0%} de confiance · {role}</p>"
        f"<p><b>Résumé :</b><br>{short_summary}</p>"
        f"<p><b>Pourquoi :</b><br>{reason}</p>"
        "</div>"
    )


def highlight_terms_as_html(text: str, terms: list[str]) -> str:
    ranges = _merge_ranges(_find_highlight_ranges(text, terms))
    if not ranges:
        return _escaped_text_to_html(text)

    chunks = []
    cursor = 0
    for item in ranges:
        chunks.append(_escaped_text_to_html(text[cursor:item.start]))
        chunks.append(
            f"<span style='{HIGHLIGHT_STYLE}'>"
            f"{_escaped_text_to_html(text[item.start:item.end])}"
            "</span>"
        )
        cursor = item.end
    chunks.append(_escaped_text_to_html(text[cursor:]))
    return "".join(chunks)


def _find_highlight_ranges(text: str, terms: list[str]) -> list[HighlightRange]:
    normalized_text, mapping = _normalize_with_mapping(text)
    ranges = []
    for term in terms:
        normalized_term = _normalize_text(term)
        if not normalized_term:
            continue
        start = normalized_text.find(normalized_term)
        while start >= 0:
            end = start + len(normalized_term)
            if start < len(mapping) and end - 1 < len(mapping):
                ranges.append(HighlightRange(start=mapping[start], end=mapping[end - 1] + 1))
            start = normalized_text.find(normalized_term, start + len(normalized_term))
    return ranges


def _merge_ranges(ranges: list[HighlightRange]) -> list[HighlightRange]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    merged = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        if item.start <= previous.end:
            merged[-1] = HighlightRange(previous.start, max(previous.end, item.end))
        else:
            merged.append(item)
    return merged


def _normalize_with_mapping(value: str) -> tuple[str, list[int]]:
    normalized_chars = []
    mapping = []
    for index, char in enumerate(value):
        decomposed = unicodedata.normalize("NFKD", char.casefold())
        for normalized_char in decomposed:
            if unicodedata.combining(normalized_char):
                continue
            normalized_chars.append(normalized_char)
            mapping.append(index)
    return "".join(normalized_chars), mapping


def _normalize_text(value: str) -> str:
    normalized, _mapping = _normalize_with_mapping(value)
    return normalized


def _escaped_text_to_html(value: str) -> str:
    return html.escape(value).replace("\n", "<br>")


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen = set()
    result = []
    for term in terms:
        cleaned = term.strip()
        normalized = _normalize_text(cleaned)
        if not cleaned or normalized in seen:
            continue
        seen.add(normalized)
        result.append(cleaned)
    return result
