from __future__ import annotations

from mailflow.models import PreviewAction, PreviewRow


def rows_to_archive(rows: list[PreviewRow], *, include_review: bool = False) -> list[PreviewRow]:
    allowed_actions = {PreviewAction.ARCHIVE}
    if include_review:
        allowed_actions.add(PreviewAction.REVIEW)
    return [row for row in rows if row.action in allowed_actions and row.decision.archive]


def mark_rows_ignored(
    rows: list[PreviewRow],
    row_indexes: list[int] | None = None,
) -> list[PreviewRow]:
    targets = _target_indexes(rows, row_indexes)
    return [
        row.model_copy(update={"action": PreviewAction.IGNORE})
        if index in targets and row.action != PreviewAction.ARCHIVED
        else row
        for index, row in enumerate(rows)
    ]


def mark_rows_archivable(rows: list[PreviewRow]) -> list[PreviewRow]:
    return [
        row.model_copy(update={"action": PreviewAction.ARCHIVE})
        if _can_restore_archive_action(row)
        else row
        for row in rows
    ]


def _can_restore_archive_action(row: PreviewRow) -> bool:
    return (
        row.action != PreviewAction.ARCHIVED
        and row.decision.archive
        and not row.decision.requires_review
    )


def _target_indexes(rows: list[PreviewRow], row_indexes: list[int] | None) -> set[int]:
    if row_indexes is None:
        return set(range(len(rows)))
    return {index for index in row_indexes if 0 <= index < len(rows)}
