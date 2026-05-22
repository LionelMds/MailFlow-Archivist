from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from mailflow.core.correspondence_hierarchy import (
    SUPPLIER_ORDER_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
    company_folder_for_row,
)
from mailflow.models import Direction, InterlocutorType, MailType, PreviewRow

ISSUE_PATTERNS = (
    r"\bprobleme\b",
    r"\breclamation\b",
    r"\breserve\b",
    r"\bnon[- ]conform",
    r"\berreur\b",
    r"\bretard\b",
    r"\burgent\b",
    r"\bbloqu",
    r"\bannul",
    r"\bclaim\b",
    r"\bcomplaint\b",
    r"\bissue\b",
    r"\bdelay\b",
    r"\bwrong\b",
    r"\bmangel\b",
    r"\breklamation\b",
    r"\bverzug\b",
    r"\britardo\b",
    r"\breclamo\b",
)


@dataclass(frozen=True)
class ProjectDigest:
    project_number: str
    mail_count: int
    sent_count: int
    received_count: int
    first_date: datetime | None
    last_date: datetime | None
    main_companies: tuple[str, ...] = ()
    global_points: tuple[str, ...] = ()
    client_points: tuple[str, ...] = ()
    supplier_points: tuple[str, ...] = ()
    order_points: tuple[str, ...] = ()
    issue_points: tuple[str, ...] = ()

    @property
    def has_rows(self) -> bool:
        return self.mail_count > 0

    @property
    def has_useful_sections(self) -> bool:
        return bool(
            self.global_points
            or self.client_points
            or self.supplier_points
            or self.order_points
            or self.issue_points
        )


@dataclass
class _CompanyStats:
    company: str
    rows: list[PreviewRow] = field(default_factory=list)

    @property
    def request_count(self) -> int:
        return sum(
            row.decision.target_relative_folder.startswith(SUPPLIER_REQUEST_FOLDER)
            for row in self.rows
        )

    @property
    def order_count(self) -> int:
        return sum(
            row.decision.target_relative_folder.startswith(SUPPLIER_ORDER_FOLDER)
            for row in self.rows
        )


def build_project_digest(rows: Sequence[PreviewRow]) -> ProjectDigest:
    ordered_rows = sorted(rows, key=lambda row: (row.mail.sent_at, row.mail.entry_id))
    if not ordered_rows:
        return ProjectDigest(
            project_number="",
            mail_count=0,
            sent_count=0,
            received_count=0,
            first_date=None,
            last_date=None,
        )

    project_number = ordered_rows[0].mail.project_number
    sent_count = sum(row.mail.direction == Direction.SENT for row in ordered_rows)
    received_count = len(ordered_rows) - sent_count
    companies = _top_companies(ordered_rows)
    return ProjectDigest(
        project_number=project_number,
        mail_count=len(ordered_rows),
        sent_count=sent_count,
        received_count=received_count,
        first_date=ordered_rows[0].mail.sent_at,
        last_date=ordered_rows[-1].mail.sent_at,
        main_companies=tuple(companies),
        global_points=tuple(
            _global_points(
                ordered_rows,
                sent_count=sent_count,
                received_count=received_count,
                companies=companies,
            )
        ),
        client_points=tuple(_client_points(ordered_rows)),
        supplier_points=tuple(_supplier_points(ordered_rows)),
        order_points=tuple(_order_points(ordered_rows)),
        issue_points=tuple(_issue_points(ordered_rows)),
    )


def _global_points(
    rows: Sequence[PreviewRow],
    *,
    sent_count: int,
    received_count: int,
    companies: list[str],
) -> list[str]:
    first_date = rows[0].mail.sent_at
    last_date = rows[-1].mail.sent_at
    points = [
        (
            f"{len(rows)} mails analyses du {first_date:%d.%m.%Y} au "
            f"{last_date:%d.%m.%Y} ({sent_count} envoyes, {received_count} recus)."
        )
    ]
    if companies:
        points.append(f"Interlocuteurs principaux: {', '.join(companies[:6])}.")
    latest = _dedupe(
        _row_point(row, include_company=True)
        for row in reversed(rows)
        if _is_informative_row(row)
    )
    if latest:
        points.append(f"Dernier point marquant: {latest[0]}")
    return points


def _client_points(rows: Sequence[PreviewRow]) -> list[str]:
    client_rows = [
        row
        for row in rows
        if row.decision.interlocutor == InterlocutorType.CLIENT
    ]
    return _dedupe(
        _row_point(row, include_company=True)
        for row in reversed(client_rows)
        if _is_informative_row(row)
    )[:6]


