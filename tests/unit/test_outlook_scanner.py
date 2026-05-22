from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from mailflow.models import Direction
from mailflow.outlook.attachments import PR_ATTACH_CONTENT_ID
from mailflow.outlook.scanner import OutlookScanner


class FakePropertyAccessor:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def GetProperty(self, schema: str) -> object:
        if schema not in self.values:
            raise RuntimeError("missing property")
        return self.values[schema]


def attachment(filename: str, *, properties: dict[str, object] | None = None) -> object:
    return SimpleNamespace(
        FileName=filename,
        DisplayName=filename,
        PropertyAccessor=FakePropertyAccessor(properties),
    )


class FakeCollection:
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self._items[index - 1]


def mail_item(entry_id: str) -> object:
    return SimpleNamespace(
        EntryID=entry_id,
        MessageClass="IPM.Note",
        Subject="Offre",
        SenderName="Dupont",
        SenderEmailAddress="sales@dupont.test",
        Recipients=[],
        SentOn=datetime(2026, 5, 6, 10, 30),
        Attachments=[],
        Body="Bonjour",
        Categories="",
    )


class DirectoryOnlyMailItem:
    EntryID = "ENTRY-LIGHT"
    MessageClass = "IPM.Note"
    Subject = "Contact"
    SenderName = "AIG"
    SenderEmailAddress = "contact@gva.ch"
    Recipients: list[object] = []
    SentOn = datetime(2026, 5, 6, 10, 30)

    @property
    def Body(self) -> str:
        raise AssertionError("directory import must not read mail body")

    @property
    def Attachments(self) -> object:
        raise AssertionError("directory import must not read attachments")

    @property
    def Categories(self) -> str:
        raise AssertionError("directory import must not read categories")


def test_iter_project_folders_filters_names() -> None:
    folders = FakeCollection(
        [
            SimpleNamespace(Name="2025-4893"),
            SimpleNamespace(Name="Archive"),
            SimpleNamespace(Name="2025-4893-2"),
            SimpleNamespace(Name="2026-4995"),
        ]
    )
    year_folder = SimpleNamespace(Folders=folders)

    names = [folder.Name for folder in OutlookScanner().iter_project_folders(year_folder)]

    assert names == ["2025-4893", "2026-4995"]


def test_scan_year_folder_scans_only_project_folders() -> None:
    valid_project = SimpleNamespace(
        Name="2025-4893",
        Items=FakeCollection(
            [
                SimpleNamespace(
                    EntryID="ENTRY-1",
                    MessageClass="IPM.Note",
                    Subject="Offre",
                    SenderName="Dupont",
                    SenderEmailAddress="sales@dupont.test",
                    Recipients=[],
                    SentOn=datetime(2026, 5, 6, 10, 30),
                    Attachments=[],
                    Body="Bonjour",
                    Categories="",
                )
            ]
        ),
    )
    ignored_folder = SimpleNamespace(Name="Archive", Items=FakeCollection([]))
    year_folder = SimpleNamespace(
        Name="2025",
        Folders=FakeCollection([valid_project, ignored_folder]),
    )

    mails = OutlookScanner().scan_year_folder(
        year_folder,
        outlook_root_path="Boite de reception",
    )

    assert len(mails) == 1
    assert mails[0].project_number == "2025-4893"
    assert mails[0].outlook_folder == "Boite de reception/2025/2025-4893"


def test_scan_all_project_folders_recurses_under_root() -> None:
    project_2025 = SimpleNamespace(
        Name="2025-4893 (Marquise)",
        Items=FakeCollection([mail_item("ENTRY-2025")]),
        Folders=FakeCollection([]),
    )
    project_2026 = SimpleNamespace(
        Name="2026-4995",
        Items=FakeCollection([mail_item("ENTRY-2026")]),
        Folders=FakeCollection([]),
    )
    root = SimpleNamespace(
        Name="Boite de reception",
        Folders=FakeCollection(
            [
                SimpleNamespace(
                    Name="2025",
                    Items=FakeCollection([]),
                    Folders=FakeCollection([project_2025]),
                ),
                SimpleNamespace(
                    Name="Archive",
                    Items=FakeCollection([]),
                    Folders=FakeCollection([]),
                ),
                SimpleNamespace(
                    Name="2026",
                    Items=FakeCollection([]),
                    Folders=FakeCollection([project_2026]),
                ),
            ]
        ),
    )

    scanned = OutlookScanner().scan_all_project_folders_with_items(
        root,
        outlook_root_path="Boite de reception",
    )

    assert [item.metadata.entry_id for item in scanned] == ["ENTRY-2025", "ENTRY-2026"]
    assert [item.metadata.outlook_folder for item in scanned] == [
        "Boite de reception/2025/2025-4893",
        "Boite de reception/2026/2026-4995",
    ]


