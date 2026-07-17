from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from mailflow.classifier.ai_classifier import AiClassifier
from mailflow.classifier.decision_engine import destination_for
from mailflow.classifier.pipeline import ClassificationPipeline, action_from_decision
from mailflow.classifier.routing_context import primary_external_email
from mailflow.config import AppSettings, get_openai_api_key
from mailflow.core.archive_actions import (
    mark_rows_archivable,
    mark_rows_ignored,
    rows_to_archive,
)
from mailflow.core.archive_batch import (
    ArchiveBatchExecutor,
    ArchiveBatchResult,
    ArchiveCandidate,
    ArchiveFailure,
)
from mailflow.core.archive_service import ArchiveService
from mailflow.core.contact_directory import (
    ContactDirectoryStoreProtocol,
    DirectoryImportResult,
    OrganizationDirectoryEntry,
    import_contact_directory_from_mails,
)
from mailflow.core.correspondence_hierarchy import (
    OrganizationDirectoryProtocol,
    apply_correspondence_hierarchy,
)
from mailflow.core.folder_tree import (
    FolderPathSummary,
    FolderTreeNode,
    build_folder_tree,
    folder_path_counts,
    merge_folder,
    rename_folder_leaf,
)
from mailflow.core.manual_review import apply_manual_classification, verified_example_from_signal
from mailflow.core.project_html_exporter import (
    ProjectHtmlExportResult,
    export_project_correspondence_html,
)
from mailflow.core.project_paths import local_project_path
from mailflow.core.reporting import export_preview_report
from mailflow.core.scan_service import (
    DirectoryScanRequest,
    OutlookScanService,
    ProjectFolderOption,
    ScanRequest,
)
from mailflow.models import (
    AiMode,
    InterlocutorType,
    MailMetadata,
    ManualClassificationUpdate,
    ManualLearningSignal,
    OutlookAccount,
    PreviewAction,
    PreviewRow,
    VerifiedRoutingExample,
)
from mailflow.outlook.client import OutlookClient
from mailflow.outlook.exporter import OutlookExporter
from mailflow.outlook.scanner import OutlookScanner, ScannedMail
from mailflow.storage.directory_store import SQLiteDirectoryStore
from mailflow.storage.learning_store import SQLiteLearningStore
from mailflow.storage.sqlite_store import SQLiteArchiveStore

ScanProgressCallback = Callable[[str], None]


class ScanServiceProtocol(Protocol):
    def scan(self, request: ScanRequest) -> list[MailMetadata]:
        ...

    def scan_with_items(self, request: ScanRequest) -> list[ScannedMail]:
        ...

    def scan_all_project_folders_with_items(
        self,
        request: DirectoryScanRequest,
    ) -> list[ScannedMail]:
        ...

    def list_project_folders(self, request: ScanRequest) -> list[ProjectFolderOption]:
        ...

    def scan_entry_ids(self, request: ScanRequest) -> set[str]:
        ...


