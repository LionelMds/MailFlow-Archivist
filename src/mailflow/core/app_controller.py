from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from mailflow.classifier.ai_classifier import AiClassifier
from mailflow.classifier.pipeline import ClassificationPipeline
from mailflow.config import AppSettings, get_openai_api_key
from mailflow.core.archive_actions import mark_rows_ignored, rows_to_archive
from mailflow.core.archive_batch import (
    ArchiveBatchExecutor,
    ArchiveBatchResult,
    ArchiveCandidate,
    ArchiveFailure,
)
from mailflow.core.archive_service import ArchiveService
from mailflow.core.folder_tree import (
    FolderPathSummary,
    FolderTreeNode,
    build_folder_tree,
    folder_path_counts,
    merge_folder,
    rename_folder_leaf,
)
from mailflow.core.manual_review import (
    LearnedClassificationRule,
    LearnedMisleadingTerm,
    apply_manual_classification,
)
from mailflow.core.project_html_exporter import (
    ProjectHtmlExportResult,
    export_project_correspondence_html,
)
from mailflow.core.project_paths import local_project_path
from mailflow.core.reporting import export_preview_report
from mailflow.core.scan_service import OutlookScanService, ScanRequest
from mailflow.models import (
    AiMode,
    MailMetadata,
    ManualClassificationUpdate,
    ManualLearningSignal,
    OutlookAccount,
    PreviewAction,
    PreviewRow,
)
from mailflow.outlook.client import OutlookClient
from mailflow.outlook.exporter import OutlookExporter
from mailflow.outlook.scanner import OutlookScanner, ScannedMail
from mailflow.storage.learning_store import SQLiteLearningStore
from mailflow.storage.sqlite_store import SQLiteArchiveStore


class ScanServiceProtocol(Protocol):
    def scan(self, request: ScanRequest) -> list[MailMetadata]:
        ...

    def scan_with_items(self, request: ScanRequest) -> list[ScannedMail]:
        ...


class PreviewPipelineProtocol(Protocol):
    def preview(self, mails: list[MailMetadata]) -> list[PreviewRow]:
        ...


class AccountProviderProtocol(Protocol):
    def list_accounts(self) -> list[OutlookAccount]:
        ...

    def list_root_folder_paths(self, account_identifier: str | None = None) -> list[str]:
        ...


class LearningStoreProtocol(Protocol):
    def record(self, signal: ManualLearningSignal) -> None:
        ...

    def learned_rules(self) -> list[LearnedClassificationRule]:
        ...

    def misleading_terms(self) -> list[LearnedMisleadingTerm]:
        ...


@dataclass(frozen=True)
class PreviewRequest:
    account_identifier: str | None
    outlook_root_folder: str
    year: str
    project_number: str | None = None


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
    ) -> None:
        self.scan_service = scan_service
        self.preview_pipeline = preview_pipeline
        self.projects_root = projects_root
        self.report_dir = report_dir
        self.archive_executor = archive_executor
        self.learning_store = learning_store
        self.preview_rows: list[PreviewRow] = []
        self.outlook_items: dict[str, object] = {}

    def scan_and_preview(self, request: PreviewRequest) -> list[PreviewRow]:
        normalized = _normalize_preview_request(request)
        scanned = self.scan_service.scan_with_items(
            ScanRequest(
                account_identifier=normalized.account_identifier,
                outlook_root_folder=normalized.outlook_root_folder,
                year=normalized.year,
                project_number=normalized.project_number,
            )
        )
        mails = [item.metadata for item in scanned]
        self.outlook_items = {item.metadata.entry_id: item.item for item in scanned}
        self.preview_rows = self.preview_pipeline.preview(mails)
        return self.preview_rows

    def rows_ready_for_archive(self, *, include_review: bool = False) -> list[PreviewRow]:
        return rows_to_archive(self.preview_rows, include_review=include_review)

    def mark_all_ignored(self) -> list[PreviewRow]:
        self.preview_rows = mark_rows_ignored(self.preview_rows)
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
        updated_row, signal = apply_manual_classification(
            self.preview_rows[row_index],
            update,
            projects_root=self.projects_root,
        )
        self.preview_rows[row_index] = updated_row
        if self.learning_store is not None:
            self.learning_store.record(signal)
        return updated_row

    def suggested_account_identifier(self) -> str | None:
        return None

    def available_outlook_accounts(self) -> list[OutlookAccount]:
        return []

    def available_outlook_root_folders(
        self,
        account_identifier: str | None = None,
    ) -> list[str]:
        return []

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
            rule_confidence_threshold=settings.rule_confidence_threshold,
            decision_confidence_threshold=settings.decision_confidence_threshold,
            include_body_for_ai=settings.ai_include_body_excerpt,
            privacy_mask_phone_numbers=settings.privacy_mask_phone_numbers,
            learned_rules=learning_store.learned_rules(),
            misleading_terms=learning_store.misleading_terms(),
        ),
        outlook_client=outlook_client,
        projects_root=settings.local_projects_root,
        report_dir=settings.paths.data_dir,
        learning_store=learning_store,
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
    ) -> None:
        super().__init__(
            scan_service=scan_service,
            preview_pipeline=preview_pipeline,
            projects_root=projects_root,
            report_dir=report_dir,
            archive_executor=archive_executor,
            learning_store=learning_store,
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
    return AiClassifier(api_key=api_key, model=settings.ai_model)


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
    )


def selected_rows(rows: Sequence[PreviewRow], indexes: Sequence[int]) -> list[PreviewRow]:
    return [rows[index] for index in indexes if 0 <= index < len(rows)]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
