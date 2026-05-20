from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from mailflow.core.archive_actions import rows_to_archive
from mailflow.core.filenames import next_archive_order
from mailflow.models import ArchiveDecision, MailMetadata, PreviewRow
from mailflow.outlook.exporter import ExportResult


class ArchiveServiceProtocol(Protocol):
    def archive(
        self,
        item: object,
        metadata: MailMetadata,
        decision: ArchiveDecision,
    ) -> ExportResult:
        ...


@dataclass(frozen=True)
class ArchiveCandidate:
    item: object
    row: PreviewRow


@dataclass(frozen=True)
class ArchiveFailure:
    mail_id: str
    reason: str


@dataclass(frozen=True)
class ArchiveBatchResult:
    exported: list[ExportResult] = field(default_factory=list)
    exported_mail_ids: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failures: list[ArchiveFailure] = field(default_factory=list)

    @property
    def exported_count(self) -> int:
        return len(self.exported)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def failure_count(self) -> int:
        return len(self.failures)


class ArchiveBatchExecutor:
    def __init__(self, archive_service: ArchiveServiceProtocol) -> None:
        self.archive_service = archive_service

    def archive(
        self,
        candidates: list[ArchiveCandidate],
        *,
        include_review: bool = False,
    ) -> ArchiveBatchResult:
        ready_rows = set(
            id(row)
            for row in rows_to_archive(
                [candidate.row for candidate in candidates],
                include_review=include_review,
            )
        )
        archive_orders = _archive_orders_for_ready_candidates(
            [
                candidate
                for candidate in candidates
                if id(candidate.row) in ready_rows
            ]
        )
        result = ArchiveBatchResult()
        for candidate in candidates:
            row = candidate.row
            if id(row) not in ready_rows:
                result.skipped.append(row.mail.entry_id)
                continue
            try:
                metadata = row.mail.model_copy(
                    update={"archive_order": archive_orders[id(candidate)]}
                )
                result.exported.append(
                    self.archive_service.archive(candidate.item, metadata, row.decision)
                )
                result.exported_mail_ids.append(row.mail.entry_id)
            except Exception as exc:
                result.failures.append(ArchiveFailure(mail_id=row.mail.entry_id, reason=str(exc)))
        return result


def _archive_orders_for_ready_candidates(
    candidates: list[ArchiveCandidate],
) -> dict[int, int]:
    candidates_by_target: dict[Path, list[ArchiveCandidate]] = {}
    for candidate in candidates:
        candidates_by_target.setdefault(candidate.row.decision.target_path, []).append(candidate)

    orders: dict[int, int] = {}
    for target_path, target_candidates in candidates_by_target.items():
        next_order = next_archive_order(target_path)
        ordered_candidates = sorted(
            target_candidates,
            key=lambda candidate: (candidate.row.mail.sent_at, candidate.row.mail.entry_id),
        )
        for offset, candidate in enumerate(ordered_candidates):
            orders[id(candidate)] = next_order + offset
    return orders
