from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mailflow.core.app_controller import OutlookAppController
from mailflow.core.scan_service import DirectoryScanRequest, ScanRequest
from mailflow.models import MailMetadata, OutlookAccount, PreviewRow
from mailflow.outlook.scanner import ScannedMail


class FakeOutlookClient:
    def list_accounts(self) -> list[OutlookAccount]:
        return [
            OutlookAccount(display_name="Balz", smtp_address="lionel@balzmetal.test"),
            OutlookAccount(display_name="Shared", smtp_address=None),
        ]

    def list_root_folder_paths(self, account_identifier: str | None = None) -> list[str]:
        assert account_identifier == "lionel@balzmetal.test"
        return ["Boite de reception", "Archives"]


class FakeScanService:
    def scan(self, request: ScanRequest) -> list[MailMetadata]:
        return []

    def scan_with_items(self, request: ScanRequest) -> list[ScannedMail]:
        return []

    def scan_all_project_folders_with_items(
        self,
        request: DirectoryScanRequest,
    ) -> list[ScannedMail]:
        return []


class FakePreviewPipeline:
    def preview(
        self,
        mails: list[MailMetadata],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[PreviewRow]:
        if progress_callback is not None:
            for index, _mail in enumerate(mails, start=1):
                progress_callback(index, len(mails))
        return []


def test_outlook_app_controller_suggests_first_account_smtp(tmp_path: Path) -> None:
    controller = OutlookAppController(
        outlook_client=FakeOutlookClient(),
        scan_service=FakeScanService(),
        preview_pipeline=FakePreviewPipeline(),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )

    assert controller.suggested_account_identifier() == "lionel@balzmetal.test"


def test_outlook_app_controller_lists_accounts_and_root_folders(tmp_path: Path) -> None:
    controller = OutlookAppController(
        outlook_client=FakeOutlookClient(),
        scan_service=FakeScanService(),
        preview_pipeline=FakePreviewPipeline(),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )

    assert [account.display_name for account in controller.available_outlook_accounts()] == [
        "Balz",
        "Shared",
    ]
    assert controller.available_outlook_root_folders("lionel@balzmetal.test") == [
        "Boite de reception",
        "Archives",
    ]