def _supplier_points(rows: Sequence[PreviewRow]) -> list[str]:
    supplier_groups: dict[str, _CompanyStats] = {}
    for row in rows:
        if not _is_supplier_row(row):
            continue
        company = _company_for_row(row)
        stats = supplier_groups.get(company)
        if stats is None or not stats.company:
            stats = _CompanyStats(company=company)
            supplier_groups[company] = stats
        stats.rows.append(row)

    points = []
    for stats in sorted(
        supplier_groups.values(),
        key=lambda item: (-len(item.rows), item.company.casefold()),
    ):
        latest = max(stats.rows, key=lambda row: (row.mail.sent_at, row.mail.entry_id))
        parts = [f"{len(stats.rows)} echange(s)"]
        if stats.request_count:
            parts.append(f"{stats.request_count} demande(s) de prix/offre")
        if stats.order_count:
            parts.append(f"{stats.order_count} commande(s)/suivi commande")
        points.append(
            f"{stats.company}: {', '.join(parts)}. Dernier point: {_summary_for_row(latest)}"
        )
    return points[:8]


def _order_points(rows: Sequence[PreviewRow]) -> list[str]:
    order_rows = [
        row
        for row in rows
        if row.decision.target_relative_folder.startswith(SUPPLIER_ORDER_FOLDER)
        or (
            row.decision.interlocutor == InterlocutorType.FOURNISSEUR
            and row.decision.mail_type
            in {MailType.COMMANDE, MailType.FACTURE, MailType.LIVRAISON}
        )
    ]
    return _dedupe(
        f"{_company_for_row(row)}: {_summary_for_row(row)} ({row.mail.sent_at:%d.%m.%Y})"
        for row in reversed(order_rows)
        if _is_informative_row(row)
    )[:8]


def _issue_points(rows: Sequence[PreviewRow]) -> list[str]:
    issues = []
    for row in rows:
        text = _issue_search_text(row)
        if not _contains_issue_signal(text):
            continue
        issues.append(
            f"{_company_for_row(row)}: {_issue_summary_for_row(row)} "
            f"({row.mail.sent_at:%d.%m.%Y})"
        )
    return _dedupe(reversed(issues))[:8]


def _top_companies(rows: Sequence[PreviewRow]) -> list[str]:
    counts = Counter(_company_for_row(row) for row in rows)
    counts.pop("Interlocuteur inconnu", None)
    return [
        company
        for company, _count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
    ][:8]


def _row_point(row: PreviewRow, *, include_company: bool) -> str:
    summary = _summary_for_row(row)
    if not include_company:
        return summary
    return f"{_company_for_row(row)}: {summary}"


def _summary_for_row(row: PreviewRow) -> str:
    ai = row.classification.ai
    if ai is not None and ai.short_summary.strip():
        return _shorten(ai.short_summary.strip(), 150)
    if row.mail.subject.strip():
        return _shorten(row.mail.subject.strip(), 150)
    return _shorten(_first_sentence(row.mail.body_excerpt), 150)


def _issue_summary_for_row(row: PreviewRow) -> str:
    ai = row.classification.ai
    candidates = [row.mail.subject, row.mail.body_excerpt, row.decision.reason]
    if ai is not None:
        candidates.extend([ai.short_summary, ai.reason])
    for candidate in candidates:
        sentence = _sentence_with_issue(candidate)
        if sentence is not None:
            return _shorten(sentence, 150)
    return _summary_for_row(row)


def _sentence_with_issue(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    for sentence in sentences:
        if _contains_issue_signal(sentence):
            return sentence
    if _contains_issue_signal(cleaned):
        return cleaned
    return None


def _first_sentence(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return "(Aucun resume disponible)"
    match = re.search(r"(.+?[.!?])(?:\s|$)", cleaned)
    return match.group(1) if match else cleaned


def _company_for_row(row: PreviewRow) -> str:
    folder_company = _company_from_target_folder(row.decision.target_relative_folder)
    if folder_company is not None:
        return folder_company
    return company_folder_for_row(row)


def _company_from_target_folder(relative_folder: str) -> str | None:
    parts = [part for part in relative_folder.replace("\\", "/").split("/") if part]
    if len(parts) <= 1:
        return None
    if parts[0] == "Fournisseurs" and len(parts) >= 3:
        return parts[-1]
    if parts[0] == "Correspondance":
        return parts[-1]
    return None


def _is_supplier_row(row: PreviewRow) -> bool:
    return (
        row.decision.interlocutor == InterlocutorType.FOURNISSEUR
        or row.decision.target_relative_folder.startswith("Fournisseurs/")
    )


def _is_informative_row(row: PreviewRow) -> bool:
    return row.decision.mail_type != MailType.INUTILE_OU_FAIBLE_VALEUR


def _issue_search_text(row: PreviewRow) -> str:
    ai = row.classification.ai
    ai_text = "" if ai is None else f"{ai.short_summary} {ai.reason}"
    return " ".join(
        [
            row.mail.subject,
            row.mail.body_excerpt,
            row.decision.reason,
            ai_text,
        ]
    )


def _contains_issue_signal(value: str) -> bool:
    normalized = _normalize(value)
    return any(re.search(pattern, normalized) for pattern in ISSUE_PATTERNS)


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            continue
        key = _normalize(cleaned)
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _shorten(value: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _normalize(value: str) -> str:
    normalized = value.casefold()
    replacements = {
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "à": "a",
        "â": "a",
        "ä": "a",
        "ù": "u",
        "û": "u",
        "ü": "u",
        "ô": "o",
        "ö": "o",
        "î": "i",
        "ï": "i",
        "ç": "c",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized
