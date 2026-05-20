from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any

from mailflow.core import cloud_files


def test_is_probably_onedrive_path_detects_business_folder() -> None:
    path = PureWindowsPath(
        r"C:\Users\Lionel\OneDrive - Balz Metal Sa\Clients\2025\2025-4893\plan.pdf"
    )

    assert cloud_files.is_probably_onedrive_path(path)


def test_request_local_availability_pins_onedrive_file_on_windows(monkeypatch: Any) -> None:
    calls: list[list[str]] = []

    def fake_run_attrib(args: list[str]) -> None:
        calls.append(args)

    monkeypatch.setattr(cloud_files, "_is_windows", lambda: True)
    monkeypatch.setattr(cloud_files, "_run_attrib", fake_run_attrib)

    cloud_files.request_local_availability(
        PureWindowsPath(
            r"C:\Users\Lionel\OneDrive - Balz Metal Sa\Clients\2025\2025-4893\plan.pdf"
        )
    )

    assert calls
    assert calls[0][:3] == ["attrib", "+P", "-U"]


def test_request_local_availability_ignores_non_onedrive_files(monkeypatch: Any) -> None:
    calls: list[list[str]] = []

    def fake_run_attrib(args: list[str]) -> None:
        calls.append(args)

    monkeypatch.setattr(cloud_files, "_is_windows", lambda: True)
    monkeypatch.setattr(cloud_files, "_run_attrib", fake_run_attrib)

    cloud_files.request_local_availability(PureWindowsPath(r"C:\Temp\plan.pdf"))

    assert calls == []
