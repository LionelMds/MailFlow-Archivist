"""Outlook side of mailbox sorting: source folders, project folders, move and copy.

Mails are only moved or copied, never deleted. Attachments read for a project number
are written to a private temporary folder that is removed right after reading.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from mailflow.config import DEFAULT_MAILBOX_PENDING_FOLDER
from mailflow.core.attachment_text import (
    MAX_ATTACHMENT_BYTES,
    extract_attachment_text,
    is_searchable_attachment,
)
from mailflow.core.project_paths import extract_project_number_from_folder_name
from mailflow.models import Direction, MailMetadata
from mailflow.outlook.attachments import attachment_display_name, is_inline_image_attachment
from mailflow.outlook.client import OutlookClient, child_folder_named
from mailflow.outlook.scanner import OutlookScanner, iter_com_collection

OL_FOLDER_SENT_MAIL = 5
OL_BY_VALUE = 1
DEFAULT_PENDING_FOLDER = DEFAULT_MAILBOX_PENDING_FOLDER
SENT_FOLDER_NAMES = ("Elements envoyes", "Sent Items", "Gesendete Elemente", "Posta inviata")


class MailboxSourceKind(StrEnum):
    INBOX = "inbox"
    PENDING = "pending"
    SENT = "sent"


@dataclass(frozen=True)
class MailboxSource:
    kind: MailboxSourceKind
    label: str
    folder: Any


@dataclass(frozen=True)
class ProjectFolder:
    project_number: str
    folder_name: str
    outlook_path: str
    folder: Any


@dataclass(frozen=True)
class MailboxMail:
    metadata: MailMetadata
    body: str


class OutlookMailbox:
    def __init__(self, client: OutlookClient, scanner: OutlookScanner) -> None:
        self.client = client
        self.scanner = scanner

    def inbox(self, *, account_identifier: str | None, outlook_root_folder: str) -> Any:
        return self.client.resolve_folder_path(
            outlook_root_folder, account_identifier=account_identifier,
        )

    def sources(
        self,
        *,
        account_identifier: str | None,
        outlook_root_folder: str,
        pending_folder_name: str,
        kinds: frozenset[MailboxSourceKind],
    ) -> tuple[list[MailboxSource], list[str]]:
        """Return the folders to read and a warning for each one that is missing."""
        inbox = self.inbox(
            account_identifier=account_identifier, outlook_root_folder=outlook_root_folder,
        )
        sources: list[MailboxSource] = []
        warnings: list[str] = []
        if MailboxSourceKind.INBOX in kinds:
            sources.append(MailboxSource(
                MailboxSourceKind.INBOX, str(getattr(inbox, "Name", "")) or outlook_root_folder,
                inbox,
            ))
        if MailboxSourceKind.PENDING in kinds:
            pending = self._pending_folder(account_identifier, inbox, pending_folder_name)
            if pending is None:
                warnings.append(f"Dossier Outlook « {pending_folder_name} » introuvable.")
            else:
                sources.append(MailboxSource(
                    MailboxSourceKind.PENDING, str(getattr(pending, "Name", "")), pending,
                ))
        if MailboxSourceKind.SENT in kinds:
            sent = self._sent_folder(account_identifier)
            if sent is None:
                warnings.append("Dossier Outlook des éléments envoyés introuvable.")
            else:
                sources.append(MailboxSource(
                    MailboxSourceKind.SENT, str(getattr(sent, "Name", "")) or "Envoyés", sent,
                ))
        return sources, warnings

    def project_folders(self, inbox: Any, *, outlook_root_folder: str) -> dict[str, ProjectFolder]:
        folders: dict[str, ProjectFolder] = {}
        for folder, outlook_path in self.scanner.iter_project_folders_recursive(
            inbox, outlook_root_path=outlook_root_folder,
        ):
            name = str(getattr(folder, "Name", "")).strip()
            number = extract_project_number_from_folder_name(name)
            if number is not None:
                # With two folders for one number, the first one met keeps the mails.
                folders.setdefault(number, ProjectFolder(number, name, outlook_path, folder))
        return folders

    def mail_items(self, folder: Any, *, since: datetime | None) -> list[Any]:
        """Mails stored directly in the folder (not in its subfolders), newest first."""
        items = getattr(folder, "Items", None)
        if items is None:
            return []
        sort = getattr(items, "Sort", None)
        get_first = getattr(items, "GetFirst", None)
        if since is not None and callable(sort) and callable(get_first):
            # Sorting lets a large Sent Items folder stop at the first older mail.
            sort("[ReceivedTime]", True)
            found: list[Any] = []
            item = get_first()
            while item is not None:
                received = _item_datetime(item)
                if received is not None and received < since:
                    break
                if _looks_like_mail_item(item):
                    found.append(item)
                item = items.GetNext()
            return found
        return [
            item for item in iter_com_collection(items)
            if _looks_like_mail_item(item) and _is_recent(item, since)
        ]

    def read_mail(self, item: Any, source: MailboxSource) -> MailboxMail:
        metadata = self.scanner.mail_item_to_metadata(
            item, project_number="", outlook_folder=source.label,
        )
        if source.kind == MailboxSourceKind.SENT:
            metadata = metadata.model_copy(update={"direction": Direction.SENT})
        return MailboxMail(metadata=metadata, body=str(getattr(item, "Body", "") or ""))

    def attachment_texts(self, item: Any) -> list[str]:
        attachments = [
            attachment
            for attachment in iter_com_collection(getattr(item, "Attachments", []))
            if _is_readable_attachment(attachment)
        ]
        if not attachments:
            return []
        texts: list[str] = []
        with tempfile.TemporaryDirectory(
            prefix="mailflow-rangement-", ignore_cleanup_errors=True,
        ) as temp_dir:
            for index, attachment in enumerate(attachments, start=1):
                suffix = Path(attachment_display_name(attachment)).suffix.lower()
                path = Path(temp_dir) / f"piece-{index}{suffix}"
                try:
                    attachment.SaveAsFile(str(path))
                except Exception:
                    # A cloud link or a blocked file cannot be saved: skip it.
                    continue
                texts.append(extract_attachment_text(path))
                path.unlink(missing_ok=True)
        return texts

    def move(self, item: Any, folder: Any) -> None:
        item.Move(folder)

    def copy(self, item: Any, folder: Any) -> None:
        # Outlook creates the copy next to the original, then it is moved away.
        duplicate = item.Copy()
        duplicate.Move(folder)

    def display(self, item: Any) -> None:
        item.Display()

    def _pending_folder(
        self, account_identifier: str | None, inbox: Any, name: str,
    ) -> Any | None:
        cleaned = name.strip()
        if not cleaned:
            return None
        if "/" in cleaned or "\\" in cleaned:
            try:
                return self.client.resolve_folder_path(
                    cleaned, account_identifier=account_identifier,
                )
            except Exception:
                return None
        return child_folder_named(inbox, cleaned) or child_folder_named(
            self.client.find_account_root(account_identifier), cleaned,
        )

    def _sent_folder(self, account_identifier: str | None) -> Any | None:
        sent = self.client.default_folder(account_identifier, OL_FOLDER_SENT_MAIL)
        if sent is not None:
            return sent
        root = self.client.find_account_root(account_identifier)
        for name in SENT_FOLDER_NAMES:
            folder = child_folder_named(root, name)
            if folder is not None:
                return folder
        return None


def _is_readable_attachment(attachment: Any) -> bool:
    if is_inline_image_attachment(attachment):
        return False
    if not is_searchable_attachment(attachment_display_name(attachment)):
        return False
    attachment_type = getattr(attachment, "Type", OL_BY_VALUE)
    size = getattr(attachment, "Size", 0)
    too_large = isinstance(size, int) and size > MAX_ATTACHMENT_BYTES
    return attachment_type == OL_BY_VALUE and not too_large


def _looks_like_mail_item(item: Any) -> bool:
    return str(getattr(item, "MessageClass", "IPM.Note")).startswith("IPM.Note")


def _is_recent(item: Any, since: datetime | None) -> bool:
    if since is None:
        return True
    received = _item_datetime(item)
    return received is None or received >= since


def _item_datetime(item: Any) -> datetime | None:
    for name in ("ReceivedTime", "SentOn", "CreationTime"):
        value = getattr(item, name, None)
        if value is None:
            continue
        try:
            moment = value if isinstance(value, datetime) else datetime.fromisoformat(
                value.isoformat()
            )
        except (AttributeError, TypeError, ValueError):
            continue
        # pywin32 labels local times with a time zone: compare wall-clock values.
        return moment.replace(tzinfo=None)
    return None
