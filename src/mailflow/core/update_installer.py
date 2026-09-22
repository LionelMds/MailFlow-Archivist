from __future__ import annotations

import hashlib
import platform
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from urllib import request
from urllib.parse import urlsplit

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
    if urlsplit(asset.browser_download_url).scheme.lower() != "https":
        raise ValueError("Le telechargement d'une mise a jour exige une URL HTTPS.")
    target_dir = download_dir or default_update_download_dir()
    filename = PureWindowsPath(asset.name).name
    if (
        not filename
        or filename in {".", ".."}
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)
        or filename.endswith((".", " "))
    ):
        raise ValueError("Nom de fichier de mise a jour invalide.")
    target = target_dir / filename
    data = downloader(asset.browser_download_url, timeout) if downloader else _download_bytes(
        asset.browser_download_url,
        timeout,
    )
    if not data:
        msg = "Installateur telecharge vide"
        raise ValueError(msg)
    if asset.size > 0 and len(data) != asset.size:
        raise ValueError("Taille de l'installateur incorrecte : telechargement incomplet.")
    if asset.digest is not None:
        if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", asset.digest):
            raise ValueError("Empreinte de verification de l'installateur invalide.")
        expected_digest = asset.digest.split(":", maxsplit=1)[1].lower()
        if hashlib.sha256(data).hexdigest() != expected_digest:
            raise ValueError("L'empreinte de l'installateur ne correspond pas a la release.")
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".mailflow-update-", dir=target_dir, delete=False,
    ) as stream:
        temp_target = Path(stream.name)
        try:
            stream.write(data)
        except BaseException:
            stream.close()
            temp_target.unlink(missing_ok=True)
            raise
    try:
        temp_target.replace(target)
    finally:
        temp_target.unlink(missing_ok=True)
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
