from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mailflow.models import (
    ArchiveDecision,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
)
from mailflow.outlook.attachments import PR_ATTACH_CONTENT_ID
from mailflow.outlook.exporter import AttachmentConflictPolicy, OutlookExporter


class FakePropertyAccessor:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def GetProperty(self, schema: str) -> object:
        if schema not in self.values:
            raise RuntimeError("missing property")
        return self.values[schema]


class FakeAttachment:
    def __init__(
        self,
        filename: str,
        content: str,
        *,
        properties: dict[str, object] | None = None,
    ) -> None:
        self.FileName = filename
        self.content = content
        self.PropertyAccessor = FakePropertyAccessor(properties)

    def SaveAsFile(self, path: str) -> None:
        Path(path).write_text(self.content, encoding="utf-8")


class FakeCollection:
    def __init__(self, items: list[object]) -> None:
        self.items = items
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self.items[index - 1]


class FakeMailItem:
    def __init__(self, attachments: list[object] | None = None) -> None:
        self.Attachments = FakeCollection(attachments or [FakeAttachment("plan.pdf", "pdf")])
        self.saved_as: Path | None = None

    def SaveAs(self, path: str, _format: int) -> None:
        self.saved_as = Path(path)
        self.saved_as.write_text("msg", encoding="utf-8")


def metadata() -> MailMetadata:
    return MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre garde-corps",
        sender_name="Dupont SA",
        sender_email="sales@dupont.test",
        recipients=["lionel@balzmetal.test"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        attachment_names=["plan.pdf"],
    )


def decision(tmp_path: Path) -> ArchiveDecision:
    return ArchiveDecision(
        mail_id="ENTRY-1",
        project_number="2025-4893",
        archive=True,
        requires_review=False,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Demande de prix",
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="ok",
    )


def test_export_mail_saves_msg_and_attachments(tmp_path: Path) -> None:
    item = FakeMailItem()

    result = OutlookExporter().export_mail(item, metadata(), decision(tmp_path))

    assert result.msg_path.exists()
    assert result.msg_path.name == "1-R-Offre garde-corps.msg"
    assert item.saved_as is not None
    assert item.saved_as.name == result.msg_path.name
    assert not item.saved_as.exists()
    assert len(result.attachment_paths) == 1
    assert result.attachment_paths[0].parent == tmp_path
    assert result.attachment_paths[0].name == "1-R-Offre garde-corps - plan.pdf"
    assert result.attachment_paths[0].read_text(encoding="utf-8") == "pdf"
    assert not list(tmp_path.glob("*pieces jointes*"))


def test_export_mail_refuses_missing_destination_folder(tmp_path: Path) -> None:
    item = FakeMailItem()

    with pytest.raises(FileNotFoundError):
        OutlookExporter().export_mail(item, metadata(), decision(tmp_path / "missing"))


def test_export_mail_refuses_existing_msg_without_confirmation(tmp_path: Path) -> None:
    item = FakeMailItem()
    exporter = OutlookExporter()
    meta = metadata()
    dec = decision(tmp_path)
    exporter.export_mail(item, meta, dec)

    with pytest.raises(FileExistsError):
        exporter.export_mail(
            FakeMailItem(),
            meta.model_copy(update={"archive_order": 1}),
            dec,
        )


def test_export_attachment_creates_suffixed_copy_on_conflict(tmp_path: Path) -> None:
    item = FakeMailItem()
    exporter = OutlookExporter()
    meta = metadata()
    dec = decision(tmp_path)
    exporter.export_mail(item, meta, dec)

    result = exporter.export_mail(
        FakeMailItem(),
        meta.model_copy(update={"entry_id": "ENTRY-2"}),
        dec,
        attachment_policy=AttachmentConflictPolicy.CREATE_SUFFIXED_COPY,
    )

    assert result.attachment_paths[0].name == "2-R-Offre garde-corps - plan.pdf"


def test_export_mail_skips_inline_images_as_separate_attachments(tmp_path: Path) -> None:
    item = FakeMailItem(
        [
            FakeAttachment("logo.png", "image", properties={PR_ATTACH_CONTENT_ID: "cid-logo"}),
            FakeAttachment("plan.pdf", "pdf"),
        ]
    )

    result = OutlookExporter().export_mail(item, metadata(), decision(tmp_path))

    assert [path.name for path in result.attachment_paths] == [
        "1-R-Offre garde-corps - plan.pdf"
    ]
    assert not list(tmp_path.glob("*logo*"))


def test_attachment_export_failure_leaves_no_partial_archive(tmp_path: Path) -> None:
    class BrokenAttachment(FakeAttachment):
        def SaveAsFile(self, path: str) -> None:
            Path(path).write_bytes(b"partial")
            raise OSError("attachment unavailable")

    item = FakeMailItem([
        FakeAttachment("plan.pdf", "first"), BrokenAttachment("detail.pdf", ""),
    ])

    with pytest.raises(OSError):
        OutlookExporter().export_mail(item, metadata(), decision(tmp_path))

    assert not list(tmp_path.iterdir())
    result = OutlookExporter().export_mail(FakeMailItem(), metadata(), decision(tmp_path))
    assert result.msg_path.name.startswith("1-R-")


def test_failed_confirmed_overwrite_preserves_original_files(tmp_path: Path) -> None:
    exporter = OutlookExporter()
    result = exporter.export_mail(FakeMailItem(), metadata(), decision(tmp_path))
    original_msg = result.msg_path.read_bytes()
    original_attachment = result.attachment_paths[0].read_bytes()

    class BrokenMail(FakeMailItem):
        def SaveAs(self, path: str, _format: int) -> None:
            Path(path).write_bytes(b"partial replacement")
            raise OSError("Outlook disconnected")

    with pytest.raises(OSError):
        exporter.export_mail(
            BrokenMail(), metadata().model_copy(update={"archive_order": 1}), decision(tmp_path),
            overwrite_msg=True, attachment_policy=AttachmentConflictPolicy.OVERWRITE,
        )

    assert result.msg_path.read_bytes() == original_msg
    assert result.attachment_paths[0].read_bytes() == original_attachment


def test_concurrent_msg_creation_preserves_existing_mail_and_reverts_attachments(
    tmp_path: Path,
) -> None:
    expected_msg = tmp_path / "1-R-Offre garde-corps.msg"

    class RacingMail(FakeMailItem):
        def SaveAs(self, path: str, _format: int) -> None:
            super().SaveAs(path, _format)
            expected_msg.write_bytes(b"other process")

    with pytest.raises(FileExistsError):
        OutlookExporter().export_mail(RacingMail(), metadata(), decision(tmp_path))

    assert expected_msg.read_bytes() == b"other process"
    assert list(tmp_path.iterdir()) == [expected_msg]


def test_explicit_attachment_overwrite_supports_duplicate_attachment_names(tmp_path: Path) -> None:
    item = FakeMailItem([FakeAttachment("plan.pdf", "first"), FakeAttachment("plan.pdf", "last")])
    result = OutlookExporter().export_mail(
        item, metadata(), decision(tmp_path), attachment_policy=AttachmentConflictPolicy.OVERWRITE,
    )
    assert len(result.attachment_paths) == 1
    assert result.attachment_paths[0].read_text(encoding="utf-8") == "last"
