from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from mailflow.classifier.decision_engine import ArchiveState, decide_archive
from mailflow.classifier.routing_context import (
    ResolvedCounterparty,
    RoutingDirectoryProtocol,
    build_routing_context,
    resolve_counterparty,
)
from mailflow.core.correspondence_hierarchy import apply_correspondence_hierarchy
from mailflow.models import (
    AiMailClassification,
    AiMode,
    ClassificationResult,
    InterlocutorType,
    MailMetadata,
    PreviewAction,
    PreviewRow,
    RoutingCategory,
    RuleClassification,
    VerifiedRoutingExample,
)


class AiClassifierProtocol(Protocol):
    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        ...


PreviewProgressCallback = Callable[[int, int], None]


def should_call_ai(*, ai_mode: AiMode) -> bool:
    return ai_mode != AiMode.DISABLED


class ClassificationPipeline:
    def __init__(
        self,
        *,
        projects_root: Path,
        archive_state: ArchiveState | None = None,
        ai_mode: AiMode = AiMode.AMBIGUOUS_ONLY,
        ai_classifier: AiClassifierProtocol | None = None,
        decision_confidence_threshold: float = 0.80,
        include_body_for_ai: bool = True,
        privacy_mask_phone_numbers: bool = False,
        organization_directory: RoutingDirectoryProtocol | None = None,
        verified_examples: list[VerifiedRoutingExample] | None = None,
    ) -> None:
        self.projects_root = projects_root
        self.archive_state = archive_state
        self.ai_mode = ai_mode
        self.ai_classifier = ai_classifier
        self.decision_confidence_threshold = decision_confidence_threshold
        self.include_body_for_ai = include_body_for_ai
        self.privacy_mask_phone_numbers = privacy_mask_phone_numbers
        self.organization_directory = organization_directory
        self.verified_examples = verified_examples or []

    def add_verified_example(self, example: VerifiedRoutingExample) -> None:
        self.verified_examples = [
            current
            for current in self.verified_examples
            if not (
                current.project_number == example.project_number
                and current.subject == example.subject
                and current.organization_name == example.organization_name
            )
        ]
        self.verified_examples.append(example)

    def preview(
        self,
        mails: list[MailMetadata],
        *,
        progress_callback: PreviewProgressCallback | None = None,
    ) -> list[PreviewRow]:
        indexed_mails = list(enumerate(mails))
        chronological = sorted(indexed_mails, key=lambda item: item[1].sent_at)
        rows_by_index: dict[int, PreviewRow] = {}
        history_by_company: dict[tuple[str, str], list[dict[str, str]]] = {}
        total = len(mails)
        for progress_index, (original_index, mail) in enumerate(chronological, start=1):
            counterparty = resolve_counterparty(mail, self.organization_directory)
            history_key = (mail.project_number, counterparty.history_key)
            history = history_by_company.setdefault(history_key, [])
            row = self.preview_one(mail, counterparty=counterparty, history=history)
            rows_by_index[original_index] = row
            if row.classification.ai is not None:
                history.append(_history_item(row.classification.ai, mail))
            if progress_callback is not None:
                progress_callback(progress_index, total)

        ordered_rows = [rows_by_index[index] for index in range(len(mails))]
        return apply_correspondence_hierarchy(
            ordered_rows,
            projects_root=self.projects_root,
            organization_directory=self.organization_directory,
        )

    def preview_one(
        self,
        mail: MailMetadata,
        *,
        counterparty: ResolvedCounterparty | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> PreviewRow:
        resolved = counterparty or resolve_counterparty(mail, self.organization_directory)
        ai = self._classify_with_ai(mail, resolved, history or [])
        rule = _neutral_rule()
        decision = decide_archive(
            mail,
            projects_root=self.projects_root,
            rule=rule,
            ai=ai,
            archive_state=self.archive_state,
            confidence_threshold=self.decision_confidence_threshold,
        )
        return PreviewRow(
            mail=mail,
            classification=ClassificationResult(rule=rule, ai=ai),
            decision=decision,
            action=action_from_decision(
                archive=decision.archive,
                requires_review=decision.requires_review,
            ),
        )

    def _classify_with_ai(
        self,
        mail: MailMetadata,
        counterparty: ResolvedCounterparty,
        history: list[dict[str, str]],
    ) -> AiMailClassification | None:
        if not should_call_ai(ai_mode=self.ai_mode) or self.ai_classifier is None:
            return None
        context = build_routing_context(
            mail,
            counterparty,
            history=history,
            verified_examples=self.verified_examples,
        )
        try:
            result = self.ai_classifier.classify(
                mail,
                include_body=self.include_body_for_ai,
                privacy_mask_phone_numbers=self.privacy_mask_phone_numbers,
                known_context=context,
            )
        except Exception:
            return None
        return apply_routing_guardrails(result, counterparty)


def apply_routing_guardrails(
    ai: AiMailClassification,
    counterparty: ResolvedCounterparty,
) -> AiMailClassification:
    updates: dict[str, Any] = {}
    reason_notes: list[str] = []
    role = counterparty.role

    if counterparty.organization_locked:
        updates["organization_name"] = counterparty.organization_name
    else:
        updates["requires_review"] = True
        reason_notes.append("Entreprise non confirmee dans l'annuaire.")

    if role == InterlocutorType.CLIENT:
        updates["organization_role"] = InterlocutorType.CLIENT.value
        if ai.category != RoutingCategory.CORRESPONDANCE.value:
            updates["category"] = RoutingCategory.CORRESPONDANCE.value
            reason_notes.append("Role client: Correspondance imposee.")
    elif role == InterlocutorType.FOURNISSEUR:
        updates["organization_role"] = InterlocutorType.FOURNISSEUR.value
        if ai.category == RoutingCategory.CORRESPONDANCE.value:
            updates["requires_review"] = True
            reason_notes.append("Un fournisseur doit etre classe en demande de prix ou commande.")
    else:
        updates["organization_role"] = InterlocutorType.INCONNU.value
        updates["requires_review"] = True
        reason_notes.append("Role projet client/fournisseur a confirmer dans l'annuaire.")

    if ai.confidence < 0.80:
        updates["requires_review"] = True
    if reason_notes:
        updates["reason"] = _append_reason(ai.reason, reason_notes)
    return ai.model_copy(update=updates)


def action_from_decision(*, archive: bool, requires_review: bool) -> PreviewAction:
    if requires_review:
        return PreviewAction.REVIEW
    if archive:
        return PreviewAction.ARCHIVE
    return PreviewAction.IGNORE


def _neutral_rule() -> RuleClassification:
    return RuleClassification(
        suggested_type=None,
        suggested_interlocutor=None,
        likely_archive=None,
        confidence=0.0,
        matched_rules=[],
        matched_terms=[],
    )


def _history_item(ai: AiMailClassification, mail: MailMetadata) -> dict[str, str]:
    return {
        "sent_at": mail.sent_at.isoformat(),
        "direction": mail.direction.value,
        "subject": mail.subject[:160],
        "category": ai.category,
        "summary": ai.short_summary,
        "review": "yes" if ai.requires_review else "no",
    }


def _append_reason(reason: str, notes: list[str]) -> str:
    result = " ".join([reason.strip(), *notes]).strip()
    return result[:200]
