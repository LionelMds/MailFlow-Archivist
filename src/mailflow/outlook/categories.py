from __future__ import annotations

from typing import Any

ARCHIVED_CATEGORY = "ProjectFlow - Archivé"


def split_categories(categories: str | None) -> list[str]:
    if not categories:
        return []
    return [part.strip() for part in categories.split(";") if part.strip()]


def has_archived_category(categories: str | None) -> bool:
    return ARCHIVED_CATEGORY in split_categories(categories)


def mark_archived(item: Any) -> None:
    existing = split_categories(getattr(item, "Categories", ""))
    if ARCHIVED_CATEGORY not in existing:
        existing.append(ARCHIVED_CATEGORY)
        item.Categories = "; ".join(existing)
    save = getattr(item, "Save", None)
    if callable(save):
        save()
