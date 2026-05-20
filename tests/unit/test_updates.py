from __future__ import annotations

from pathlib import Path
from typing import Any

from mailflow.core.update_installer import (
    download_update_installer,
    installer_command,
    launch_update_installer,
)
from mailflow.core.updates import (
    ReleaseAsset,
    check_for_updates,
    is_version_newer,
    normalize_version,
    select_installer_asset,
)


def fake_release_payload(_url: str, _timeout: float) -> dict[str, Any]:
    return {
        "tag_name": "v0.5.0",
        "html_url": "https://github.test/releases/v0.5.0",
        "assets": [
            {
                "name": "MailFlow-Archivist-Setup.exe",
                "browser_download_url": "https://github.test/setup.exe",
                "size": 123,
            },
            {
                "name": "MailFlow-Archivist.dmg",
                "browser_download_url": "https://github.test/app.dmg",
                "size": 456,
            },
        ],
    }


def test_version_comparison_handles_v_prefix_and_patch_numbers() -> None:
    assert normalize_version("v0.4.1") == "0.4.1"
    assert is_version_newer("0.4.10", "0.4.2")
    assert not is_version_newer("0.4.1", "0.4.1")


def test_check_for_updates_selects_windows_installer() -> None:
    result = check_for_updates(
        "0.4.1",
        platform_system="Windows",
        fetch_json=fake_release_payload,
    )

    assert result.update_available
    assert result.latest_version == "0.5.0"
    assert result.installer_asset is not None
    assert result.installer_asset.name == "MailFlow-Archivist-Setup.exe"


def test_select_installer_asset_uses_dmg_on_macos() -> None:
    assets = fake_release_payload("", 0)["assets"]
    release_assets = [
        ReleaseAsset(
            name=str(asset["name"]),
            browser_download_url=str(asset["browser_download_url"]),
            size=int(asset["size"]),
        )
        for asset in assets
    ]

    selected = select_installer_asset(release_assets, platform_system="Darwin")

    assert selected is not None
    assert selected.name.endswith(".dmg")


def test_download_update_installer_writes_safe_filename(tmp_path: Path) -> None:
    asset = ReleaseAsset(
        name="../MailFlow-Archivist-Setup.exe",
        browser_download_url="https://github.test/setup.exe",
        size=3,
    )

    path = download_update_installer(
        asset,
        download_dir=tmp_path,
        downloader=lambda _url, _timeout: b"exe",
    )

    assert path == tmp_path / "MailFlow-Archivist-Setup.exe"
    assert path.read_bytes() == b"exe"


def test_launch_update_installer_returns_platform_command(tmp_path: Path) -> None:
    installer = tmp_path / "MailFlow-Archivist.dmg"
    calls: list[list[str]] = []

    command = launch_update_installer(
        installer,
        platform_system="Darwin",
        launcher=calls.append,
    )

    assert command == ["open", str(installer)]
    assert calls == [command]


def test_installer_command_uses_exe_directly_on_windows(tmp_path: Path) -> None:
    installer = tmp_path / "MailFlow-Archivist-Setup.exe"

    assert installer_command(installer, platform_system="Windows") == [str(installer)]
