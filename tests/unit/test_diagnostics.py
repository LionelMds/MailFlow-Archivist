from __future__ import annotations

from types import SimpleNamespace

from mailflow.diagnostics import collect_outlook_diagnostics, format_outlook_diagnostics
from mailflow.models import OutlookAccount


class FakeCollection:
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self._items[index - 1]


class FakeClient:
    def __init__(self) -> None:
        self.namespace = SimpleNamespace(
            Folders=FakeCollection(
                [
                    SimpleNamespace(Name="Mailbox - Lionel", Folders=FakeCollection([object()])),
                    SimpleNamespace(Name="Archives", Folders=FakeCollection([])),
                ]
            )
        )

    def list_accounts(self) -> list[OutlookAccount]:
        return [OutlookAccount(display_name="Balz", smtp_address="lionel@balzmetal.test")]


def test_collect_outlook_diagnostics() -> None:
    diagnostics = collect_outlook_diagnostics(FakeClient())  # type: ignore[arg-type]

    assert diagnostics.accounts == ["Balz <lionel@balzmetal.test>"]
    assert diagnostics.root_folders[0].name == "Mailbox - Lionel"
    assert diagnostics.root_folders[0].child_count == 1


def test_format_outlook_diagnostics() -> None:
    text = format_outlook_diagnostics(collect_outlook_diagnostics(FakeClient()))  # type: ignore[arg-type]

    assert "Comptes Outlook:" in text
    assert "Balz <lionel@balzmetal.test>" in text
    assert "Mailbox - Lionel (1 sous-dossiers)" in text
