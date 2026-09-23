"""The directory learns from each scan and from manual client/supplier choices."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

from mailflow.config import AppPaths, AppSettings
from mailflow.core.app_controller import AppController, PreviewRequest
from mailflow.core.scan_service import (
    DirectoryScanRequest,
    ProjectFolderOption,
    ScanRequest,
)
from mailflow.models import (
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualClassificationUpdate,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)
from mailflow.outlook.scanner import ScannedMail
from mailflow.storage.directory_store import SQLiteDirectoryStore

PROJECT = "2025-4893"


def mail(entry_id: str, sender: str, *, name: str = "Jean Dupont") -> MailMetadata:
    return MailMetadata(
        entry_id=entry_id,
        project_number=PROJECT,
        outlook_folder=PROJECT,
        direction=Direction.RECEIVED,
        subject=f"Sujet {entry_id}",
        sender_name=name,
        sender_email=sender,
        recipients=["Lionel <lionel@balzmetal.ch>"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        attachment_names=[],
        body_excerpt="",
    )


def review_row(
    tmp_path: Path,
    metadata: MailMetadata,
    action: PreviewAction = PreviewAction.REVIEW,
) -> PreviewRow:
    return PreviewRow(
        mail=metadata,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=None,
                suggested_interlocutor=None,
                likely_archive=None,
                confidence=0.0,
                matched_rules=[],
            )
        ),
        decision=ArchiveDecision(
            mail_id=metadata.entry_id,
            project_number=PROJECT,
            archive=False,
            requires_review=True,
            mail_type=MailType.CORRESPONDANCE_GENERALE,
            interlocutor=InterlocutorType.INCONNU,
            target_relative_folder="A verifier",
            target_path=tmp_path / PROJECT,
            confidence=0.9,
            duplicate_status="none",
            reason="Role a confirmer.",
        ),
        action=action,
    )


class ScanService:
    def __init__(self, mails: list[MailMetadata]) -> None:
        self.mails = mails

    def scan(self, request: ScanRequest) -> list[MailMetadata]:
        return list(self.mails)

    def scan_with_items(self, request: ScanRequest) -> list[ScannedMail]:
        return [
            ScannedMail(item=object(), metadata=item)
            for item in self.mails
            if request.entry_ids is None or item.entry_id in request.entry_ids
        ]

    def scan_all_project_folders_with_items(
        self, request: DirectoryScanRequest,
    ) -> list[ScannedMail]:
        return []

    def list_project_folders(self, request: ScanRequest) -> list[ProjectFolderOption]:
        return []

    def scan_entry_ids(self, request: ScanRequest) -> set[str]:
        return {item.entry_id for item in self.mails}


class EmptyPipeline:
    def preview(
        self,
        mails: list[MailMetadata],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[PreviewRow]:
        return []


def controller(
    tmp_path: Path,
    store: SQLiteDirectoryStore,
    mails: list[MailMetadata] | None = None,
) -> AppController:
    (tmp_path / PROJECT).mkdir(exist_ok=True)
    return AppController(
        scan_service=ScanService(mails or []),
        preview_pipeline=EmptyPipeline(),
        projects_root=tmp_path,
        report_dir=tmp_path,
        directory_store=store,
    )


def request() -> PreviewRequest:
    return PreviewRequest(account_identifier=None, outlook_root_folder="Inbox", year="2025")


def client_update() -> ManualClassificationUpdate:
    return ManualClassificationUpdate(
        mail_type=MailType.CORRESPONDANCE_GENERALE,
        interlocutor=InterlocutorType.CLIENT,
        target_relative_folder="Correspondance",
    )


def test_scan_records_external_contacts_once(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    app = controller(tmp_path, store, [
        mail("1", "jean@acme-metal.ch"),
        mail("2", "jean@acme-metal.ch"),
        mail("3", "paul@acme-metal.ch", name="Paul Martin"),
    ])

    app.scan_and_preview(request())

    assert [entry.name for entry in store.list_organizations()] == ["Acme Metal"]
    assert len(store.list_organizations()[0].contacts) == 2
    update = app.last_scan_directory_update
    assert update is not None
    assert (update.contact_count, update.new_organizations, update.new_contacts) == (2, 1, 2)

    app.scan_and_preview(request())

    update = app.last_scan_directory_update
    assert update is not None
    assert (update.new_organizations, update.new_contacts) == (0, 0)


def test_scan_skips_internal_and_anonymous_generic_contacts(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    app = controller(tmp_path, store, [
        mail("1", "collegue@balzmetal.ch"),
        mail("2", "someone@gmail.com", name="Someone"),
    ])

    app.scan_and_preview(request())

    assert store.list_organizations() == []
    assert app.last_scan_directory_update is None


def test_watch_scan_also_records_new_contacts(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    app = controller(tmp_path, store, [mail("1", "jean@acme-metal.ch")])

    app.scan_incremental_preview(request(), ["1"])

    assert [entry.name for entry in store.list_organizations()] == ["Acme Metal"]


def test_manual_client_choice_updates_directory_and_other_mails(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    same_company = [mail("1", "jean@acme-metal.ch"), mail("2", "paul@acme-metal.ch")]
    other_company = mail("3", "info@steel-supply.ch")
    app = controller(tmp_path, store, [*same_company, other_company])
    app.scan_and_preview(request())
    app.preview_rows = [
        review_row(tmp_path, item) for item in [*same_company, other_company]
    ]

    updated = app.apply_manual_update(0, client_update())

    assert store.interlocutor_for_email(PROJECT, "jean@acme-metal.ch") == InterlocutorType.CLIENT
    assert store.interlocutor_for_email("2026-0001", "new@acme-metal.ch") == (
        InterlocutorType.CLIENT
    )
    assert updated.decision.target_relative_folder == "Correspondance/Acme Metal"
    sibling = app.preview_rows[1]
    assert sibling.decision.interlocutor == InterlocutorType.CLIENT
    assert sibling.decision.target_relative_folder == "Correspondance/Acme Metal"
    assert app.preview_rows[2].decision.interlocutor == InterlocutorType.INCONNU
    change = app.last_directory_role_change
    assert change is not None
    assert (change.organization_name, change.role, change.updated_row_count) == (
        "Acme Metal", InterlocutorType.CLIENT, 1,
    )


def test_manual_choice_adds_unknown_contact_to_directory(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    app = controller(tmp_path, store)
    app.preview_rows = [review_row(tmp_path, mail("1", "anna@gmail.com", name="Anna"))]

    app.apply_manual_update(0, client_update())

    assert store.interlocutor_for_email(PROJECT, "anna@gmail.com") == InterlocutorType.CLIENT
    # A generic mailbox never claims the whole domain for this person's company.
    assert store.interlocutor_for_email(PROJECT, "other@gmail.com") is None


def test_manual_unknown_role_leaves_directory_untouched(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    app = controller(tmp_path, store, [mail("1", "jean@acme-metal.ch")])
    app.scan_and_preview(request())
    app.preview_rows = [review_row(tmp_path, mail("1", "jean@acme-metal.ch"))]

    app.apply_manual_update(0, ManualClassificationUpdate(
        mail_type=MailType.A_VERIFIER,
        interlocutor=InterlocutorType.INCONNU,
        target_relative_folder="A verifier",
    ))

    assert store.interlocutor_for_email(PROJECT, "jean@acme-metal.ch") is None
    assert app.last_directory_role_change is None


def test_role_change_keeps_archived_and_ignored_mails(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    mails = [mail(str(index), "jean@acme-metal.ch") for index in range(3)]
    app = controller(tmp_path, store, mails)
    app.scan_and_preview(request())
    app.preview_rows = [
        review_row(tmp_path, mails[0]),
        review_row(tmp_path, mails[1], PreviewAction.ARCHIVED),
        review_row(tmp_path, mails[2], PreviewAction.IGNORE),
    ]

    app.apply_manual_update(0, client_update())

    assert app.preview_rows[1].decision.interlocutor == InterlocutorType.INCONNU
    assert app.preview_rows[1].action == PreviewAction.ARCHIVED
    assert app.preview_rows[2].action == PreviewAction.IGNORE


def test_manual_client_choice_is_logged_and_shown_in_directory(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtWidgets import QComboBox, QDialog

    from mailflow.ui.main_window import MainWindow
    from mailflow.ui.preview_table import interlocutor_option_label

    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    mails = [mail("1", "jean@acme-metal.ch"), mail("2", "paul@acme-metal.ch")]
    app = controller(tmp_path, store, mails)
    app.scan_and_preview(request())
    app.preview_rows = [review_row(tmp_path, item) for item in mails]

    def choose_client(dialog: QDialog) -> int:
        interlocutor = dialog.findChildren(QComboBox)[1]
        interlocutor.setCurrentText(interlocutor_option_label(InterlocutorType.CLIENT))
        return int(QDialog.DialogCode.Accepted.value)

    monkeypatch.setattr(QDialog, "exec", choose_client)
    window = cast(Any, MainWindow(
        AppSettings(paths=AppPaths(data_dir=tmp_path)), controller=app,
    ))
    window.mailflow_refresh_table()

    window.mailflow_preview_table.cellDoubleClicked.emit(0, 0)

    logs = window.mailflow_logs.toPlainText()
    assert "Acme Metal enregistre comme client" in logs
    assert "1 autre(s) mail(s)" in logs
    assert app.preview_rows[1].decision.interlocutor == InterlocutorType.CLIENT
    directory = window.mailflow_directory_table
    names = [directory.item(row, 0).text() for row in range(directory.rowCount())]
    assert "Acme Metal" in names


def supplier_order() -> ManualClassificationUpdate:
    return ManualClassificationUpdate(
        mail_type=MailType.COMMANDE,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Commande",
    )


def test_bulk_update_classifies_each_mail_under_its_own_company(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    mails = [
        mail("1", "jean@acme-metal.ch"),
        mail("2", "info@steel-supply.ch"),
        mail("3", "paul@acme-metal.ch"),
        mail("4", "jean@acme-metal.ch"),
    ]
    app = controller(tmp_path, store, mails)
    app.scan_and_preview(request())
    app.preview_rows = [review_row(tmp_path, item) for item in mails]

    result = app.apply_manual_updates([0, 1, 1, 99], supplier_order())

    assert result.updated_count == 2
    assert result.errors == ()
    assert {change.organization_name for change in result.role_changes} == {
        "Acme Metal", "Steel Supply",
    }
    targets = [row.decision.target_relative_folder for row in app.preview_rows[:2]]
    assert targets == ["Fournisseurs/Commande/Acme Metal", "Fournisseurs/Commande/Steel Supply"]
    assert all(row.action == PreviewAction.ARCHIVE for row in app.preview_rows[:2])
    # Unselected mails of the same company only receive the company role.
    assert app.preview_rows[2].decision.interlocutor == InterlocutorType.FOURNISSEUR
    assert app.preview_rows[2].action == PreviewAction.REVIEW
    assert app.last_directory_role_change is None


def test_bulk_update_never_changes_archived_mails(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    mails = [mail("1", "jean@acme-metal.ch"), mail("2", "paul@acme-metal.ch")]
    app = controller(tmp_path, store, mails)
    app.preview_rows = [
        review_row(tmp_path, mails[0]),
        review_row(tmp_path, mails[1], PreviewAction.ARCHIVED),
    ]
    archived_before = app.preview_rows[1]

    result = app.apply_manual_updates([0, 1], client_update())

    assert (result.updated_count, result.skipped_archived_count) == (1, 1)
    assert app.preview_rows[1] == archived_before


def test_review_button_applies_one_choice_to_the_whole_selection(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QComboBox, QDialog

    from mailflow.ui.main_window import MainWindow
    from mailflow.ui.preview_table import interlocutor_option_label

    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    mails = [mail(str(index), f"contact{index}@acme-metal.ch") for index in range(3)]
    app = controller(tmp_path, store, mails)
    app.scan_and_preview(request())
    app.preview_rows = [review_row(tmp_path, item) for item in mails]
    titles: list[str] = []

    def choose_client(dialog: QDialog) -> int:
        titles.append(dialog.windowTitle())
        interlocutor = dialog.findChildren(QComboBox)[1]
        interlocutor.setCurrentText(interlocutor_option_label(InterlocutorType.CLIENT))
        return int(QDialog.DialogCode.Accepted.value)

    monkeypatch.setattr(QDialog, "exec", choose_client)
    window = cast(Any, MainWindow(
        AppSettings(paths=AppPaths(data_dir=tmp_path)), controller=app,
    ))
    window.mailflow_refresh_table()
    table = window.mailflow_preview_table
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in (0, 2):
        table.selectionModel().select(table.model().index(row, 0), flags)

    assert window.mailflow_review_button.text() == "Vérifier les 2 mails"
    window.mailflow_review_button.click()

    assert titles == ["Classement manuel de 2 mails"]
    actions = [row.action for row in app.preview_rows]
    assert actions[0] == actions[2] == PreviewAction.ARCHIVE
    assert app.preview_rows[1].decision.interlocutor == InterlocutorType.CLIENT
    assert "Classement manuel applique a 2 mail(s)." in window.mailflow_logs.toPlainText()
