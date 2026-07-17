from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from mailflow.models import PreviewAction, PreviewRow


@dataclass(frozen=True)
class WatchChange:
    new_entry_ids: list[str]
    total_count: int

    @property
    def new_count(self) -> int:
        return len(self.new_entry_ids)


@dataclass
class WatchState:
    known_entry_ids: set[str] = field(default_factory=set)

    def reset(self, rows: Sequence[PreviewRow]) -> None:
        self.reset_entry_ids(_entry_ids(rows))

    def reset_entry_ids(self, entry_ids: Sequence[str] | set[str]) -> None:
        self.known_entry_ids = {entry_id for entry_id in entry_ids if entry_id}

    def update(self, rows: Sequence[PreviewRow]) -> WatchChange:
        return self.update_entry_ids(_entry_ids(rows))

    def update_entry_ids(self, entry_ids: Sequence[str] | set[str]) -> WatchChange:
        current = {entry_id for entry_id in entry_ids if entry_id}
        new_ids = sorted(current - self.known_entry_ids)
        self.known_entry_ids = current
        return WatchChange(new_entry_ids=new_ids, total_count=len(current))


@dataclass
class ReviewQueue:
    pending_entry_ids: set[str] = field(default_factory=set)

    @property
    def count(self) -> int:
        return len(self.pending_entry_ids)

    def clear(self) -> None:
        self.pending_entry_ids.clear()

    def sync(self, rows: Sequence[PreviewRow]) -> int:
        previous = set(self.pending_entry_ids)
        self.pending_entry_ids = review_entry_ids(rows)
        return len(self.pending_entry_ids - previous)


def _entry_ids(rows: Sequence[PreviewRow]) -> set[str]:
    return {row.mail.entry_id for row in rows}


def review_entry_ids(rows: Sequence[PreviewRow]) -> set[str]:
    return {
        row.mail.entry_id
        for row in rows
        if row.action == PreviewAction.REVIEW or row.decision.requires_review
    }
