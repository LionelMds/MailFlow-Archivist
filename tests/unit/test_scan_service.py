from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from mailflow.core.scan_service import (
    DirectoryScanRequest,
    OutlookScanService,
    ProjectFolderOption,
    ScanRequest,
)
from mailflow.outlook.client import OutlookFolderNotFoundError
from mailflow.outlook.scanner import OutlookScanner


class FakeCollection:
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self._items[index - 1]


class FakeResolver:
    def __init__(self, folder: object) -> None:
        self.folder = folder
        self.calls: list[tuple[list[str], str | None]] = []

    def resolve_folder_path(
        self,
        path: str | list[str],
        *,
        account_identifier: str | None = None,
    ) -> object:
        self.calls.append((list(path) if not isinstance(path, str) else [path], account_identifier))
        return self.folder


def mail_item(entry_id: str) -> object:
    return SimpleNamespace(
        EntryID=entry_id,
        MessageClass="IPM.Note",
        Subject="Offre",
        SenderName="Dupont",
        SenderEmailAddress="sales@dupont.test",
        Recipients=[],
        SentOn=datetime(2026, 5, 6, 10, 30),
        Attachments=[],
        Body="Bonjour",
        Categories="",
    )


def test_scan_service_resolves_year_folder_and_scans_projects() -> None:
    project = SimpleNamespace(Name="2025-4893", Items=FakeCollection([mail_item("ENTRY-1")]))
    year_folder = SimpleNamespace(Name="2025", Folders=FakeCollection([project]))
    resolver = FakeResolver(year_folder)
    service = OutlookScanService(folder_resolver=resolver, scanner=OutlookScanner())

    mails = service.scan(
        ScanRequest(
            account_identifier="Balz",
            outlook_root_folder="Boite de reception",
            year="2025",
        )
    )

    assert resolver.calls == [
        (["Boite de reception", "2025"], "Balz"),
        (["Boite de reception"], "Balz"),
    ]
    assert [mail.entry_id for mail in mails] == ["ENTRY-1"]


def test_scan_service_filters_specific_project() -> None:
    selected = SimpleNamespace(Name="2025-4893", Items=FakeCollection([mail_item("ENTRY-1")]))
    other = SimpleNamespace(Name="2025-4999", Items=FakeCollection([mail_item("ENTRY-2")]))
    year_folder = SimpleNamespace(Name="2025", Folders=FakeCollection([selected, other]))
    service = OutlookScanService(
        folder_resolver=FakeResolver(year_folder),
        scanner=OutlookScanner(),
    )

    mails = service.scan(
        ScanRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
            project_number="2025-4893",
        )
    )

    assert [mail.entry_id for mail in mails] == ["ENTRY-1"]


def test_scan_service_filters_multiple_selected_projects() -> None:
    first = SimpleNamespace(
        Name="2025-4893",
        Items=FakeCollection([mail_item("ENTRY-1")]),
    )
    skipped = SimpleNamespace(
        Name="2025-4900",
        Items=FakeCollection([mail_item("ENTRY-2")]),
    )
    second = SimpleNamespace(
        Name="2025-4999 (Extension)",
        Items=FakeCollection([mail_item("ENTRY-3")]),
    )
    year_folder = SimpleNamespace(
        Name="2025",
        Folders=FakeCollection([first, skipped, second]),
    )
    service = OutlookScanService(
        folder_resolver=FakeResolver(year_folder),
        scanner=OutlookScanner(),
    )

    mails = service.scan(
        ScanRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
            project_numbers=("2025-4893", "4999"),
        )
    )

    assert [mail.entry_id for mail in mails] == ["ENTRY-1", "ENTRY-3"]


def test_scan_service_lists_project_folders_before_scan() -> None:
    project = SimpleNamespace(Name="2025-4893 (Marquise)", Items=FakeCollection([]))
    ignored = SimpleNamespace(Name="Archives", Items=FakeCollection([]))
    year_folder = SimpleNamespace(
        Name="2025",
        Folders=FakeCollection([project, ignored]),
    )
    service = OutlookScanService(
        folder_resolver=FakeResolver(year_folder),
        scanner=OutlookScanner(),
    )

    options = service.list_project_folders(
        ScanRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
        )
    )

    assert options == [
        ProjectFolderOption(
            project_number="2025-4893",
            folder_name="2025-4893 (Marquise)",
        )
    ]


