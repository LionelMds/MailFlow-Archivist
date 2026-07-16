from __future__ import annotations

import struct

from mailflow.resources import APP_ICON_ICNS_PATH, APP_ICON_ICO_PATH, app_icon_path, app_logo_path


def test_official_logo_assets_are_packaged() -> None:
    assert app_logo_path().is_file()
    assert APP_ICON_ICO_PATH.is_file()
    assert APP_ICON_ICNS_PATH.is_file()
    assert app_icon_path("Windows") == APP_ICON_ICO_PATH
    assert app_icon_path("Darwin") == APP_ICON_ICNS_PATH


def test_official_logo_png_is_transparent_1024_source() -> None:
    data = app_logo_path().read_bytes()

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (1024, 1024)
    assert b"tRNS" in data or b"\x06" == data[25:26]


def test_platform_icons_have_expected_container_signatures() -> None:
    ico = APP_ICON_ICO_PATH.read_bytes()
    icns = APP_ICON_ICNS_PATH.read_bytes()

    assert ico[:4] == b"\x00\x00\x01\x00"
    assert struct.unpack("<H", ico[4:6])[0] >= 6
    assert icns[:4] == b"icns"
