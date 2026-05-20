from __future__ import annotations

import platform
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib import request

from platformdirs import user_downloads_path

from mailflow.config import APP_NAME
from mailflow.core.updates import ReleaseAsset

ByteDownloader = Callable[[str, float], bytes]
CommandLauncher = Callable[[list[str]], object]


def default_update_download_dir() -> Path:
    try:
        downloads = Path(user_downloads_path())
    except Exception:
        downloads = Path.home() / "Downloads"
    return downloads / f"{APP_NAME} Updates"


def download_update_installer(
    asset: ReleaseAsset,
    *,
    download_dir: Path | None = None,
    timeout: float = 120.0,
    downloader: ByteDownloader | None = None,
) -> Path:
    target_dir = download_dir or default_update_download_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(asset.name).name
    data = downloader(asset.browser_download_url, timeout) if downloader else _download_bytes(
        asset.browser_download_url,
        timeout,
    )
    if not data:
        msg = "Installateur telecharge vide"
        raise ValueError(msg)
    temp_target = target.with_name(f"{target.name}.tmp")
    temp_target.write_bytes(data)
    temp_target.replace(target)
    return target


def launch_update_installer(
    installer_path: Path,
    *,
    platform_system: str | None = None,
    launcher: CommandLauncher | None = None,
) -> list[str]:
    command = installer_command(installer_path, platform_system=platform_system)
    if launcher is not None:
        launcher(command)
    else:
        subprocess.Popen(command)
    return command


def installer_command(
    installer_path: Path,
    *,
    platform_system: str | None = None,
) -> list[str]:
    system = (platform_system or platform.system()).casefold()
    if system == "darwin":
        return ["open", str(installer_path)]
    return [str(installer_path)]


def _download_bytes(url: str, timeout: float) -> bytes:
    http_request = request.Request(
        url,
        headers={"User-Agent": "MailFlow-Archivist"},
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        return bytes(response.read())
