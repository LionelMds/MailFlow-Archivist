from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mailflow.core.project_paths import (
    extract_project_number_from_folder_name,
    normalize_project_filter,
)
from mailflow.models import MailMetadata
from mailflow.outlook.client import (
    OutlookFolderNotFoundError,
    child_folder_named,
    is_archive_folder_name,
)
from mailflow.outlook.scanner import OutlookScanner, ScannedMail, iter_com_collection


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
    archived: bool = False


@dataclass(frozen=True)
class YearFolder:
    folder: object
    outlook_path: str
    archived: bool = False


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
        scanned: list[ScannedMail] = []
        for year_folder in self._year_folders(request):
            scanned.extend(
                self.scanner.scan_year_folder_with_items(
                    year_folder.folder,
                    outlook_root_path=year_folder.outlook_path,
                    project_numbers=_selected_project_numbers(request),
                    entry_ids=request.entry_ids,
                )
            )
        return scanned

    def list_project_folders(self, request: ScanRequest) -> list[ProjectFolderOption]:
        options: list[ProjectFolderOption] = []
        for year_folder in self._year_folders(request):
            for folder in self.scanner.iter_project_folders(year_folder.folder):
                folder_name = str(getattr(folder, "Name", "")).strip()
                project_number = extract_project_number_from_folder_name(folder_name)
                if project_number is not None:
                    options.append(
                        ProjectFolderOption(
                            project_number=project_number,
                            folder_name=folder_name,
                            archived=year_folder.archived,
                        )
                    )
        return options

    def scan_entry_ids(self, request: ScanRequest) -> set[str]:
        entry_ids: set[str] = set()
        for year_folder in self._year_folders(request):
            entry_ids |= self.scanner.scan_year_folder_entry_ids(
                year_folder.folder,
                project_numbers=_selected_project_numbers(request),
            )
        return entry_ids

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

    def _year_folders(self, request: ScanRequest) -> list[YearFolder]:
        """The year folder, then the same year in each archive folder of the root.

        Archived projects move to e.g. "Boite de reception/00-Archives/2026"; their
        mails are archived like the others.
        """
        root = request.outlook_root_folder.strip("/")
        year_folders: list[YearFolder] = []
        missing: OutlookFolderNotFoundError | None = None
        try:
            year_folders.append(
                YearFolder(
                    self.folder_resolver.resolve_folder_path(
                        [request.outlook_root_folder, request.year],
                        account_identifier=request.account_identifier,
                    ),
                    root,
                )
            )
        except OutlookFolderNotFoundError as exc:
            missing = exc
        root_folder = self.folder_resolver.resolve_folder_path(
            request.outlook_root_folder,
            account_identifier=request.account_identifier,
        )
        for child in iter_com_collection(getattr(root_folder, "Folders", [])):
            name = str(getattr(child, "Name", "")).strip()
            if not is_archive_folder_name(name):
                continue
            archived_year = child_folder_named(child, request.year)
            if archived_year is not None:
                year_folders.append(YearFolder(archived_year, f"{root}/{name}", archived=True))
        if not year_folders:
            if missing is not None:
                raise missing
            msg = f"Dossier Outlook introuvable: {request.outlook_root_folder}/{request.year}"
            raise OutlookFolderNotFoundError(msg)
        return year_folders


def _selected_project_numbers(request: ScanRequest) -> set[str] | None:
    if request.project_numbers is not None:
        return {
            normalized
            for value in request.project_numbers
            if (normalized := normalize_project_filter(request.year, value)) is not None
        }
    project_number = normalize_project_filter(request.year, request.project_number)
    return {project_number} if project_number else None