class PreviewPipelineProtocol(Protocol):
    def preview(
        self,
        mails: list[MailMetadata],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[PreviewRow]:
        ...


class AccountProviderProtocol(Protocol):
    def list_accounts(self) -> list[OutlookAccount]:
        ...

    def list_root_folder_paths(self, account_identifier: str | None = None) -> list[str]:
        ...


class LearningStoreProtocol(Protocol):
    def record(self, signal: ManualLearningSignal) -> None:
        ...

    def verified_examples(self) -> list[VerifiedRoutingExample]:
        ...


class DirectoryStoreProtocol(
    ContactDirectoryStoreProtocol,
    OrganizationDirectoryProtocol,
    Protocol,
):
    def list_organizations(self) -> list[OrganizationDirectoryEntry]:
        ...

    def add_organization(
        self,
        name: str,
        *,
        domain: str | None = None,
        role: InterlocutorType = InterlocutorType.INCONNU,
    ) -> int:
        ...

    def set_organization_role(
        self,
        organization_id: int,
        role: InterlocutorType,
    ) -> None:
        ...

    def delete_organization(self, organization_id: int) -> None:
        ...

    def rename_organization(self, organization_id: int, name: str) -> None:
        ...

    def merge_organizations(
        self,
        source_organization_id: int,
        target_organization_id: int,
    ) -> None:
        ...

    def interlocutor_for_email(
        self,
        project_number: str,
        email: str,
    ) -> InterlocutorType | None:
        ...


@dataclass(frozen=True)
class PreviewRequest:
    account_identifier: str | None
    outlook_root_folder: str
    year: str
    project_number: str | None = None
    project_numbers: tuple[str, ...] | None = None


class AppController:
    def __init__(
        self,
        *,
        scan_service: ScanServiceProtocol,
        preview_pipeline: PreviewPipelineProtocol,
        projects_root: Path,
        report_dir: Path,
        archive_executor: ArchiveBatchExecutor | None = None,
        learning_store: LearningStoreProtocol | None = None,
        directory_store: DirectoryStoreProtocol | None = None,
    ) -> None:
        self.scan_service = scan_service
        self.preview_pipeline = preview_pipeline
        self.projects_root = projects_root
        self.report_dir = report_dir
        self.archive_executor = archive_executor
        self.learning_store = learning_store
        self.directory_store = directory_store
        self.preview_rows: list[PreviewRow] = []
        self.outlook_items: dict[str, object] = {}

    def scan_and_preview(
        self,
        request: PreviewRequest,
        *,
        progress_callback: ScanProgressCallback | None = None,
    ) -> list[PreviewRow]:
        normalized = _normalize_preview_request(request)
        if progress_callback is not None:
            progress_callback("Lecture Outlook en cours...")
        scanned = self.scan_service.scan_with_items(
            ScanRequest(
                account_identifier=normalized.account_identifier,
                outlook_root_folder=normalized.outlook_root_folder,
                year=normalized.year,
                project_number=normalized.project_number,
                project_numbers=normalized.project_numbers,
            )
        )
        mails = [item.metadata for item in scanned]
        self.outlook_items = {item.metadata.entry_id: item.item for item in scanned}
        if progress_callback is not None:
            progress_callback(f"{len(mails)} mail(s) lus. Classification en cours...")
        self.preview_rows = self.preview_pipeline.preview(
            mails,
            progress_callback=(
                None
                if progress_callback is None
                else lambda index, total: progress_callback(
                    f"Classification {index}/{total}..."
                )
            ),
        )
        if progress_callback is not None:
            progress_callback(f"{len(self.preview_rows)} mail(s) prets.")
        return self.preview_rows

    def available_project_folders(
        self,
        request: PreviewRequest,
    ) -> list[ProjectFolderOption]:
        normalized = _normalize_preview_request(request)
        return self.scan_service.list_project_folders(
            ScanRequest(
                account_identifier=normalized.account_identifier,
                outlook_root_folder=normalized.outlook_root_folder,
                year=normalized.year,
            )
        )

    def scan_entry_ids(self, request: PreviewRequest) -> set[str]:
        normalized = _normalize_preview_request(request)
        return self.scan_service.scan_entry_ids(
            ScanRequest(
                account_identifier=normalized.account_identifier,
                outlook_root_folder=normalized.outlook_root_folder,
                year=normalized.year,
                project_number=normalized.project_number,
                project_numbers=normalized.project_numbers,
            )
        )

    def scan_incremental_preview(
        self,
        request: PreviewRequest,
        entry_ids: Sequence[str],
        *,
        progress_callback: ScanProgressCallback | None = None,
    ) -> list[PreviewRow]:
        wanted_ids = frozenset(entry_id for entry_id in entry_ids if entry_id)
        if not wanted_ids:
            return []
        normalized = _normalize_preview_request(request)
        if progress_callback is not None:
            progress_callback("Lecture des nouveaux mails Outlook...")
        scanned = self.scan_service.scan_with_items(
            ScanRequest(
                account_identifier=normalized.account_identifier,
                outlook_root_folder=normalized.outlook_root_folder,
                year=normalized.year,
                project_number=normalized.project_number,
                project_numbers=normalized.project_numbers,
                entry_ids=wanted_ids,
            )
        )
        mails = [item.metadata for item in scanned]
        new_rows = self.preview_pipeline.preview(
            mails,
            progress_callback=(
                None
                if progress_callback is None
                else lambda index, total: progress_callback(
                    f"Classification des nouveaux mails {index}/{total}..."
                )
            ),
        )
        current_ids = {row.mail.entry_id for row in self.preview_rows}
        self.preview_rows.extend(
            row for row in new_rows if row.mail.entry_id not in current_ids
        )
        self.outlook_items.update(
            {item.metadata.entry_id: item.item for item in scanned}
        )
        return new_rows

    def reset_preview(self) -> list[PreviewRow]:
        self.preview_rows = []
        self.outlook_items = {}
        return self.preview_rows

    def reclassify_preview(
        self,
        *,
        progress_callback: ScanProgressCallback | None = None,
    ) -> list[PreviewRow]:
        mails = [row.mail for row in self.preview_rows]
        if not mails:
            return self.preview_rows
        self.preview_rows = self.preview_pipeline.preview(
            mails,
            progress_callback=(
                None
                if progress_callback is None
                else lambda index, total: progress_callback(
                    f"Reclassification {index}/{total}..."
                )
            ),
        )
        return self.preview_rows

    def rows_ready_for_archive(self, *, include_review: bool = False) -> list[PreviewRow]:
        return rows_to_archive(self.preview_rows, include_review=include_review)

    def mark_all_ignored(self) -> list[PreviewRow]:
        self.preview_rows = mark_rows_ignored(self.preview_rows)
        return self.preview_rows

    def mark_selected_ignored(self, row_indexes: Sequence[int]) -> list[PreviewRow]:
        self.preview_rows = mark_rows_ignored(self.preview_rows, list(row_indexes))
        return self.preview_rows

    def mark_all_archivable(self) -> list[PreviewRow]:
        self.preview_rows = mark_rows_archivable(self.preview_rows)
        return self.preview_rows

    def folder_tree(self) -> list[FolderTreeNode]:
        return build_folder_tree(self.preview_rows)

    def folder_path_counts(self) -> list[FolderPathSummary]:
        return folder_path_counts(self.preview_rows)

    def rename_preview_folder(
        self,
        source_relative_folder: str,
        new_folder_name: str,
    ) -> list[PreviewRow]:
        self.preview_rows = rename_folder_leaf(
            self.preview_rows,
            source_relative_folder,
            new_folder_name,
            projects_root=self.projects_root,
        )
        return self.preview_rows

    def merge_preview_folder(
        self,
        source_relative_folder: str,
        target_relative_folder: str,
    ) -> list[PreviewRow]:
        self.preview_rows = merge_folder(
            self.preview_rows,
            source_relative_folder,
            target_relative_folder,
            projects_root=self.projects_root,
        )
        return self.preview_rows

    def export_report(self, path: Path | None = None) -> Path:
        target = path or self._default_report_path()
        return export_preview_report(self.preview_rows, target)

    def export_project_html(
        self,
        row_indexes: Sequence[int] | None = None,
        *,
        overwrite_html: bool = False,
    ) -> list[ProjectHtmlExportResult]:
        rows = (
            selected_rows(self.preview_rows, row_indexes)
            if row_indexes is not None
            else self.preview_rows
        )
        return export_project_correspondence_html(
            rows,
            self.outlook_items,
            self.projects_root,
            overwrite_html=overwrite_html,
        )

    def apply_manual_update(
        self,
        row_index: int,
        update: ManualClassificationUpdate,
    ) -> PreviewRow:
        if row_index < 0 or row_index >= len(self.preview_rows):
            msg = f"Index de ligne invalide: {row_index}"
            raise IndexError(msg)
        organization_directory = (
            cast(OrganizationDirectoryProtocol, self.directory_store)
            if hasattr(self.directory_store, "organization_name_for_email")
            else None
        )
        updated_row, signal = apply_manual_classification(
            self.preview_rows[row_index],
            update,
            projects_root=self.projects_root,
            organization_directory=organization_directory,
        )
        self.preview_rows[row_index] = updated_row
        if self.learning_store is not None:
            self.learning_store.record(signal)
        example = verified_example_from_signal(signal)
        add_example = getattr(self.preview_pipeline, "add_verified_example", None)
        if example is not None and callable(add_example):
            add_example(example)
        return self.preview_rows[row_index]

    def suggested_account_identifier(self) -> str | None:
        return None

    def available_outlook_accounts(self) -> list[OutlookAccount]:
        return []

    def available_outlook_root_folders(
        self,
        account_identifier: str | None = None,
    ) -> list[str]:
        return []

    def import_contact_directory(
        self,
        *,
        account_identifier: str | None,
        outlook_root_folder: str,
    ) -> DirectoryImportResult:
        if self.directory_store is None:
            msg = "Aucun annuaire n'est configure"
            raise RuntimeError(msg)
        root = outlook_root_folder.strip()
        if not root:
            msg = "Le dossier Outlook racine est obligatoire"
            raise ValueError(msg)
        scanned = self.scan_service.scan_all_project_folders_with_items(
            DirectoryScanRequest(
                account_identifier=_clean_optional(account_identifier),
                outlook_root_folder=root,
            )
        )
        return import_contact_directory_from_mails(
            [item.metadata for item in scanned],
            self.directory_store,
        )

    def directory_entries(self) -> list[OrganizationDirectoryEntry]:
        if self.directory_store is None:
            msg = "Aucun annuaire n'est configure"
            raise RuntimeError(msg)
        return self.directory_store.list_organizations()

    def add_directory_organization(
        self,
        name: str,
        *,
        domain: str | None = None,
        role: InterlocutorType = InterlocutorType.INCONNU,
    ) -> int:
        if self.directory_store is None:
            msg = "Aucun annuaire n'est configure"
            raise RuntimeError(msg)
        organization_id = self.directory_store.add_organization(
            name,
            domain=domain,
            role=role,
        )
        self.preview_rows = apply_directory_roles_to_rows(
            self.preview_rows,
            self.directory_store,
            self.projects_root,
        )
        return organization_id

    def set_directory_organization_role(
        self,
        organization_id: int,
        role: InterlocutorType,
    ) -> list[PreviewRow]:
        if self.directory_store is None:
            msg = "Aucun annuaire n'est configure"
            raise RuntimeError(msg)
        self.directory_store.set_organization_role(organization_id, role)
        self.preview_rows = apply_directory_roles_to_rows(
            self.preview_rows,
            self.directory_store,
            self.projects_root,
        )
        return self.preview_rows

    def delete_directory_organization(self, organization_id: int) -> None:
        if self.directory_store is None:
            msg = "Aucun annuaire n'est configure"
            raise RuntimeError(msg)
        self.directory_store.delete_organization(organization_id)

    def rename_directory_organization(self, organization_id: int, name: str) -> None:
        if self.directory_store is None:
            msg = "Aucun annuaire n'est configure"
            raise RuntimeError(msg)
        self.directory_store.rename_organization(organization_id, name)

    def merge_directory_organizations(
        self,
        source_organization_id: int,
        target_organization_id: int,
    ) -> None:
        if self.directory_store is None:
            msg = "Aucun annuaire n'est configure"
            raise RuntimeError(msg)
        self.directory_store.merge_organizations(
            source_organization_id,
            target_organization_id,
        )

    def current_project_number(self) -> str | None:
        for row in self.preview_rows:
            if row.mail.project_number.strip():
                return row.mail.project_number
        return None

    def archive_ready(self, *, include_review: bool = False) -> ArchiveBatchResult:
        return self._archive_preview_rows(self.preview_rows, include_review=include_review)

    def archive_selected(
        self,
        row_indexes: Sequence[int],
        *,
        include_review: bool = False,
    ) -> ArchiveBatchResult:
        return self._archive_preview_rows(
            selected_rows(self.preview_rows, row_indexes),
            include_review=include_review,
        )

    def _archive_preview_rows(
        self,
        rows: Sequence[PreviewRow],
        *,
        include_review: bool = False,
    ) -> ArchiveBatchResult:
        if self.archive_executor is None:
            msg = "Aucun service d'archivage n'est configure"
            raise RuntimeError(msg)
        preflight_result = ArchiveBatchResult()
        candidates: list[ArchiveCandidate] = []
        for row in rows:
            project_path = local_project_path(self.projects_root, row.mail.project_number)
            if not project_path.exists():
                preflight_result.failures.append(
                    ArchiveFailure(
                        mail_id=row.mail.entry_id,
                        reason=f"Dossier projet local absent: {project_path}",
                    )
                )
                continue
            if not row.decision.target_path.exists():
                row.decision.target_path.mkdir(parents=True, exist_ok=True)
            item = self.outlook_items.get(row.mail.entry_id)
            if item is None:
                preflight_result.skipped.append(row.mail.entry_id)
                continue
            candidates.append(ArchiveCandidate(item=item, row=row))
        result = self.archive_executor.archive(candidates, include_review=include_review)
        result.skipped[:0] = preflight_result.skipped
        result.failures[:0] = preflight_result.failures
        self._mark_preview_rows_archived(result.exported_mail_ids)
        return result

    def _default_report_path(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.report_dir / f"mailflow_report_{timestamp}.csv"

    def _mark_preview_rows_archived(self, mail_ids: Sequence[str]) -> None:
        exported_ids = set(mail_ids)
        if not exported_ids:
            return
        self.preview_rows = [
            row.model_copy(update={"action": PreviewAction.ARCHIVED})
            if row.mail.entry_id in exported_ids
            else row
            for row in self.preview_rows
        ]


def build_default_controller(settings: AppSettings) -> AppController:
    store = SQLiteArchiveStore(settings.paths.sqlite_file)
    store.initialize()
    learning_store = SQLiteLearningStore(settings.paths.sqlite_file)
    learning_store.initialize()
    directory_store = SQLiteDirectoryStore(settings.paths.sqlite_file)
    directory_store.initialize()
    ai_classifier = _build_ai_classifier(settings)
    outlook_client = OutlookClient()
    return OutlookAppController(
        scan_service=OutlookScanService(
            folder_resolver=outlook_client,
            scanner=OutlookScanner(account_email=settings.selected_outlook_account or ""),
        ),
        preview_pipeline=ClassificationPipeline(
            projects_root=settings.local_projects_root,
            archive_state=store,
            ai_mode=settings.ai_mode,
            ai_classifier=ai_classifier,
            decision_confidence_threshold=settings.decision_confidence_threshold,
            include_body_for_ai=settings.ai_include_body_excerpt,
            privacy_mask_phone_numbers=settings.privacy_mask_phone_numbers,
            organization_directory=directory_store,
            verified_examples=learning_store.verified_examples(),
        ),
        outlook_client=outlook_client,
        projects_root=settings.local_projects_root,
        report_dir=settings.paths.data_dir,
        learning_store=learning_store,
        directory_store=directory_store,
        archive_executor=ArchiveBatchExecutor(
            ArchiveService(
                exporter=OutlookExporter(),
                store=store,
            )
        ),
    )


class OutlookAppController(AppController):
    def __init__(
        self,
        *,
        outlook_client: AccountProviderProtocol,
        scan_service: ScanServiceProtocol,
        preview_pipeline: PreviewPipelineProtocol,
        projects_root: Path,
        report_dir: Path,
        archive_executor: ArchiveBatchExecutor | None = None,
        learning_store: LearningStoreProtocol | None = None,
        directory_store: DirectoryStoreProtocol | None = None,
    ) -> None:
        super().__init__(
            scan_service=scan_service,
            preview_pipeline=preview_pipeline,
            projects_root=projects_root,
            report_dir=report_dir,
            archive_executor=archive_executor,
            learning_store=learning_store,
            directory_store=directory_store,
        )
        self.outlook_client = outlook_client

    def suggested_account_identifier(self) -> str | None:
        accounts = self.available_outlook_accounts()
        if not accounts:
            return None
        return accounts[0].smtp_address or accounts[0].display_name

    def available_outlook_accounts(self) -> list[OutlookAccount]:
        return self.outlook_client.list_accounts()

    def available_outlook_root_folders(
        self,
        account_identifier: str | None = None,
    ) -> list[str]:
        return self.outlook_client.list_root_folder_paths(account_identifier)


def _build_ai_classifier(settings: AppSettings) -> AiClassifier | None:
    if settings.ai_mode == AiMode.DISABLED:
        return None
    api_key = get_openai_api_key()
    if not api_key:
        return None
    return AiClassifier(
        api_key=api_key,
        model=settings.ai_model,
        timeout_seconds=settings.openai_timeout_seconds,
    )


def _normalize_preview_request(request: PreviewRequest) -> PreviewRequest:
    year = request.year.strip()
    if not year:
        msg = "L'annee Outlook est obligatoire"
        raise ValueError(msg)
    root = request.outlook_root_folder.strip()
    if not root:
        msg = "Le dossier Outlook racine est obligatoire"
        raise ValueError(msg)
    return PreviewRequest(
        account_identifier=_clean_optional(request.account_identifier),
        outlook_root_folder=root,
        year=year,
        project_number=_clean_optional(request.project_number),
        project_numbers=(
            None
            if request.project_numbers is None
            else tuple(
                project
                for value in request.project_numbers
                if (project := _clean_optional(value)) is not None
            )
        ),
    )


def selected_rows(rows: Sequence[PreviewRow], indexes: Sequence[int]) -> list[PreviewRow]:
    return [rows[index] for index in indexes if 0 <= index < len(rows)]


def apply_directory_roles_to_rows(
    rows: list[PreviewRow],
    directory_store: DirectoryStoreProtocol,
    projects_root: Path,
) -> list[PreviewRow]:
    updated_rows = [_row_with_directory_role(row, directory_store, projects_root) for row in rows]
    return apply_correspondence_hierarchy(
        updated_rows,
        projects_root=projects_root,
        organization_directory=directory_store,
    )


def _row_with_directory_role(
    row: PreviewRow,
    directory_store: DirectoryStoreProtocol,
    projects_root: Path,
) -> PreviewRow:
    role = _directory_role_for_row(row, directory_store)
    if role is None or role == row.decision.interlocutor:
        return row
    target_relative = destination_for(row.decision.mail_type, role) or "A verifier"
    target_path = (
        local_project_path(projects_root, row.mail.project_number)
        if target_relative == "A verifier"
        else local_project_path(projects_root, row.mail.project_number).joinpath(
            *target_relative.split("/")
        )
    )
    archive = row.decision.archive and target_relative != "A verifier"
    requires_review = row.decision.requires_review or target_relative == "A verifier"
    decision = row.decision.model_copy(
        update={
            "interlocutor": role,
            "archive": archive,
            "requires_review": requires_review,
            "target_relative_folder": target_relative,
            "target_path": target_path,
            "reason": _append_directory_role_reason(row.decision.reason, role),
        }
    )
    return row.model_copy(
        update={
            "decision": decision,
            "action": action_from_decision(
                archive=decision.archive,
                requires_review=decision.requires_review,
            ),
        }
    )


def _directory_role_for_row(
    row: PreviewRow,
    directory_store: DirectoryStoreProtocol,
) -> InterlocutorType | None:
    for email in _participant_emails(row.mail):
        role = directory_store.interlocutor_for_email(row.mail.project_number, email)
        if role is not None and role != InterlocutorType.INCONNU:
            return role
    return None


def _participant_emails(mail: MailMetadata) -> list[str]:
    email = primary_external_email(mail)
    return [] if email is None else [email]


def _append_directory_role_reason(reason: str, role: InterlocutorType) -> str:
    note = f"Role entreprise applique: {role.value}."
    if note in reason:
        return reason
    return f"{reason} {note}".strip()


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
