from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from mailflow.core.filenames import build_attachment_filename, suffix_copy_name
from mailflow.core.mail_file_plan import planned_msg_path
from mailflow.models import ArchiveDecision, MailMetadata
from mailflow.outlook.attachments import (
    attachment_display_name,
    is_inline_image_attachment,
)
from mailflow.outlook.scanner import iter_com_collection

OL_MSG = 3


class AttachmentConflictPolicy(StrEnum):
    KEEP_EXISTING = "keep_existing"
    OVERWRITE = "overwrite"
    CREATE_SUFFIXED_COPY = "create_suffixed_copy"


class ExportResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    msg_path: Path
    attachment_paths: list[Path]


class OutlookExporter:
    def export_mail(
        self,
        item: Any,
        metadata: MailMetadata,
        decision: ArchiveDecision,
        *,
        overwrite_msg: bool = False,
        attachment_policy: AttachmentConflictPolicy = AttachmentConflictPolicy.CREATE_SUFFIXED_COPY,
    ) -> ExportResult:
        if not decision.archive:
            msg = "Decision does not allow archiving this mail"
            raise ValueError(msg)

        if not decision.target_path.exists():
            raise FileNotFoundError(decision.target_path)
        msg_path = planned_msg_path(metadata, decision.target_path)
        if msg_path.exists() and not overwrite_msg:
            raise FileExistsError(msg_path)

        item.SaveAs(str(msg_path), OL_MSG)
        attachment_paths = self._export_attachments(item, msg_path, attachment_policy)
        return ExportResult(
            msg_path=msg_path,
            attachment_paths=attachment_paths,
        )

    def _export_attachments(
        self,
        item: Any,
        msg_path: Path,
        attachment_policy: AttachmentConflictPolicy,
    ) -> list[Path]:
        attachments = iter_com_collection(getattr(item, "Attachments", []))
        if not attachments:
            return []
        saved: list[Path] = []
        for attachment in attachments:
            if is_inline_image_attachment(attachment):
                continue
            original_name = attachment_display_name(attachment)
            target = msg_path.parent / build_attachment_filename(msg_path.stem, original_name)
            resolved_target = _resolve_attachment_conflict(target, attachment_policy)
            if resolved_target is None:
                continue
            attachment.SaveAsFile(str(resolved_target))
            saved.append(resolved_target)
        return saved


def _resolve_attachment_conflict(
    target: Path,
    policy: AttachmentConflictPolicy,
) -> Path | None:
    if not target.exists():
        return target
    if policy == AttachmentConflictPolicy.KEEP_EXISTING:
        return None
    if policy == AttachmentConflictPolicy.OVERWRITE:
        return target
    copy_index = 2
    candidate = target.with_name(suffix_copy_name(target.name, copy_index))
    while candidate.exists():
        copy_index += 1
        candidate = target.with_name(suffix_copy_name(target.name, copy_index))
    return candidate
