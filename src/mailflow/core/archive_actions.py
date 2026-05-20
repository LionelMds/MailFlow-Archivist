from __future__ import annotations

from mailflow.models import PreviewAction, PreviewRow


def rows_to_archive(rows: list[PreviewRow], *, include_review: bool = False) -> list[PreviewRow]:
    allowed_actions = {PreviewAction.ARCHIVE}
    if include_review:
        allowed_actions.add(PreviewAction.REVIEW)
    return [row for row in rows if row.action in allowed_actions and row.decision.archive]


def mark_rows_ignored(rows: list[PreviewRow]) -> list[PreviewRow]:
    return [
        row.model_copy(update={"action": PreviewAction.IGNORE})
        for row in rows
    ]

