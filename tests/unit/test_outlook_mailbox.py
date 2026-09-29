from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mailflow.models import Direction
from mailflow.outlook.attachments import PR_ATTACH_CONTENT_ID
from mailflow.outlook.client import OutlookClient, child_folder_named
from mailflow.outlook.mailbox import (
    OL_FOLDER_SENT_MAIL,
    MailboxSource,
    MailboxSourceKind,
    OutlookMailbox,
)
from mailflow.outlook.scanner import OutlookScanner


class FakeCollection:
    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> Any:
        return self._items[index - 1]


class SortableItems(FakeCollection):
    """Outlook Items with Sort, GetFirst and GetNext, counting the items read."""

    def __init__(self, items: list[Any]) -> None:
        super().__init__(items)
        self.sorted_by: tuple[str, bool] | None = None
        self.read = 0
        self._position = 0

    def Sort(self, key: str, descending: bool) -> None:
        self.sorted_by = (key, descending)
        self._items.sort(
            key=lambda item: item.ReceivedTime.replace(tzinfo=None), reverse=descending,
        )

    def GetFirst(self) -> Any:
        self._position = 0
        return self.GetNext()

    def GetNext(self) -> Any:
        if self._position >= len(self._items):
            return None
        item = self._items[self._position]
        self._position += 1
        self.read += 1
        return item


class FakeFolder:
    def __init__(
        self,
        name: str,
        children: list[FakeFolder] | None = None,
        items: list[Any] | None = None,
        store: Any = None,
    ) -> None:
        self.Name = name
        self.Folders = FakeCollection(children or [])
        self.Items = FakeCollection(items or [])
        self.Store = store


def mail(entry_id: str, received: datetime, **values: Any) -> Any:
    item = SimpleNamespace(
        EntryID=entry_id,
        MessageClass="IPM.Note",
        Subject="Offre",
        SenderName="Dupont",
        SenderEmailAddress="sales@dupont.test",
        Recipients=[],
        SentOn=received,
        ReceivedTime=received,
        Attachments=[],
        Body="Bonjour\n\nDe: ancien message 2025-4893",
        Categories="",
    )
    for name, value in values.items():
        setattr(item, name, value)
    return item


def mailbox_tree(*, sent_default: bool = True, pending_under_inbox: bool = True) -> Any:
    projects_2025 = FakeFolder("2025", [
        FakeFolder("2025-4893 Villa Dupont"),
        FakeFolder("2025-5012"),
        FakeFolder("Divers", [FakeFolder("2025-6000 Hangar")]),
    ])
    pending = FakeFolder("À classer")
    inbox = FakeFolder(
        "Boîte de réception",
        [projects_2025, FakeFolder("2026", [FakeFolder("2026-0012 Garage")]),
         *([pending] if pending_under_inbox else [])],
    )
    sent = FakeFolder("Éléments envoyés")
    store = SimpleNamespace(
        DisplayName="lionel@balzmetal.ch",
        GetDefaultFolder=(
            (lambda kind: sent if kind == OL_FOLDER_SENT_MAIL else None)
            if sent_default
            else _missing_default_folder
        ),
    )
    root = FakeFolder(
        "lionel@balzmetal.ch",
        [inbox, sent, *([] if pending_under_inbox else [pending])],
        store=store,
    )
    namespace = SimpleNamespace(Folders=FakeCollection([root]), Accounts=FakeCollection([]))
    return SimpleNamespace(
        client=OutlookClient(namespace), inbox=inbox, sent=sent, pending=pending, root=root,
    )


def _missing_default_folder(_kind: int) -> Any:
    raise RuntimeError("no default folder in this store")


def outlook_mailbox(tree: Any) -> OutlookMailbox:
    return OutlookMailbox(tree.client, OutlookScanner(account_email="lionel@balzmetal.ch"))


