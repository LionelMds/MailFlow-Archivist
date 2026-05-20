from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from mailflow.core.scan_service import OutlookScanService, ScanRequest
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

    assert resolver.calls == [(["Boite de reception", "2025"], "Balz")]
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
