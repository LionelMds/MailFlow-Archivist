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
        _row_marked_archivable(row) if _can_restore_archive_action(row) else row
        for row in rows
    ]


def _can_restore_archive_action(row: PreviewRow) -> bool:
    return row.action != PreviewAction.ARCHIVED and not _requires_manual_review(row)


def _row_marked_archivable(row: PreviewRow) -> PreviewRow:
    reason_note = "Archivage force par l'utilisateur."
    reason = row.decision.reason
    if reason_note not in reason:
        reason = f"{reason} {reason_note}".strip()
    decision = row.decision.model_copy(
        update={
            "archive": True,
            "requires_review": False,
            "reason": reason,
        }
    )
    return row.model_copy(update={"action": PreviewAction.ARCHIVE, "decision": decision})


def _requires_manual_review(row: PreviewRow) -> bool:
    return row.action == PreviewAction.REVIEW or row.decision.requires_review


def _target_indexes(rows: list[PreviewRow], row_indexes: list[int] | None) -> set[int]:
    if row_indexes is None:
        return set(range(len(rows)))
    return {index for index in row_indexes if 0 <= index < len(rows)}
