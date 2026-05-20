from __future__ import annotations

import json
import platform
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib import request

DEFAULT_RELEASE_API_URL = (
    "https://api.github.com/repos/LionelMds/MailFlow-Archivist/releases/latest"
)

JsonFetcher = Callable[[str, float], Mapping[str, Any]]


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    browser_download_url: str
    size: int


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    html_url: str
    assets: tuple[ReleaseAsset, ...]

    @property
    def version(self) -> str:
        return normalize_version(self.tag_name)


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    release_url: str
    update_available: bool
    installer_asset: ReleaseAsset | None


def check_for_updates(
    current_version: str,
    *,
    api_url: str = DEFAULT_RELEASE_API_URL,
    platform_system: str | None = None,
    timeout: float = 15.0,
    fetch_json: JsonFetcher | None = None,
) -> UpdateCheckResult:
    release = fetch_latest_release(api_url, timeout=timeout, fetch_json=fetch_json)
    installer_asset = select_installer_asset(
        release.assets,
        platform_system=platform_system,
    )
    latest_version = release.version
    normalized_current = normalize_version(current_version)
    return UpdateCheckResult(
        current_version=normalized_current,
        latest_version=latest_version,
        release_url=release.html_url,
        update_available=is_version_newer(latest_version, normalized_current),
        installer_asset=installer_asset,
    )


def fetch_latest_release(
    api_url: str = DEFAULT_RELEASE_API_URL,
    *,
    timeout: float = 15.0,
    fetch_json: JsonFetcher | None = None,
) -> ReleaseInfo:
    data = fetch_json(api_url, timeout) if fetch_json is not None else _fetch_json(api_url, timeout)
    tag_name = _required_string(data, "tag_name")
    html_url = _required_string(data, "html_url")
    raw_assets = data.get("assets", [])
    if not isinstance(raw_assets, list):
        raw_assets = []
    assets = tuple(_asset_from_json(asset) for asset in raw_assets if isinstance(asset, dict))
    return ReleaseInfo(tag_name=tag_name, html_url=html_url, assets=assets)


def select_installer_asset(
    assets: Sequence[ReleaseAsset],
    *,
    platform_system: str | None = None,
) -> ReleaseAsset | None:
    system = platform_system or platform.system()
    lowered_system = system.casefold()
    if lowered_system == "windows":
        return _first_matching_asset(assets, suffix=".exe", preferred_text="setup")
    if lowered_system == "darwin":
        return _first_matching_asset(assets, suffix=".dmg")
    return None


def normalize_version(value: str) -> str:
    cleaned = value.strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]
    return cleaned


def is_version_newer(candidate: str, current: str) -> bool:
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    length = max(len(candidate_parts), len(current_parts), 1)
    padded_candidate = candidate_parts + (0,) * (length - len(candidate_parts))
    padded_current = current_parts + (0,) * (length - len(current_parts))
    return padded_candidate > padded_current


def _first_matching_asset(
    assets: Sequence[ReleaseAsset],
    *,
    suffix: str,
    preferred_text: str | None = None,
) -> ReleaseAsset | None:
    matching = [asset for asset in assets if asset.name.casefold().endswith(suffix)]
    if preferred_text is not None:
        preferred = [
            asset for asset in matching if preferred_text.casefold() in asset.name.casefold()
        ]
        if preferred:
            return preferred[0]
    return matching[0] if matching else None


def _version_parts(value: str) -> tuple[int, ...]:
    normalized = normalize_version(value)
    return tuple(int(part) for part in re.findall(r"\d+", normalized))


def _fetch_json(api_url: str, timeout: float) -> Mapping[str, Any]:
    http_request = request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MailFlow-Archivist",
        },
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        msg = "La reponse de mise a jour est invalide"
        raise ValueError(msg)
    return data


def _asset_from_json(data: Mapping[str, Any]) -> ReleaseAsset:
    return ReleaseAsset(
        name=_required_string(data, "name"),
        browser_download_url=_required_string(data, "browser_download_url"),
        size=_optional_int(data.get("size")),
    )


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"Champ release invalide: {key}"
        raise ValueError(msg)
    return value


def _optional_int(value: object) -> int:
    if isinstance(value, int):
        return value
    return 0
