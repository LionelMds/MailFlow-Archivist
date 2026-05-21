from __future__ import annotations

from mailflow.resources import APP_ICON_ICNS_PATH, APP_ICON_ICO_PATH, app_icon_path, app_logo_path


def test_official_logo_assets_are_packaged() -> None:
    assert app_logo_path().is_file()
    assert APP_ICON_ICO_PATH.is_file()
    assert APP_ICON_ICNS_PATH.is_file()
    assert app_icon_path() == APP_ICON_ICO_PATH