def test_scan_service_reads_entry_ids_without_full_mail_scan() -> None:
    project = SimpleNamespace(
        Name="2025-4893",
        Items=FakeCollection([mail_item("ENTRY-1"), mail_item("ENTRY-2")]),
    )
    year_folder = SimpleNamespace(Name="2025", Folders=FakeCollection([project]))
    service = OutlookScanService(
        folder_resolver=FakeResolver(year_folder),
        scanner=OutlookScanner(),
    )

    entry_ids = service.scan_entry_ids(
        ScanRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
        )
    )

    assert entry_ids == {"ENTRY-1", "ENTRY-2"}


def test_scan_service_resolves_root_for_directory_import() -> None:
    project = SimpleNamespace(
        Name="2025-4893",
        Items=FakeCollection([mail_item("ENTRY-1")]),
        Folders=FakeCollection([]),
    )
    root = SimpleNamespace(
        Name="Boite de reception",
        Items=FakeCollection([]),
        Folders=FakeCollection([SimpleNamespace(Name="2025", Folders=FakeCollection([project]))]),
    )
    resolver = FakeResolver(root)
    service = OutlookScanService(folder_resolver=resolver, scanner=OutlookScanner())

    scanned = service.scan_all_project_folders_with_items(
        DirectoryScanRequest(
            account_identifier="Balz",
            outlook_root_folder="Boite de reception",
        )
    )

    assert resolver.calls == [(["Boite de reception"], "Balz")]
    assert [item.metadata.entry_id for item in scanned] == ["ENTRY-1"]


class TreeResolver:
    """Resolves real paths in a fake folder tree, like OutlookClient."""

    def __init__(self, root: Any) -> None:
        self.root = root

    def resolve_folder_path(
        self,
        path: str | list[str],
        *,
        account_identifier: str | None = None,
    ) -> object:
        parts = [path] if isinstance(path, str) else list(path)
        current = self.root
        for part in parts[1:]:
            children = [current.Folders.Item(i) for i in range(1, current.Folders.Count + 1)]
            matches = [child for child in children if child.Name == part]
            if not matches:
                raise OutlookFolderNotFoundError(f"Dossier Outlook introuvable: {part}")
            current = matches[0]
        return current


def folder(name: str, *children: Any, items: list[object] | None = None) -> Any:
    return SimpleNamespace(
        Name=name, Folders=FakeCollection(list(children)), Items=FakeCollection(items or []),
    )


def archived_tree(*, with_active_year: bool = True) -> Any:
    active = folder("2026-5107 (Caillebotis)", items=[mail_item("ACTIVE")])
    archived = folder("2026-4952 (Platelage)", items=[mail_item("ARCHIVED")])
    children = [folder("00-Archives", folder("2025"), folder("2026", archived))]
    if with_active_year:
        children.insert(0, folder("2026", active))
    return folder("Boite de reception", *children)


def request_2026() -> ScanRequest:
    return ScanRequest(
        account_identifier=None, outlook_root_folder="Boite de reception", year="2026",
    )


def test_year_scan_also_reads_the_archived_projects_of_the_year() -> None:
    service = OutlookScanService(
        folder_resolver=TreeResolver(archived_tree()), scanner=OutlookScanner(),
    )

    mails = service.scan(request_2026())
    options = service.list_project_folders(request_2026())
    entry_ids = service.scan_entry_ids(request_2026())

    assert [(mail.entry_id, mail.outlook_folder) for mail in mails] == [
        ("ACTIVE", "Boite de reception/2026/2026-5107"),
        ("ARCHIVED", "Boite de reception/00-Archives/2026/2026-4952"),
    ]
    assert options == [
        ProjectFolderOption("2026-5107", "2026-5107 (Caillebotis)"),
        ProjectFolderOption("2026-4952", "2026-4952 (Platelage)", archived=True),
    ]
    assert entry_ids == {"ACTIVE", "ARCHIVED"}


def test_year_only_in_the_archives_is_still_scanned() -> None:
    service = OutlookScanService(
        folder_resolver=TreeResolver(archived_tree(with_active_year=False)),
        scanner=OutlookScanner(),
    )

    assert [mail.entry_id for mail in service.scan(request_2026())] == ["ARCHIVED"]


def test_missing_year_everywhere_is_reported() -> None:
    service = OutlookScanService(
        folder_resolver=TreeResolver(archived_tree()), scanner=OutlookScanner(),
    )

    with pytest.raises(OutlookFolderNotFoundError):
        service.scan(replace(request_2026(), year="2019"))
