from __future__ import annotations

import shutil
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    warnings: list[str] = Field(default_factory=list)


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

        with tempfile.TemporaryDirectory(
            prefix=".mailflow-export-", dir=msg_path.parent,
        ) as temp_dir:
            staged_msg = Path(temp_dir) / msg_path.name
            item.SaveAs(str(staged_msg), OL_MSG)
            staged_attachments = self._export_attachments(item, staged_msg, attachment_policy)
            attachment_paths = _publish_export(
                staged_msg, msg_path, staged_attachments,
                overwrite_msg=overwrite_msg, attachment_policy=attachment_policy,
            )
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
            if resolved_target not in saved:
                saved.append(resolved_target)
        return saved


def _publish_export(
    staged_msg: Path,
    msg_path: Path,
    staged_attachments: list[Path],
    *,
    overwrite_msg: bool,
    attachment_policy: AttachmentConflictPolicy,
) -> list[Path]:
    published: list[tuple[Path, Path | None]] = []
    attachment_paths: list[Path] = []

    def publish(source: Path, target: Path, *, overwrite: bool) -> None:
        backup = None
        if overwrite and target.exists():
            backup = staged_msg.parent / f"backup-{len(published)}.bin"
            shutil.copyfile(target, backup)
        if overwrite:
            source.replace(target)
        else:
            # Exclusive creation prevents a concurrent export from being overwritten.
            output = target.open("xb")
            try:
                with output, source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        published.append((target, backup))

    try:
        for source in staged_attachments:
            target = _resolve_attachment_conflict(msg_path.parent / source.name, attachment_policy)
            if target is None:
                continue
            publish(
                source, target,
                overwrite=attachment_policy == AttachmentConflictPolicy.OVERWRITE,
            )
            attachment_paths.append(target)
        publish(staged_msg, msg_path, overwrite=overwrite_msg)
    except BaseException:
        for target, backup in reversed(published):
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                backup.replace(target)
        raise
    return attachment_paths


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
