"""Measure AI routing accuracy against mails a person already classified.

The reference answers come from the local SQLite file: manual corrections first,
then archived mails (their folder was accepted by a person when archiving). Mail
contents are not stored for this: the caller re-reads each mail from Outlook. The
report keeps identifiers and categories only, never subjects or bodies.
"""

from __future__ import annotations

import sqlite3
import statistics
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mailflow.classifier.decision_engine import DEFAULT_CONFIDENCE_THRESHOLD
from mailflow.classifier.pipeline import AiClassifierProtocol, apply_routing_guardrails
from mailflow.classifier.routing_context import (
    RoutingDirectoryProtocol,
    build_routing_context,
    resolve_counterparty,
)
from mailflow.models import (
    InterlocutorType,
    MailMetadata,
    MailType,
    RoutingCategory,
    routing_category_for_mail_type,
)

CaseSource = Literal["correction", "archive"]
MailFetcher = Callable[[str], MailMetadata | None]

CATEGORIES = tuple(category.value for category in RoutingCategory)
_ARCHIVE_FOLDERS = {
    "correspondance": (RoutingCategory.CORRESPONDANCE, InterlocutorType.CLIENT),
    "fournisseurs/demande de prix": (
        RoutingCategory.DEMANDE_DE_PRIX, InterlocutorType.FOURNISSEUR,
    ),
    "fournisseurs/commande": (RoutingCategory.COMMANDE, InterlocutorType.FOURNISSEUR),
}
_BUSINESS_ROLES = {InterlocutorType.CLIENT.value, InterlocutorType.FOURNISSEUR.value}
_UNROUTED_TYPES = {MailType.A_VERIFIER.value, MailType.INUTILE_OU_FAIBLE_VALEUR.value}


@dataclass(frozen=True)
class EvaluationCase:
    entry_id: str
    project_number: str
    expected_category: RoutingCategory
    expected_role: InterlocutorType
    source: CaseSource


@dataclass(frozen=True)
class CaseResult:
    case: EvaluationCase
    seconds: float = 0.0
    predicted_category: str | None = None
    requires_review: bool | None = None
    error: str | None = None
    missing_in_outlook: bool = False
    inconsistent_reference: bool = False
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def answered(self) -> bool:
        return self.predicted_category is not None

    @property
    def correct(self) -> bool:
        return self.predicted_category == self.case.expected_category.value


@dataclass
class EvaluationSummary:
    planned: int
    answered: int = 0
    missing_in_outlook: int = 0
    inconsistent_references: int = 0
    errors: int = 0
    correct: int = 0
    automatic_correct: int = 0
    automatic_wrong: int = 0
    sent_to_review: int = 0
    median_seconds: float | None = None
    p90_seconds: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    by_source: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.answered if self.answered else None


def load_reference_cases(db_path: Path) -> list[EvaluationCase]:
    """Read reference answers without writing to the database."""
    if not db_path.exists():
        return []
    connection = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        cases: dict[str, EvaluationCase] = {}
        if "archived_mails" in tables:
            for entry_id, project, folder in connection.execute(
                "SELECT outlook_entry_id, project_number, target_folder FROM archived_mails"
            ):
                case = _case_from_archive(str(entry_id), str(project), str(folder))
                if case is not None:
                    cases[case.entry_id] = case
        if "manual_learning_signals" in tables:
            # The latest correction of a mail wins over its archive and older corrections.
            for entry_id, project, mail_type, role, folder in connection.execute(
                """
                SELECT mail_id, project_number, selected_mail_type, selected_interlocutor,
                       selected_target_folder
                FROM manual_learning_signals
                WHERE id IN (SELECT MAX(id) FROM manual_learning_signals GROUP BY mail_id)
                """
            ):
                if role not in _BUSINESS_ROLES or mail_type in _UNROUTED_TYPES:
                    cases.pop(str(entry_id), None)
                    continue
                case = EvaluationCase(
                    entry_id=str(entry_id),
                    project_number=str(project),
                    expected_category=routing_category_for_mail_type(MailType(mail_type)),
                    expected_role=InterlocutorType(role),
                    source="correction",
                )
                if _correction_is_consistent(case, str(folder or "")):
                    cases[case.entry_id] = case
                # Otherwise keep the archive, if any: it records where the mail really went.
    finally:
        connection.close()
    return list(cases.values())


