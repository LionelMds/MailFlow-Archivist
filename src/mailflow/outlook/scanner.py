from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mailflow.core.body_cleaner import clean_body
from mailflow.core.project_paths import (
    extract_project_number_from_folder_name,
    is_project_folder_name,
)
from mailflow.models import Direction, MailMetadata
from mailflow.outlook.categories import split_categories

INTERNET_MESSAGE_ID_SCHEMA = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"


@dataclass(frozen=True)
class ScannedMail:
    item: object
    metadata: MailMetadata


class OutlookScanner:
    def __init__(self, *, account_email: str = "") -> None:
        self.account_email = account_email.lower()

    def iter_project_folders(self, year_folder: Any) -> Iterable[Any]:
        for folder in iter_com_collection(getattr(year_folder, "Folders", [])):
            if is_project_folder_name(str(getattr(folder, "Name", ""))):
                yield folder

    def scan_year_folder(
        self,
        year_folder: Any,
        *,
        outlook_root_path: str,
        project_numbers: set[str] | None = None,
    ) -> list[MailMetadata]:
        return [
            scanned.metadata
            for scanned in self.scan_year_folder_with_items(
                year_folder,
                outlook_root_path=outlook_root_path,
                project_numbers=project_numbers,
            )
        ]

    def scan_year_folder_with_items(
        self,
        year_folder: Any,
        *,
        outlook_root_path: str,
        project_numbers: set[str] | None = None,
    ) -> list[ScannedMail]:
        year_name = str(getattr(year_folder, "Name", ""))
        scanned_mails: list[ScannedMail] = []
        for project_folder in self.iter_project_folders(year_folder):
            project_number = _project_number_from_folder(project_folder)
            if project_numbers is not None and project_number not in project_numbers:
                continue
            outlook_path = "/".join(
                [
                    outlook_root_path.strip("/"),
                    year_name,
                    project_number,
                ]
            )
            scanned_mails.extend(self.scan_project_folder_with_items(project_folder, outlook_path))
        return scanned_mails

    def scan_project_folder(self, project_folder: Any, outlook_path: str) -> list[MailMetadata]:
        return [
            scanned.metadata
            for scanned in self.scan_project_folder_with_items(project_folder, outlook_path)
        ]

    def scan_project_folder_with_items(
        self,
        project_folder: Any,
        outlook_path: str,
    ) -> list[ScannedMail]:
        project_number = _project_number_from_folder(project_folder)
        return [
            ScannedMail(
                item=item,
                metadata=self.mail_item_to_metadata(
                    item,
                    project_number=project_number,
                    outlook_folder=outlook_path,
                ),
            )
            for item in iter_com_collection(getattr(project_folder, "Items", []))
            if _looks_like_mail_item(item)
        ]

    def mail_item_to_metadata(
        self,
        item: Any,
        *,
        project_number: str,
        outlook_folder: str,
    ) -> MailMetadata:
        sender_email = _text_attr(item, "SenderEmailAddress")
        sent_at = _coerce_datetime(
            getattr(item, "SentOn", None)
            or getattr(item, "ReceivedTime", None)
            or getattr(item, "CreationTime", None)
        )
        return MailMetadata(
            entry_id=_text_attr(item, "EntryID"),
            conversation_id=_optional_text_attr(item, "ConversationID"),
            internet_message_id=_internet_message_id(item),
            project_number=project_number,
            outlook_folder=outlook_folder,
            direction=_direction(sender_email, self.account_email),
            subject=_text_attr(item, "Subject"),
            sender_name=_text_attr(item, "SenderName"),
            sender_email=sender_email,
            recipients=_recipient_names(getattr(item, "Recipients", [])),
            sent_at=sent_at,
            attachment_names=_attachment_names(getattr(item, "Attachments", [])),
            body_excerpt=clean_body(_text_attr(item, "Body")),
            categories=split_categories(_optional_text_attr(item, "Categories")),
        )


def iter_com_collection(collection: Any) -> list[Any]:
    if collection is None:
        return []
    count = getattr(collection, "Count", None)
    if isinstance(count, int):
        return [collection.Item(index) for index in range(1, count + 1)]
    return list(collection)


def _looks_like_mail_item(item: Any) -> bool:
    message_class = str(getattr(item, "MessageClass", "IPM.Note"))
    return message_class.startswith("IPM.Note")


def _project_number_from_folder(project_folder: Any) -> str:
    folder_name = str(getattr(project_folder, "Name", ""))
    project_number = extract_project_number_from_folder_name(folder_name)
    if project_number is None:
        msg = f"Dossier Outlook projet invalide: {folder_name}"
        raise ValueError(msg)
    return project_number


def _direction(sender_email: str, account_email: str) -> Direction:
    if account_email and sender_email.lower() == account_email:
        return Direction.SENT
    return Direction.RECEIVED


def _recipient_names(recipients: Any) -> list[str]:
    names = []
    for recipient in iter_com_collection(recipients):
        names.append(_text_attr(recipient, "Address") or _text_attr(recipient, "Name"))
    return [name for name in names if name]


def _attachment_names(attachments: Any) -> list[str]:
    names = []
    for attachment in iter_com_collection(attachments):
        names.append(_text_attr(attachment, "FileName") or _text_attr(attachment, "DisplayName"))
    return [name for name in names if name]


def _internet_message_id(item: Any) -> str | None:
    accessor = getattr(item, "PropertyAccessor", None)
    getter = getattr(accessor, "GetProperty", None)
    if not callable(getter):
        return None
    try:
        return str(getter(INTERNET_MESSAGE_ID_SCHEMA))
    except Exception:
        return None


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "isoformat"):
        return datetime.fromisoformat(value.isoformat())
    msg = "Outlook mail item has no usable date"
    raise ValueError(msg)


def _text_attr(item: Any, attr: str) -> str:
    return str(getattr(item, attr, "") or "")


def _optional_text_attr(item: Any, attr: str) -> str | None:
    value = getattr(item, attr, None)
    if value is None:
        return None
    return str(value)
