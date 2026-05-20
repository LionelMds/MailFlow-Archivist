from __future__ import annotations

from pathlib import Path
from typing import Any

PR_ATTACH_CONTENT_ID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F"
PR_ATTACH_CONTENT_LOCATION = "http://schemas.microsoft.com/mapi/proptag/0x3713001F"
PR_ATTACH_MIME_TAG = "http://schemas.microsoft.com/mapi/proptag/0x370E001F"
PR_ATTACHMENT_HIDDEN = "http://schemas.microsoft.com/mapi/proptag/0x7FFE000B"
PR_RENDERING_POSITION = "http://schemas.microsoft.com/mapi/proptag/0x370B0003"

INLINE_IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


def attachment_display_name(attachment: Any) -> str:
    return str(
        getattr(attachment, "FileName", None)
        or getattr(attachment, "DisplayName", None)
        or "piece_jointe"
    )


def is_inline_image_attachment(attachment: Any) -> bool:
    if not _looks_like_image_attachment(attachment):
        return False
    if _mapi_text(attachment, PR_ATTACH_CONTENT_ID):
        return True
    if _mapi_text(attachment, PR_ATTACH_CONTENT_LOCATION):
        return True
    if _mapi_bool(attachment, PR_ATTACHMENT_HIDDEN):
        return True
    rendering_position = _mapi_int(attachment, PR_RENDERING_POSITION)
    return rendering_position is not None and rendering_position >= 0


def attachment_mime_type(attachment: Any) -> str:
    mime_type = _mapi_text(attachment, PR_ATTACH_MIME_TAG)
    if mime_type:
        return mime_type.lower()
    extension = Path(attachment_display_name(attachment)).suffix.lower()
    if extension in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if extension == ".png":
        return "image/png"
    if extension == ".gif":
        return "image/gif"
    if extension == ".webp":
        return "image/webp"
    if extension in {".bmp", ".tif", ".tiff"}:
        return f"image/{extension.lstrip('.')}"
    return "application/octet-stream"


def _looks_like_image_attachment(attachment: Any) -> bool:
    mime_type = _mapi_text(attachment, PR_ATTACH_MIME_TAG)
    if mime_type.lower().startswith("image/"):
        return True
    return Path(attachment_display_name(attachment)).suffix.lower() in INLINE_IMAGE_EXTENSIONS


def _mapi_text(attachment: Any, schema: str) -> str:
    value = _mapi_property(attachment, schema)
    if value is None:
        return ""
    return str(value).strip().strip("<>")


def _mapi_bool(attachment: Any, schema: str) -> bool:
    value = _mapi_property(attachment, schema)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "vrai"}
    return False


def _mapi_int(attachment: Any, schema: str) -> int | None:
    value = _mapi_property(attachment, schema)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _mapi_property(attachment: Any, schema: str) -> object | None:
    accessor = getattr(attachment, "PropertyAccessor", None)
    getter = getattr(accessor, "GetProperty", None)
    if not callable(getter):
        return None
    try:
        value: object = getter(schema)
        return value
    except Exception:
        return None