def test_scan_all_project_folders_uses_lightweight_directory_metadata() -> None:
    project = SimpleNamespace(
        Name="2025-4893",
        Items=FakeCollection([DirectoryOnlyMailItem()]),
        Folders=FakeCollection([]),
    )
    root = SimpleNamespace(
        Name="Boite de reception",
        Folders=FakeCollection(
            [SimpleNamespace(Name="2025", Folders=FakeCollection([project]))]
        ),
    )

    scanned = OutlookScanner().scan_all_project_folders_with_items(
        root,
        outlook_root_path="Boite de reception",
    )

    assert scanned[0].metadata.sender_email == "contact@gva.ch"
    assert scanned[0].metadata.body_excerpt == ""
    assert scanned[0].metadata.attachment_names == []


def test_mail_item_to_metadata_maps_received_mail() -> None:
    item = SimpleNamespace(
        EntryID="ENTRY-1",
        ConversationID="CONV-1",
        MessageClass="IPM.Note",
        Subject="Offre",
        SenderName="Dupont SA",
        SenderEmailAddress="sales@dupont.test",
        Recipients=FakeCollection([SimpleNamespace(Address="lionel@balzmetal.test")]),
        SentOn=datetime(2026, 5, 6, 10, 30),
        Attachments=FakeCollection(
            [
                attachment("logo.png", properties={PR_ATTACH_CONTENT_ID: "cid-logo"}),
                attachment("offre.xlsx"),
            ]
        ),
        Body="Bonjour\n\nCordialement,\nSignature",
        Categories="Important; Project",
    )

    metadata = OutlookScanner(account_email="lionel@balzmetal.test").mail_item_to_metadata(
        item,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
    )

    assert metadata.direction == Direction.RECEIVED
    assert metadata.attachment_names == ["offre.xlsx"]
    assert metadata.recipients == ["lionel@balzmetal.test"]
    assert "Signature" not in metadata.body_excerpt


def test_mail_item_to_metadata_maps_sent_mail() -> None:
    item = SimpleNamespace(
        EntryID="ENTRY-2",
        MessageClass="IPM.Note",
        Subject="Commande",
        SenderName="Lionel",
        SenderEmailAddress="lionel@balzmetal.test",
        Recipients=[],
        SentOn=datetime(2026, 5, 6, 10, 30),
        Attachments=[],
        Body="Bonjour",
        Categories="",
    )

    metadata = OutlookScanner(account_email="lionel@balzmetal.test").mail_item_to_metadata(
        item,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
    )

    assert metadata.direction == Direction.SENT


def test_mail_item_to_metadata_orders_to_recipients_before_internal_cc() -> None:
    item = SimpleNamespace(
        EntryID="ENTRY-3",
        MessageClass="IPM.Note",
        Subject="Plan",
        SenderName="Lionel",
        SenderEmailAddress="lionel@balzmetal.test",
        Recipients=FakeCollection(
            [
                SimpleNamespace(Address="andre@balzmetal.ch", Type=2),
                SimpleNamespace(Address="blaise.riva@gva.ch", Type=1),
            ]
        ),
        SentOn=datetime(2026, 5, 6, 10, 30),
        Attachments=[],
        Body="Bonjour",
        Categories="",
    )

    metadata = OutlookScanner(account_email="lionel@balzmetal.test").mail_item_to_metadata(
        item,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
    )

    assert metadata.recipients == ["blaise.riva@gva.ch", "andre@balzmetal.ch"]
