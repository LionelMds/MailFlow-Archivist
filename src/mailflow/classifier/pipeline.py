from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mailflow.classifier.decision_engine import ArchiveState, decide_archive
from mailflow.classifier.rules_classifier import classify_mail
from mailflow.core.manual_review import (
    LearnedClassificationRule,
    LearnedMisleadingTerm,
    classify_with_learned_terms,
)
from mailflow.models import (
    AiMailClassification,
    AiMode,
    ClassificationResult,
    MailMetadata,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)


class AiClassifierProtocol(Protocol):
    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, str] | None = None,
    ) -> AiMailClassification:
        ...


def should_call_ai(rule: RuleClassification, *, ai_mode: AiMode, threshold: float) -> bool:
    if ai_mode == AiMode.DISABLED:
        return False
    if ai_mode == AiMode.ALL:
        return True
    return (
        rule.suggested_type is None
        or rule.likely_archive is None
        or rule.confidence < threshold
    )


class ClassificationPipeline:
    def __init__(
        self,
        *,
        projects_root: Path,
        archive_state: ArchiveState | None = None,
        ai_mode: AiMode = AiMode.AMBIGUOUS_ONLY,
        ai_classifier: AiClassifierProtocol | None = None,
        rule_confidence_threshold: float = 0.80,
        decision_confidence_threshold: float = 0.80,
        include_body_for_ai: bool = True,
        privacy_mask_phone_numbers: bool = False,
        learned_rules: list[LearnedClassificationRule] | None = None,
        misleading_terms: list[LearnedMisleadingTerm] | None = None,
    ) -> None:
        self.projects_root = projects_root
        self.archive_state = archive_state
        self.ai_mode = ai_mode
        self.ai_classifier = ai_classifier
        self.rule_confidence_threshold = rule_confidence_threshold
        self.decision_confidence_threshold = decision_confidence_threshold
        self.include_body_for_ai = include_body_for_ai
        self.privacy_mask_phone_numbers = privacy_mask_phone_numbers
        self.learned_rules = learned_rules or []
        self.misleading_terms = misleading_terms or []

    def preview(self, mails: list[MailMetadata]) -> list[PreviewRow]:
        return [self.preview_one(mail) for mail in mails]

    def preview_one(self, mail: MailMetadata) -> PreviewRow:
        rule = classify_with_learned_terms(mail, self.learned_rules) or classify_mail(
            mail,
            ignored_terms=[term.term for term in self.misleading_terms],
        )
        ai = self._maybe_classify_with_ai(mail, rule)
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

    def _maybe_classify_with_ai(
        self,
        mail: MailMetadata,
        rule: RuleClassification,
    ) -> AiMailClassification | None:
        if not should_call_ai(rule, ai_mode=self.ai_mode, threshold=self.rule_confidence_threshold):
            return None
        if self.ai_classifier is None:
            return None
        return self.ai_classifier.classify(
            mail,
            include_body=self.include_body_for_ai,
            privacy_mask_phone_numbers=self.privacy_mask_phone_numbers,
        )


def action_from_decision(*, archive: bool, requires_review: bool) -> PreviewAction:
    if requires_review:
        return PreviewAction.REVIEW
    if archive:
        return PreviewAction.ARCHIVE
    return PreviewAction.IGNORE
