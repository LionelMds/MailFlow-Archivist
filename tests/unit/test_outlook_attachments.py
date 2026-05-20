from __future__ import annotations

from types import SimpleNamespace

from mailflow.outlook.attachments import (
    PR_ATTACH_CONTENT_ID,
    PR_ATTACH_MIME_TAG,
    PR_ATTACHMENT_HIDDEN,
    PR_RENDERING_POSITION,
    attachment_display_name,
    attachment_mime_type,
    is_inline_image_attachment,
)


class FakePropertyAccessor:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def GetProperty(self, schema: str) -> object:
        if schema not in self.values:
            raise RuntimeError("missing property")
        return self.values[schema]


def attachment(
    filename: str,
    *,
    properties: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        FileName=filename,
        DisplayName=filename,
        PropertyAccessor=FakePropertyAccessor(properties or {}),
    )


def test_detects_inline_image_from_content_id() -> None:
    item = attachment("logo.png", properties={PR_ATTACH_CONTENT_ID: "<image001@test>"})

    assert is_inline_image_attachment(item)


def test_detects_inline_image_from_hidden_flag() -> None:
    item = attachment(
        "signature.jpg",
        properties={
            PR_ATTACHMENT_HIDDEN: True,
            PR_ATTACH_MIME_TAG: "image/jpeg",
        },
    )

    assert is_inline_image_attachment(item)


def test_detects_inline_image_from_rendering_position() -> None:
    item = attachment("cid.gif", properties={PR_RENDERING_POSITION: 42})

    assert is_inline_image_attachment(item)


def test_regular_image_attachment_is_not_inline_without_outlook_markers() -> None:
    item = attachment("photo_chantier.jpg")

    assert not is_inline_image_attachment(item)


def test_non_image_hidden_attachment_is_not_inline_image() -> None:
    item = attachment("rapport.pdf", properties={PR_ATTACHMENT_HIDDEN: True})

    assert not is_inline_image_attachment(item)


def test_attachment_display_name_and_mime_type_fallbacks() -> None:
    item = attachment("schema.png")

    assert attachment_display_name(item) == "schema.png"
    assert attachment_mime_type(item) == "image/png"