def sources_of(tree: Any, **options: Any) -> tuple[list[MailboxSource], list[str]]:
    return outlook_mailbox(tree).sources(
        account_identifier=None,
        outlook_root_folder="Boite de reception",
        pending_folder_name=options.get("pending", "A CLASSER"),
        kinds=options.get("kinds", frozenset(MailboxSourceKind)),
    )


@pytest.mark.parametrize("pending_under_inbox", [True, False])
@pytest.mark.parametrize("sent_default", [True, False])
def test_sources_find_inbox_pending_and_sent_folders(
    pending_under_inbox: bool, sent_default: bool,
) -> None:
    tree = mailbox_tree(sent_default=sent_default, pending_under_inbox=pending_under_inbox)

    sources, warnings = sources_of(tree)

    assert [(source.kind, source.folder) for source in sources] == [
        (MailboxSourceKind.INBOX, tree.inbox),
        (MailboxSourceKind.PENDING, tree.pending),
        (MailboxSourceKind.SENT, tree.sent),
    ]
    assert [source.label for source in sources] == [
        "Boîte de réception", "À classer", "Éléments envoyés",
    ]
    assert warnings == []


def test_missing_pending_folder_is_a_warning_and_other_sources_stay() -> None:
    tree = mailbox_tree()

    sources, warnings = sources_of(tree, pending="Tri manuel")

    assert [source.kind for source in sources] == [
        MailboxSourceKind.INBOX, MailboxSourceKind.SENT,
    ]
    assert warnings == ["Dossier Outlook « Tri manuel » introuvable."]


def test_pending_folder_can_be_given_as_a_path_and_sources_can_be_limited() -> None:
    tree = mailbox_tree()

    sources, _warnings = sources_of(
        tree,
        pending="Boite de reception/A classer",
        kinds=frozenset({MailboxSourceKind.PENDING}),
    )

    assert [source.folder for source in sources] == [tree.pending]


def test_project_folder_index_covers_every_year_and_nested_folder() -> None:
    tree = mailbox_tree()

    folders = outlook_mailbox(tree).project_folders(
        tree.inbox, outlook_root_folder="Boite de reception",
    )

    assert sorted(folders) == ["2025-4893", "2025-5012", "2025-6000", "2026-0012"]
    assert folders["2025-4893"].folder_name == "2025-4893 Villa Dupont"
    assert folders["2025-4893"].outlook_path == "Boite de reception/2025/2025-4893"
    assert folders["2025-6000"].outlook_path == "Boite de reception/2025/Divers/2025-6000"


def test_recent_mails_stop_at_the_first_older_mail_once_sorted() -> None:
    items = SortableItems([
        mail("OLD", datetime(2026, 1, 5)),
        mail("NEW", datetime(2026, 9, 20, tzinfo=UTC)),
        SimpleNamespace(MessageClass="IPM.Schedule.Meeting.Request",
                        ReceivedTime=datetime(2026, 9, 10)),
        mail("MID", datetime(2026, 8, 1)),
        mail("OLDER", datetime(2025, 3, 1)),
    ])
    folder = FakeFolder("Éléments envoyés")
    folder.Items = items

    found = outlook_mailbox(mailbox_tree()).mail_items(folder, since=datetime(2026, 7, 1))

    assert [item.EntryID for item in found] == ["NEW", "MID"]
    assert items.sorted_by == ("[ReceivedTime]", True)
    assert items.read == 4


def test_mail_items_without_sort_keep_only_mails_of_the_period() -> None:
    folder = FakeFolder("A CLASSER", items=[
        mail("NEW", datetime(2026, 9, 20)),
        mail("OLD", datetime(2026, 1, 5)),
        SimpleNamespace(MessageClass="REPORT.IPM.Note.NDR"),
    ])
    mailbox = outlook_mailbox(mailbox_tree())

    assert [item.EntryID for item in mailbox.mail_items(folder, since=None)] == ["NEW", "OLD"]
    assert [
        item.EntryID for item in mailbox.mail_items(folder, since=datetime(2026, 7, 1))
    ] == ["NEW"]


