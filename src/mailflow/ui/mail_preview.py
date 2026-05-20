from __future__ import annotations

import html
import unicodedata
from dataclasses import dataclass

from mailflow.models import Direction, PreviewRow

HIGHLIGHT_STYLE = "background-color: #fff176; color: #1f2933; font-weight: 600;"


@dataclass(frozen=True)
class HighlightRange:
    start: int
    end: int


def preview_row_to_html(row: PreviewRow) -> str:
    text = preview_row_to_text(row)
    terms = classification_highlight_terms(row)
    highlighted = highlight_terms_as_html(text, terms)
    reason = html.escape(row.decision.reason)
    return (
        "<div style='font-family: Segoe UI, Arial, sans-serif; font-size: 10pt;'>"
        f"{highlighted}"
        f"<p><b>Raison:</b> {reason}</p>"
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
    terms = list(row.classification.rule.matched_terms)
    if row.classification.ai is not None:
        terms.extend([row.classification.ai.mail_type, row.classification.ai.interlocutor])
    return _dedupe_terms(terms)


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
