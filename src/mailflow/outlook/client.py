from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any

from mailflow.models import OutlookAccount


class OutlookUnavailableError(RuntimeError):
    pass


class OutlookFolderNotFoundError(RuntimeError):
    pass


class OutlookClient:
    def __init__(self, namespace: Any | None = None) -> None:
        self._namespace = namespace

    @property
    def namespace(self) -> Any:
        if self._namespace is None:
            self._namespace = self._connect_namespace()
        return self._namespace

    def list_accounts(self) -> list[OutlookAccount]:
        accounts = []
        for account in _iter_com_collection(getattr(self.namespace, "Accounts", [])):
            accounts.append(
                OutlookAccount(
                    display_name=str(getattr(account, "DisplayName", "")),
                    smtp_address=getattr(account, "SmtpAddress", None),
                )
            )
        return accounts

    def list_root_folder_paths(self, account_identifier: str | None = None) -> list[str]:
        root = self.find_account_root(account_identifier)
        folder_names = []
        for folder in _iter_com_collection(getattr(root, "Folders", [])):
            name = str(getattr(folder, "Name", "")).strip()
            if name:
                folder_names.append(name)
        return folder_names

    def find_account_root(self, account_identifier: str | None = None) -> Any:
        roots = _iter_com_collection(getattr(self.namespace, "Folders", []))
        if not roots:
            msg = "Aucun dossier racine Outlook disponible"
            raise OutlookFolderNotFoundError(msg)
        if account_identifier is None or not account_identifier.strip():
            return roots[0]

        wanted = _normalize_name(account_identifier)
        for root in roots:
            if _folder_matches(root, wanted):
                return root

        for account in _iter_com_collection(getattr(self.namespace, "Accounts", [])):
            if _account_matches(account, wanted):
                delivery_store = getattr(account, "DeliveryStore", None)
                for root in roots:
                    if _same_store(root, delivery_store):
                        return root

        msg = f"Compte Outlook introuvable: {account_identifier}"
        raise OutlookFolderNotFoundError(msg)

    def resolve_folder_path(
        self,
        path: str | Sequence[str],
        *,
        account_identifier: str | None = None,
    ) -> Any:
        parts = _split_path(path)
        if not parts:
            msg = "Le chemin Outlook ne peut pas etre vide"
            raise OutlookFolderNotFoundError(msg)

        current = self.find_account_root(account_identifier)
        if _normalize_name(parts[0]) == _normalize_name(str(getattr(current, "Name", ""))):
            parts = parts[1:]
        for part in parts:
            current = _find_child_folder(current, part)
        return current

    def _connect_namespace(self) -> Any:
        try:
            import win32com.client
        except Exception as exc:
            msg = "Outlook classique et pywin32 sont requis pour scanner Outlook"
            raise OutlookUnavailableError(msg) from exc
        outlook = win32com.client.Dispatch("Outlook.Application")
        return outlook.GetNamespace("MAPI")


def _iter_com_collection(collection: Any) -> list[Any]:
    if collection is None:
        return []
    count = getattr(collection, "Count", None)
    if isinstance(count, int):
        return [collection.Item(index) for index in range(1, count + 1)]
    return list(collection)


def _split_path(path: str | Sequence[str]) -> list[str]:
    if isinstance(path, str):
        return [part.strip() for part in path.replace("\\", "/").split("/") if part.strip()]
    return [str(part).strip() for part in path if str(part).strip()]


def _find_child_folder(parent: Any, name: str) -> Any:
    wanted = _normalize_name(name)
    for child in _iter_com_collection(getattr(parent, "Folders", [])):
        if _normalize_name(str(getattr(child, "Name", ""))) == wanted:
            return child
    parent_name = str(getattr(parent, "Name", ""))
    msg = f"Dossier Outlook introuvable sous {parent_name}: {name}"
    raise OutlookFolderNotFoundError(msg)


def _folder_matches(folder: Any, wanted: str) -> bool:
    names = [
        getattr(folder, "Name", ""),
        getattr(getattr(folder, "Store", None), "DisplayName", ""),
    ]
    return any(_normalize_name(str(name)) == wanted for name in names if name)


def _account_matches(account: Any, wanted: str) -> bool:
    names = [
        getattr(account, "DisplayName", ""),
        getattr(account, "SmtpAddress", ""),
    ]
    return any(_normalize_name(str(name)) == wanted for name in names if name)


def _same_store(root: Any, store: Any) -> bool:
    if store is None:
        return False
    root_store = getattr(root, "Store", None)
    root_id = getattr(root_store, "StoreID", None)
    store_id = getattr(store, "StoreID", None)
    if root_id is not None and store_id is not None:
        return str(root_id) == str(store_id)
    return getattr(root_store, "DisplayName", None) == getattr(store, "DisplayName", None)


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())
