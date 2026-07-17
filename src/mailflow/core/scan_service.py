from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mailflow.core.project_paths import (
    extract_project_number_from_folder_name,
    normalize_project_filter,
)
from mailflow.models import MailMetadata
from mailflow.outlook.scanner import OutlookScanner, ScannedMail


class OutlookFolderResolver(Protocol):
    def resolve_folder_path(
        self,
        path: str | list[str],
        *,
        account_identifier: str | None = None,
    ) -> object:
        ...


@dataclass(frozen=True)
class ScanRequest:
    account_identifier: str | None
    outlook_root_folder: str
    year: str
    project_number: str | None = None
    project_numbers: tuple[str, ...] | None = None
    entry_ids: frozenset[str] | None = None


@dataclass(frozen=True)
class ProjectFolderOption:
    project_number: str
    folder_name: str


@dataclass(frozen=True)
class DirectoryScanRequest:
    account_identifier: str | None
    outlook_root_folder: str


class OutlookScanService:
    def __init__(
        self,
        *,
        folder_resolver: OutlookFolderResolver,
        scanner: OutlookScanner,
    ) -> None:
        self.folder_resolver = folder_resolver
        self.scanner = scanner

    def scan(self, request: ScanRequest) -> list[MailMetadata]:
        return [scanned.metadata for scanned in self.scan_with_items(request)]

    def scan_with_items(self, request: ScanRequest) -> list[ScannedMail]:
        year_folder = self._resolve_year_folder(request)
        return self.scanner.scan_year_folder_with_items(
            year_folder,
            outlook_root_path=request.outlook_root_folder,
            project_numbers=_selected_project_numbers(request),
            entry_ids=request.entry_ids,
        )

    def list_project_folders(self, request: ScanRequest) -> list[ProjectFolderOption]:
        year_folder = self._resolve_year_folder(request)
        options: list[ProjectFolderOption] = []
        for folder in self.scanner.iter_project_folders(year_folder):
            folder_name = str(getattr(folder, "Name", "")).strip()
            project_number = extract_project_number_from_folder_name(folder_name)
            if project_number is not None:
                options.append(
                    ProjectFolderOption(
                        project_number=project_number,
                        folder_name=folder_name,
                    )
                )
        return options

    def scan_entry_ids(self, request: ScanRequest) -> set[str]:
        year_folder = self._resolve_year_folder(request)
        return self.scanner.scan_year_folder_entry_ids(
            year_folder,
            project_numbers=_selected_project_numbers(request),
        )

    def scan_all_project_folders_with_items(
        self,
        request: DirectoryScanRequest,
    ) -> list[ScannedMail]:
        root_folder = self.folder_resolver.resolve_folder_path(
            request.outlook_root_folder,
            account_identifier=request.account_identifier,
        )
        return self.scanner.scan_all_project_folders_with_items(
            root_folder,
            outlook_root_path=request.outlook_root_folder,
        )

    def _resolve_year_folder(self, request: ScanRequest) -> object:
        return self.folder_resolver.resolve_folder_path(
            [request.outlook_root_folder, request.year],
            account_identifier=request.account_identifier,
        )


def _selected_project_numbers(request: ScanRequest) -> set[str] | None:
    if request.project_numbers is not None:
        return {
            normalized
            for value in request.project_numbers
            if (normalized := normalize_project_filter(request.year, value)) is not None
        }
    project_number = normalize_project_filter(request.year, request.project_number)
    return {project_number} if project_number else None
