from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mailflow.core.correspondence_hierarchy import (
    CORRESPONDENCE_FOLDER,
    SUPPLIER_ORDER_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
)
from mailflow.core.mail_file_plan import planned_msg_path
from mailflow.core.project_paths import local_project_path
from mailflow.models import (
    AiMailClassification,
    ArchiveDecision,
    DuplicateStatus,
    InterlocutorType,
    MailMetadata,
    MailType,
    RuleClassification,
)
from mailflow.outlook.categories import ARCHIVED_CATEGORY

DEFAULT_CONFIDENCE_THRESHOLD = 0.80


class ArchiveState(Protocol):
    def is_archived(self, outlook_entry_id: str) -> bool:
        ...


def decide_archive(
    mail: MailMetadata,
    *,
    projects_root: Path,
    rule: RuleClassification,
    ai: AiMailClassification | None = None,
    archive_state: ArchiveState | None = None,
    candidate_msg_path: Path | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ArchiveDecision:
    project_path = local_project_path(projects_root, mail.project_number)
    mail_type, interlocutor, archive, confidence, reason = _classification_choice(rule, ai)
    target_relative = (
        _target_from_ai(ai) or destination_for(mail_type, interlocutor) or "Ne pas archiver"
    )
    target_path = _resolve_target_path(project_path, target_relative)

    if ARCHIVED_CATEGORY in mail.categories:
        return _decision(
            mail,
            archive=False,
            requires_review=False,
            mail_type=mail_type,
            interlocutor=interlocutor,
            target_relative=target_relative,
            target_path=target_path,
            confidence=confidence,
            duplicate_status="already_archived",
            reason="Mail deja marque ProjectFlow - Archive dans Outlook.",
        )

    if archive_state is not None and archive_state.is_archived(mail.entry_id):
        return _decision(
            mail,
            archive=False,
            requires_review=False,
            mail_type=mail_type,
            interlocutor=interlocutor,
            target_relative=target_relative,
            target_path=target_path,
            confidence=confidence,
            duplicate_status="already_archived",
            reason="Mail deja present dans le journal SQLite.",
        )

    if not project_path.exists():
        return _decision(
            mail,
            archive=False,
            requires_review=True,
            mail_type=mail_type,
            interlocutor=interlocutor,
            target_relative=target_relative,
            target_path=target_path,
            confidence=confidence,
            duplicate_status="none",
            reason=f"Dossier projet local absent: {project_path}",
        )

    if mail_type == MailType.INUTILE_OU_FAIBLE_VALEUR:
        return _decision(
            mail,
            archive=False,
            requires_review=False,
            mail_type=mail_type,
            interlocutor=interlocutor,
            target_relative=target_relative,
            target_path=target_path,
            confidence=confidence,
            duplicate_status="none",
            reason="Mail classe comme inutile ou faible valeur.",
        )

    if target_relative == "Ne pas archiver":
        return _decision(
            mail,
            archive=False,
            requires_review=True,
            mail_type=mail_type,
            interlocutor=interlocutor,
            target_relative=target_relative,
            target_path=target_path,
            confidence=confidence,
            duplicate_status="none",
            reason="Aucune destination d'archivage determinee, validation requise.",
        )

    if confidence < confidence_threshold or mail_type == MailType.A_VERIFIER:
        return _decision(
            mail,
            archive=False,
            requires_review=True,
            mail_type=mail_type,
            interlocutor=interlocutor,
            target_relative=target_relative,
            target_path=target_path,
            confidence=confidence,
            duplicate_status="none",
            reason="Confiance insuffisante, validation utilisateur requise.",
        )

    planned_path = candidate_msg_path or planned_msg_path(mail, target_path)
    if planned_path.exists():
        return _decision(
            mail,
            archive=False,
            requires_review=True,
            mail_type=mail_type,
            interlocutor=interlocutor,
            target_relative=target_relative,
            target_path=target_path,
            confidence=confidence,
            duplicate_status="same_file_exists",
            reason="Un fichier cible existe deja.",
        )

    return _decision(
        mail,
        archive=archive,
        requires_review=not archive,
        mail_type=mail_type,
        interlocutor=interlocutor,
        target_relative=target_relative,
        target_path=target_path,
        confidence=confidence,
        duplicate_status="none",
        reason=reason,
    )


def destination_for(mail_type: MailType, interlocutor: InterlocutorType) -> str | None:
    if mail_type in {MailType.DEMANDE_DE_PRIX, MailType.DEVIS}:
        if interlocutor == InterlocutorType.FOURNISSEUR:
            return SUPPLIER_REQUEST_FOLDER
    if mail_type == MailType.COMMANDE:
        if interlocutor == InterlocutorType.FOURNISSEUR:
            return SUPPLIER_ORDER_FOLDER
    if mail_type in {
        MailType.FACTURE,
        MailType.CORRESPONDANCE_GENERALE,
        MailType.TECHNIQUE,
        MailType.PLAN,
        MailType.LIVRAISON,
        MailType.ADMINISTRATIF,
    }:
        return CORRESPONDENCE_FOLDER
    if mail_type == MailType.A_VERIFIER:
        return "A verifier"
    return None


def _classification_choice(
    rule: RuleClassification,
    ai: AiMailClassification | None,
) -> tuple[MailType, InterlocutorType, bool, float, str]:
    if ai is not None:
        return (
            MailType(ai.mail_type),
            InterlocutorType(ai.interlocutor),
            ai.archive,
            ai.confidence,
            ai.reason,
        )
    return (
        rule.suggested_type or MailType.A_VERIFIER,
        rule.suggested_interlocutor or InterlocutorType.INCONNU,
        bool(rule.likely_archive),
        rule.confidence,
        "Decision issue des regles locales.",
    )


def _target_from_ai(ai: AiMailClassification | None) -> str | None:
    if ai is None:
        return None
    if ai.target_folder == "A verifier":
        return "A verifier"
    return ai.target_folder


def _resolve_target_path(project_path: Path, target_relative: str) -> Path:
    if target_relative in {"Ne pas archiver", "A verifier"}:
        return project_path
    return project_path.joinpath(*target_relative.split("/"))


def _decision(
    mail: MailMetadata,
    *,
    archive: bool,
    requires_review: bool,
    mail_type: MailType,
    interlocutor: InterlocutorType,
    target_relative: str,
    target_path: Path,
    confidence: float,
    duplicate_status: DuplicateStatus,
    reason: str,
) -> ArchiveDecision:
    return ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=archive,
        requires_review=requires_review,
        mail_type=mail_type,
        interlocutor=interlocutor,
        target_relative_folder=target_relative,
        target_path=target_path,
        confidence=confidence,
        duplicate_status=duplicate_status,
        reason=reason,
    )
