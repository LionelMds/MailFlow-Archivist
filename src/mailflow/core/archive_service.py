from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mailflow.models import ArchiveDecision, ArchivedMailRecord, MailMetadata
from mailflow.outlook.categories import mark_archived
from mailflow.outlook.exporter import ExportResult, OutlookExporter
from mailflow.storage.sqlite_store import SQLiteArchiveStore


class ArchiveService:
    def __init__(self, *, exporter: OutlookExporter, store: SQLiteArchiveStore) -> None:
        self.exporter = exporter
        self.store = store

    def archive(self, item: Any, metadata: MailMetadata, decision: ArchiveDecision) -> ExportResult:
        result = self.exporter.export_mail(item, metadata, decision)
        try:
            self._record(metadata, decision, result)
        except Exception:
            # Without its journal entry the mail would be archived again as a copy:
            # take back the files this call has just created, and only those.
            for path in (result.msg_path, *result.attachment_paths):
                path.unlink(missing_ok=True)
            raise
        try:
            mark_archived(item)
        except Exception:
            result.warnings.append(
                f"{metadata.subject or metadata.entry_id} : archive locale enregistree, "
                "mais la categorie Outlook n'a pas pu etre appliquee."
            )
        return result

    def _record(
        self, metadata: MailMetadata, decision: ArchiveDecision, result: ExportResult,
    ) -> None:
        self.store.record_archived(
            ArchivedMailRecord(
                outlook_entry_id=metadata.entry_id,
                conversation_id=metadata.conversation_id,
                internet_message_id=metadata.internet_message_id,
                project_number=metadata.project_number,
                subject=metadata.subject,
                sender=metadata.sender_email or metadata.sender_name,
                sent_at=metadata.sent_at,
                msg_path=result.msg_path,
                target_folder=decision.target_relative_folder,
                classification=decision.mail_type,
                confidence=decision.confidence,
                archived_at=datetime.now(UTC),
            )
        )
