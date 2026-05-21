from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mailflow.core.correspondence_hierarchy import (
    CORRESPONDENCE_FOLDER,
    SUPPLIER_ORDER_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
    is_safe_relative_folder,
)
from mailflow.core.project_paths import local_project_path
from mailflow.models import (
    ArchiveDecision,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualClassificationUpdate,
    ManualLearningSignal,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)

MANUAL_DESTINATIONS = (
    CORRESPONDENCE_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
    SUPPLIER_ORDER_FOLDER,
    "A verifier",
    "Ne pas archiver",
)


@dataclass(frozen=True)
class LearnedClassificationRule:
    term: str
    mail_type: MailType
    interlocutor: InterlocutorType
    target_relative_folder: str
    confidence: float = 0.97


@dataclass(frozen=True)
class LearnedMisleadingTerm:
    term: str


def apply_manual_classification(
    row: PreviewRow,
    update: ManualClassificationUpdate,
    *,
    projects_root: Path,
    now: datetime | None = None,
) -> tuple[PreviewRow, ManualLearningSignal]:
    if not is_safe_relative_folder(update.target_relative_folder):
        msg = f"Destination manuelle invalide: {update.target_relative_folder}"
        raise ValueError(msg)

    project_path = local_project_path(projects_root, row.mail.project_number)
    target_path = _target_path(project_path, update.target_relative_folder)
    archive = _should_archive(update)
    requires_review = _requires_review(update)
    decision = ArchiveDecision(
        mail_id=row.mail.entry_id,
        project_number=row.mail.project_number,
        archive=archive,
        requires_review=requires_review,
        mail_type=update.mail_type,
        interlocutor=update.interlocutor,
        target_relative_folder=update.target_relative_folder,
        target_path=target_path,
        confidence=1.0,
        duplicate_status=row.decision.duplicate_status,
        reason="Classement manuel utilisateur.",
    )
    action = _action_from_manual_decision(archive=archive, requires_review=requires_review)
    updated_row = row.model_copy(update={"decision": decision, "action": action})
    signal = ManualLearningSignal(
        mail_id=row.mail.entry_id,
        project_number=row.mail.project_number,
        subject=row.mail.subject,
        selected_mail_type=update.mail_type,
        selected_interlocutor=update.interlocutor,
        selected_target_folder=update.target_relative_folder,
        learning_term=None if update.manual_required else update.learning_term,
        misleading_term=update.misleading_term,
        manual_required=update.manual_required,
        created_at=now or datetime.now(UTC),
    )
    return updated_row, signal


def classify_with_learned_terms(
    mail: MailMetadata,
    learned_rules: list[LearnedClassificationRule],
) -> RuleClassification | None:
    corpus = _normalize_learning_text(
        " ".join([mail.subject, mail.body_excerpt, " ".join(mail.attachment_names)])
    )
    for rule in learned_rules:
        if _normalize_learning_text(rule.term) in corpus:
            return RuleClassification(
                suggested_type=rule.mail_type,
                suggested_interlocutor=rule.interlocutor,
                likely_archive=rule.target_relative_folder != "Ne pas archiver",
                confidence=rule.confidence,
                matched_rules=[f"apprentissage:{rule.term}"],
                matched_terms=[rule.term],
            )
    return None


def learned_rule_from_signal(signal: ManualLearningSignal) -> LearnedClassificationRule | None:
    if signal.manual_required or not signal.learning_term:
        return None
    return LearnedClassificationRule(
        term=signal.learning_term,
        mail_type=signal.selected_mail_type,
        interlocutor=signal.selected_interlocutor,
        target_relative_folder=signal.selected_target_folder,
    )


def misleading_term_from_signal(signal: ManualLearningSignal) -> LearnedMisleadingTerm | None:
    if not signal.misleading_term:
        return None
    return LearnedMisleadingTerm(term=signal.misleading_term)


def _should_archive(update: ManualClassificationUpdate) -> bool:
    if update.mail_type == MailType.INUTILE_OU_FAIBLE_VALEUR:
        return False
    return update.target_relative_folder not in {"A verifier", "Ne pas archiver"}


def _requires_review(update: ManualClassificationUpdate) -> bool:
    return (
        update.mail_type == MailType.A_VERIFIER
        or update.interlocutor == InterlocutorType.INCONNU
        or update.target_relative_folder == "A verifier"
    )


def _target_path(project_path: Path, target_relative_folder: str) -> Path:
    if target_relative_folder in {"A verifier", "Ne pas archiver"}:
        return project_path
    return project_path.joinpath(*target_relative_folder.split("/"))


def _action_from_manual_decision(*, archive: bool, requires_review: bool) -> PreviewAction:
    if requires_review:
        return PreviewAction.REVIEW
    if archive:
        return PreviewAction.ARCHIVE
    return PreviewAction.IGNORE


def _normalize_learning_text(value: str) -> str:
    normalized = value.replace("\u2019", "'").lower()
    decomposed = unicodedata.normalize("NFKD", normalized)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
