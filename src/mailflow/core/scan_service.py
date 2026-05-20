from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mailflow.core.project_paths import normalize_project_filter
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
        year_folder = self.folder_resolver.resolve_folder_path(
            [request.outlook_root_folder, request.year],
            account_identifier=request.account_identifier,
        )
        project_number = normalize_project_filter(request.year, request.project_number)
        project_numbers = {project_number} if project_number else None
        return self.scanner.scan_year_folder_with_items(
            year_folder,
            outlook_root_path=request.outlook_root_folder,
            project_numbers=project_numbers,
        )
