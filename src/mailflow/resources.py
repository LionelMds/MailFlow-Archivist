from __future__ import annotations

import platform
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_ROOT / "assets"
APP_LOGO_PATH = ASSETS_DIR / "mailflow-logo.png"
APP_ICON_ICO_PATH = ASSETS_DIR / "mailflow-icon.ico"
APP_ICON_ICNS_PATH = ASSETS_DIR / "mailflow-icon.icns"


def app_logo_path() -> Path:
    return APP_LOGO_PATH


def app_icon_path(platform_system: str | None = None) -> Path:
    system = platform_system or platform.system()
    if system == "Darwin" and APP_ICON_ICNS_PATH.exists():
        return APP_ICON_ICNS_PATH
    if system == "Windows" and APP_ICON_ICO_PATH.exists():
        return APP_ICON_ICO_PATH
    if APP_ICON_ICO_PATH.exists():
        return APP_ICON_ICO_PATH
    if APP_ICON_ICNS_PATH.exists():
        return APP_ICON_ICNS_PATH
    return APP_LOGO_PATH
