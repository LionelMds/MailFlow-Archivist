from __future__ import annotations

from types import SimpleNamespace

import pytest

from mailflow.outlook.client import OutlookClient, OutlookFolderNotFoundError


class FakeCollection:
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self._items[index - 1]


def folder(
    name: str,
    children: list[object] | None = None,
    store_name: str | None = None,
) -> object:
    return SimpleNamespace(
        Name=name,
        Folders=FakeCollection(children or []),
        Store=SimpleNamespace(DisplayName=store_name or name, StoreID=store_name or name),
    )


def test_list_accounts_from_namespace() -> None:
    namespace = SimpleNamespace(
        Accounts=FakeCollection(
            [
                SimpleNamespace(DisplayName="Balz", SmtpAddress="lionel@balzmetal.test"),
                SimpleNamespace(DisplayName="Perso", SmtpAddress="lionel@example.test"),
            ]
        ),
        Folders=FakeCollection([]),
    )

    accounts = OutlookClient(namespace).list_accounts()

    assert [account.display_name for account in accounts] == ["Balz", "Perso"]
    assert accounts[0].smtp_address == "lionel@balzmetal.test"


def test_resolve_folder_path_ignores_accents() -> None:
    project = folder("2025-4893")
    year = folder("2025", [project])
    inbox = folder("Boîte de réception", [year])
    root = folder("Balz", [inbox])
    namespace = SimpleNamespace(Accounts=FakeCollection([]), Folders=FakeCollection([root]))

    resolved = OutlookClient(namespace).resolve_folder_path(
        "Boite de reception/2025/2025-4893",
        account_identifier="Balz",
    )

    assert resolved is project


def test_find_account_root_by_account_delivery_store() -> None:
    root = folder("Mailbox - Lionel", store_name="STORE-1")
    namespace = SimpleNamespace(
        Accounts=FakeCollection(
            [
                SimpleNamespace(
                    DisplayName="Balz",
                    SmtpAddress="lionel@balzmetal.test",
                    DeliveryStore=SimpleNamespace(
                        StoreID="STORE-1",
                        DisplayName="Mailbox - Lionel",
                    ),
                )
            ]
        ),
        Folders=FakeCollection([root]),
    )

    resolved = OutlookClient(namespace).find_account_root("lionel@balzmetal.test")

    assert resolved is root


def test_list_root_folder_paths_for_selected_account() -> None:
    inbox = folder("Boite de reception")
    sent = folder("Elements envoyes")
    root = folder("Mailbox - Lionel", [inbox, sent], store_name="STORE-1")
    namespace = SimpleNamespace(
        Accounts=FakeCollection(
            [
                SimpleNamespace(
                    DisplayName="Balz",
                    SmtpAddress="lionel@balzmetal.test",
                    DeliveryStore=SimpleNamespace(
                        StoreID="STORE-1",
                        DisplayName="Mailbox - Lionel",
                    ),
                )
            ]
        ),
        Folders=FakeCollection([root]),
    )

    folders = OutlookClient(namespace).list_root_folder_paths("lionel@balzmetal.test")

    assert folders == ["Boite de reception", "Elements envoyes"]


def test_resolve_folder_path_raises_for_missing_folder() -> None:
    root = folder("Balz", [])
    namespace = SimpleNamespace(Accounts=FakeCollection([]), Folders=FakeCollection([root]))

    with pytest.raises(OutlookFolderNotFoundError):
        OutlookClient(namespace).resolve_folder_path("Boite de reception/2025")