def evaluate_case(
    case: EvaluationCase,
    *,
    fetch_mail: MailFetcher,
    classifier: AiClassifierProtocol,
    directory: RoutingDirectoryProtocol | None,
    include_body: bool = True,
    privacy_mask_phone_numbers: bool = False,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> CaseResult:
    mail = fetch_mail(case.entry_id)
    if mail is None:
        return CaseResult(case=case, missing_in_outlook=True)
    counterparty = resolve_counterparty(mail, directory)
    if (
        counterparty.role in {InterlocutorType.CLIENT, InterlocutorType.FOURNISSEUR}
        and counterparty.role != case.expected_role
    ):
        # The directory now gives the company another role: the guardrails would
        # forbid the reference answer, so it no longer measures the model.
        return CaseResult(case=case, inconsistent_reference=True)
    # No history and no verified examples: an example built from this very
    # correction would hand the model its answer.
    context = build_routing_context(mail, counterparty, history=[], verified_examples=[])
    started = time.perf_counter()
    try:
        answer = classifier.classify(
            mail,
            include_body=include_body,
            privacy_mask_phone_numbers=privacy_mask_phone_numbers,
            known_context=context,
        )
    except Exception as exc:
        return CaseResult(
            case=case,
            seconds=time.perf_counter() - started,
            error=type(exc).__name__,
        )
    seconds = time.perf_counter() - started
    guarded = apply_routing_guardrails(
        answer, counterparty, confidence_threshold=confidence_threshold,
    )
    usage = getattr(classifier, "last_usage", None) or (0, 0)
    return CaseResult(
        case=case,
        seconds=seconds,
        predicted_category=guarded.category,
        requires_review=guarded.requires_review,
        input_tokens=int(usage[0]),
        output_tokens=int(usage[1]),
    )


def summarize(results: Sequence[CaseResult], *, planned: int | None = None) -> EvaluationSummary:
    summary = EvaluationSummary(planned=len(results) if planned is None else planned)
    summary.confusion = {expected: dict.fromkeys(CATEGORIES, 0) for expected in CATEGORIES}
    per_source: Counter[tuple[str, bool]] = Counter()
    durations = []
    for result in results:
        summary.input_tokens += result.input_tokens
        summary.output_tokens += result.output_tokens
        if result.missing_in_outlook:
            summary.missing_in_outlook += 1
            continue
        if result.inconsistent_reference:
            summary.inconsistent_references += 1
            continue
        if not result.answered:
            summary.errors += 1
            continue
        durations.append(result.seconds)
        summary.answered += 1
        summary.correct += result.correct
        per_source[(result.case.source, result.correct)] += 1
        expected = result.case.expected_category.value
        summary.confusion[expected][str(result.predicted_category)] += 1
        if result.requires_review:
            summary.sent_to_review += 1
        elif result.correct:
            summary.automatic_correct += 1
        else:
            summary.automatic_wrong += 1
    if durations:
        summary.median_seconds = statistics.median(durations)
        ordered = sorted(durations)
        summary.p90_seconds = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    for source in ("correction", "archive"):
        correct = per_source[(source, True)]
        total = correct + per_source[(source, False)]
        if total:
            summary.by_source[source] = (correct, total)
    return summary


def format_summary(
    summary: EvaluationSummary,
    *,
    engine: str,
    price_per_million: tuple[float, float] | None = None,
) -> str:
    def share(count: int) -> str:
        return f"{count} ({count / summary.answered:.0%})" if summary.answered else str(count)

    accuracy = summary.accuracy
    lines = [
        f"Moteur : {engine}",
        f"Mails prevus : {summary.planned} | analyses : {summary.answered} | "
        f"introuvables dans Outlook : {summary.missing_in_outlook} | "
        f"erreurs IA : {summary.errors}",
        f"References ecartees (role de l'annuaire different) : "
        f"{summary.inconsistent_references}",
        "",
        "Precision de la categorie : "
        + ("-" if accuracy is None else f"{accuracy:.1%} ({summary.correct}/{summary.answered})"),
    ]
    for source, (correct, total) in summary.by_source.items():
        label = "corrections manuelles" if source == "correction" else "mails archives"
        lines.append(f"  - {label} : {correct / total:.1%} ({correct}/{total})")
    lines += [
        "",
        f"Classes automatiquement et justes : {share(summary.automatic_correct)}",
        f"Classes automatiquement mais FAUX : {share(summary.automatic_wrong)}",
        f"Envoyes en verification : {share(summary.sent_to_review)}",
        "",
        "Matrice (lignes = attendu, colonnes = propose) :",
        "  " + " | ".join(f"{name:>15}" for name in ("", *CATEGORIES)),
    ]
    for expected, row in summary.confusion.items():
        lines.append(
            "  " + " | ".join(f"{value:>15}" for value in (expected, *map(str, row.values())))
        )
    if summary.median_seconds is not None and summary.p90_seconds is not None:
        lines += [
            "",
            f"Temps par mail : median {summary.median_seconds:.1f} s, "
            f"90 % sous {summary.p90_seconds:.1f} s",
        ]
    if summary.input_tokens or summary.output_tokens:
        lines.append(
            f"Tokens : {summary.input_tokens} en entree, {summary.output_tokens} en sortie"
        )
        if price_per_million is not None:
            cost = (
                summary.input_tokens * price_per_million[0]
                + summary.output_tokens * price_per_million[1]
            ) / 1_000_000
            per_mail = cost / summary.answered if summary.answered else 0.0
            lines.append(f"Cout estime : {cost:.4f} (soit {per_mail:.5f} par mail)")
    return "\n".join(lines)


def result_to_json(result: CaseResult) -> dict[str, Any]:
    """One report line: identifiers and categories only, no mail content."""
    return {
        "entry_id": result.case.entry_id,
        "project_number": result.case.project_number,
        "source": result.case.source,
        "expected_category": result.case.expected_category.value,
        "expected_role": result.case.expected_role.value,
        "predicted_category": result.predicted_category,
        "requires_review": result.requires_review,
        "correct": result.correct if result.answered else None,
        "missing_in_outlook": result.missing_in_outlook,
        "inconsistent_reference": result.inconsistent_reference,
        "error": result.error,
        "seconds": round(result.seconds, 3),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }


def _correction_is_consistent(case: EvaluationCase, folder: str) -> bool:
    """Reject corrections made under older rules or whose type and folder disagree."""
    if not follows_business_rules(case.expected_category, case.expected_role):
        return False
    folder_case = _case_from_archive(case.entry_id, case.project_number, folder)
    return folder_case is None or folder_case.expected_category == case.expected_category


def follows_business_rules(category: RoutingCategory, role: InterlocutorType) -> bool:
    """A client is always Correspondance; a supplier never is."""
    if role == InterlocutorType.CLIENT:
        return category == RoutingCategory.CORRESPONDANCE
    if role == InterlocutorType.FOURNISSEUR:
        return category != RoutingCategory.CORRESPONDANCE
    return False


def _case_from_archive(entry_id: str, project: str, folder: str) -> EvaluationCase | None:
    parts = [part for part in folder.replace("\\", "/").split("/") if part]
    for depth in (2, 1):
        match = _ARCHIVE_FOLDERS.get("/".join(parts[:depth]).casefold())
        if match is not None:
            category, role = match
            return EvaluationCase(
                entry_id=entry_id,
                project_number=project,
                expected_category=category,
                expected_role=role,
                source="archive",
            )
    return None