def test_read_mail_keeps_the_full_body_and_marks_sent_items() -> None:
    item = mail("SENT-1", datetime(2026, 9, 20), SenderEmailAddress="autre@exemple.test")
    mailbox = outlook_mailbox(mailbox_tree())

    read = mailbox.read_mail(
        item, MailboxSource(MailboxSourceKind.SENT, "Éléments envoyés", None),
    )

    assert read.metadata.direction == Direction.SENT
    assert read.metadata.project_number == ""
    assert read.metadata.outlook_folder == "Éléments envoyés"
    # The quoted history is kept for the search, unlike the cleaned AI excerpt.
    assert "2025-4893" in read.body
    assert "2025-4893" not in read.metadata.body_excerpt


class SavedAttachment:
    def __init__(self, name: str, content: bytes, **values: Any) -> None:
        self.FileName = name
        self.DisplayName = name
        self.content = content
        self.saved_to: list[Path] = []
        for key, value in values.items():
            setattr(self, key, value)

    def SaveAsFile(self, path: str) -> None:
        self.saved_to.append(Path(path))
        Path(path).write_bytes(self.content)


class InlineLogo(SavedAttachment):
    @property
    def PropertyAccessor(self) -> Any:
        return SimpleNamespace(
            GetProperty=lambda schema: "logo@cid" if schema == PR_ATTACH_CONTENT_ID else None
        )


def test_attachment_texts_read_supported_files_and_clean_up() -> None:
    offer = SavedAttachment("Offre.txt", b"Chantier 2025-5012")
    photo = SavedAttachment("photo.jpg", b"2025-4893")
    logo = InlineLogo("logo.png", b"2025-4893")
    linked = SavedAttachment("plan.pdf", b"", Type=4)
    huge = SavedAttachment("gros.txt", b"2025-4893", Size=500 * 1024 * 1024)
    item = mail(
        "A", datetime(2026, 9, 20),
        Attachments=FakeCollection([offer, photo, logo, linked, huge]),
    )

    texts = outlook_mailbox(mailbox_tree()).attachment_texts(item)

    assert texts == ["Chantier 2025-5012"]
    assert [saved.name for saved in offer.saved_to] == ["piece-1.txt"]
    assert photo.saved_to == [] and logo.saved_to == [] and linked.saved_to == []
    assert huge.saved_to == []
    assert not offer.saved_to[0].parent.exists()


def test_attachment_that_cannot_be_saved_is_skipped() -> None:
    class Blocked(SavedAttachment):
        def SaveAsFile(self, path: str) -> None:
            raise RuntimeError("blocked by policy")

    item = mail(
        "A", datetime(2026, 9, 20),
        Attachments=FakeCollection([Blocked("devis.pdf", b""), SavedAttachment("a.csv", b"2025")]),
    )

    assert outlook_mailbox(mailbox_tree()).attachment_texts(item) == ["2025"]


def test_copy_moves_a_duplicate_and_move_moves_the_original() -> None:
    target = FakeFolder("2025-5012")
    calls: list[tuple[str, Any]] = []
    duplicate = SimpleNamespace(Move=lambda folder: calls.append(("duplicate moved", folder)))

    def copy() -> Any:
        calls.append(("copied", None))
        return duplicate

    item = SimpleNamespace(
        Copy=copy,
        Move=lambda folder: calls.append(("moved", folder)),
        Display=lambda: calls.append(("displayed", None)),
    )
    mailbox = outlook_mailbox(mailbox_tree())

    mailbox.copy(item, target)
    mailbox.move(item, target)
    mailbox.display(item)

    assert calls == [
        ("copied", None), ("duplicate moved", target), ("moved", target), ("displayed", None),
    ]


def test_child_folder_lookup_ignores_case_and_accents() -> None:
    tree = mailbox_tree()

    assert child_folder_named(tree.inbox, "a classer") is tree.pending
    assert child_folder_named(tree.inbox, "Inconnu") is None
