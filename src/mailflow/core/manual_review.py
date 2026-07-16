from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mailflow.classifier.routing_context import primary_external_email
from mailflow.core.correspondence_hierarchy import (
    CORRESPONDENCE_FOLDER,
    SUPPLIER_ORDER_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
    OrganizationDirectoryProtocol,
    company_folder_for_row,
    is_safe_relative_folder,
)
from mailflow.core.project_paths import local_project_path
from mailflow.models import (
    ArchiveDecision,
    InterlocutorType,
    MailType,
    ManualClassificationUpdate,
    ManualLearningSignal,
    PreviewAction,
    PreviewRow,
    VerifiedRoutingExample,
    routing_category_for_mail_type,
)

MANUAL_DESTINATIONS = (
    CORRESPONDENCE_FOLDER,
    SUPPLIER_REQUEST_FOLDER,
    SUPPLIER_ORDER_FOLDER,
)
SPECIAL_MANUAL_DESTINATIONS = {"A verifier", "Ne pas archiver"}


def apply_manual_classification(
    row: PreviewRow,
    update: ManualClassificationUpdate,
    *,
    projects_root: Path,
    organization_directory: OrganizationDirectoryProtocol | None = None,
    now: datetime | None = None,
) -> tuple[PreviewRow, ManualLearningSignal]:
    update = _normalized_business_update(update)
    if not is_safe_relative_folder(update.target_relative_folder):
        msg = f"Destination manuelle invalide: {update.target_relative_folder}"
        raise ValueError(msg)

    target_relative_folder = resolve_manual_target_folder(
        row,
        update.target_relative_folder,
        organization_directory=organization_directory,
    )
    project_path = local_project_path(projects_root, row.mail.project_number)
    target_path = _target_path(project_path, target_relative_folder)
    archive = _should_archive(target_relative_folder, update.mail_type)
    requires_review = _requires_review(target_relative_folder, update)
    decision = ArchiveDecision(
        mail_id=row.mail.entry_id,
        project_number=row.mail.project_number,
        archive=archive,
        requires_review=requires_review,
        mail_type=update.mail_type,
        interlocutor=update.interlocutor,
        target_relative_folder=target_relative_folder,
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
        selected_target_folder=target_relative_folder,
        learning_term=None,
        misleading_term=None,
        manual_required=False,
        created_at=now or datetime.now(UTC),
        organization_name=company_folder_for_row(row, organization_directory),
        primary_email=primary_external_email(row.mail),
    )
    return updated_row, signal


def _normalized_business_update(
    update: ManualClassificationUpdate,
) -> ManualClassificationUpdate:
    if update.interlocutor == InterlocutorType.CLIENT:
        return update.model_copy(
            update={
                "mail_type": MailType.CORRESPONDANCE_GENERALE,
                "target_relative_folder": CORRESPONDENCE_FOLDER,
            }
        )
    if update.interlocutor == InterlocutorType.FOURNISSEUR:
        if update.mail_type == MailType.DEMANDE_DE_PRIX:
            target = SUPPLIER_REQUEST_FOLDER
        elif update.mail_type == MailType.COMMANDE:
            target = SUPPLIER_ORDER_FOLDER
        else:
            return update.model_copy(
                update={
                    "mail_type": MailType.A_VERIFIER,
                    "target_relative_folder": "A verifier",
                }
            )
        return update.model_copy(update={"target_relative_folder": target})
    return update


def resolve_manual_target_folder(
    row: PreviewRow,
    target_relative_folder: str,
    *,
    organization_directory: OrganizationDirectoryProtocol | None = None,
) -> str:
    normalized = target_relative_folder.replace("\\", "/").strip("/")
    if normalized in SPECIAL_MANUAL_DESTINATIONS:
        return normalized
    if normalized not in MANUAL_DESTINATIONS:
        return normalized
    company = company_folder_for_row(row, organization_directory)
    return f"{normalized}/{company}"


def suggested_manual_destination(
    mail_type: MailType,
    interlocutor: InterlocutorType,
) -> str:
    if interlocutor == InterlocutorType.INCONNU:
        return "A verifier"
    if interlocutor == InterlocutorType.FOURNISSEUR:
        if mail_type == MailType.COMMANDE:
            return SUPPLIER_ORDER_FOLDER
        if mail_type == MailType.DEMANDE_DE_PRIX:
            return SUPPLIER_REQUEST_FOLDER
        return "A verifier"
    if interlocutor == InterlocutorType.CLIENT:
        return CORRESPONDENCE_FOLDER
    return "A verifier"


def verified_example_from_signal(
    signal: ManualLearningSignal,
) -> VerifiedRoutingExample | None:
    if (
        not signal.organization_name
        or signal.selected_mail_type == MailType.A_VERIFIER
        or signal.selected_interlocutor not in {
        InterlocutorType.CLIENT,
        InterlocutorType.FOURNISSEUR,
        }
    ):
        return None
    return VerifiedRoutingExample(
        project_number=signal.project_number,
        subject=signal.subject,
        organization_name=signal.organization_name,
        organization_role=signal.selected_interlocutor,
        category=routing_category_for_mail_type(signal.selected_mail_type),
    )


def _should_archive(target_relative_folder: str, mail_type: MailType) -> bool:
    return target_relative_folder not in {"A verifier", "Ne pas archiver"}


def _requires_review(
    target_relative_folder: str,
    update: ManualClassificationUpdate,
) -> bool:
    return (
        update.interlocutor == InterlocutorType.INCONNU
        or target_relative_folder == "A verifier"
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
