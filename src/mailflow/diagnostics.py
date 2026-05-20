from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mailflow.outlook.client import OutlookClient


@dataclass(frozen=True)
class OutlookFolderInfo:
    name: str
    child_count: int


@dataclass(frozen=True)
class OutlookDiagnostics:
    accounts: list[str]
    root_folders: list[OutlookFolderInfo]


def collect_outlook_diagnostics(client: OutlookClient | None = None) -> OutlookDiagnostics:
    outlook = client or OutlookClient()
    accounts = [
        _format_account(account.display_name, account.smtp_address)
        for account in outlook.list_accounts()
    ]
    roots = [
        OutlookFolderInfo(
            name=str(getattr(folder, "Name", "")),
            child_count=_collection_count(getattr(folder, "Folders", [])),
        )
        for folder in _iter_com_collection(getattr(outlook.namespace, "Folders", []))
    ]
    return OutlookDiagnostics(accounts=accounts, root_folders=roots)


def format_outlook_diagnostics(diagnostics: OutlookDiagnostics) -> str:
    lines = ["Comptes Outlook:"]
    if diagnostics.accounts:
        lines.extend(f"- {account}" for account in diagnostics.accounts)
    else:
        lines.append("- Aucun compte detecte")

    lines.append("")
    lines.append("Dossiers racine:")
    if diagnostics.root_folders:
        lines.extend(
            f"- {folder.name} ({folder.child_count} sous-dossiers)"
            for folder in diagnostics.root_folders
        )
    else:
        lines.append("- Aucun dossier racine detecte")
    return "\n".join(lines)


def _format_account(display_name: str, smtp_address: str | None) -> str:
    if smtp_address:
        return f"{display_name} <{smtp_address}>"
    return display_name


def _collection_count(collection: object) -> int:
    count = getattr(collection, "Count", None)
    if isinstance(count, int):
        return count
    try:
        return len(list(_iter_com_collection(collection)))
    except TypeError:
        return 0


def _iter_com_collection(collection: Any) -> Iterable[object]:
    count = getattr(collection, "Count", None)
    if isinstance(count, int):
        for index in range(1, count + 1):
            yield collection.Item(index)
        return
    yield from collection
