from __future__ import annotations

from pathlib import Path

from mailflow.core.filenames import build_msg_filename, next_archive_order
from mailflow.models import MailMetadata


def planned_msg_path(metadata: MailMetadata, target_path: Path) -> Path:
    order = metadata.archive_order or next_archive_order(target_path)
    filename = build_msg_filename(
        order,
        metadata.direction,
        metadata.subject,
    )
    return target_path / filename
