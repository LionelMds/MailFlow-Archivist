from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mailflow.config import AI_MODEL_OPTIONS, AppSettings
from mailflow.core.contact_directory import (
    DirectoryImportResult,
    OrganizationDirectoryEntry,
)
from mailflow.core.folder_tree import FolderPathSummary, FolderTreeNode
from mailflow.models import (
    AiMode,
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    OutlookAccount,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)
from mailflow.ui.main_window import (
    UI_TEXT,
    ai_mode_label,
    build_archive_confirmation_message,
    build_manual_classification_update,
    format_outlook_account_label,
    format_project_html_export_result,
    format_reminder_times,
    openai_key_status_style,
    openai_key_status_text,
    parse_reminder_times,
    project_folder_selected_by_default,
    review_reminder_due_key,
    should_hide_to_tray,
    should_pause_watch_scan,
    summarize_archive_selection,
    tray_tooltip_text,
)
from mailflow.ui.preview_table import ACTION_LABELS, PREVIEW_COLUMNS


class FakeController:
    def __init__(self) -> None:
        self.preview_rows: list[object] = []
        self.report_path = Path("rapport.csv")
        self.archived_all = False
        self.reset_count = 0
        self.directory_entries_list = [
            OrganizationDirectoryEntry(
                organization_id=1,
                name="AIG",
                domains=("gva.ch",),
                contacts=("contact@gva.ch",),
                project_count=1,
                default_role=InterlocutorType.CLIENT,
            )
        ]
        self.global_role = InterlocutorType.CLIENT

    def scan_and_preview(
        self,
        _request: object,
        *,
        progress_callback: object | None = None,
    ) -> list[object]:
        if callable(progress_callback):
            progress_callback("0 mail(s) prets.")
        self.preview_rows = []
        return []

    def available_project_folders(self, _request: object) -> list[object]:
        return []

    def scan_entry_ids(self, _request: object) -> set[str]:
        return set()

    def scan_incremental_preview(
        self,
        _request: object,
        _entry_ids: object,
    ) -> list[object]:
        return []

    def reset_preview(self) -> list[object]:
        self.preview_rows = []
        self.reset_count += 1
        return self.preview_rows

    def export_report(self) -> Path:
        return self.report_path

    def export_project_html(self, *, overwrite_html: bool = False) -> list[object]:
        return []

    def import_contact_directory(
        self,
        *,
        account_identifier: str | None,
        outlook_root_folder: str,
    ) -> DirectoryImportResult:
        return DirectoryImportResult(
            scanned_mail_count=0,
            observed_contact_count=0,
            imported_contact_count=0,
            skipped_internal_count=0,
            skipped_generic_domain_count=0,
            new_organizations=0,
            new_domains=0,
            new_contacts=0,
            new_project_participants=0,
        )

    def directory_entries(self) -> list[OrganizationDirectoryEntry]:
        return self.directory_entries_list

    def add_directory_organization(
        self,
        name: str,
        *,
        domain: str | None = None,
        role: InterlocutorType = InterlocutorType.INCONNU,
    ) -> int:
        organization_id = 2
        self.directory_entries_list.append(
            OrganizationDirectoryEntry(
                organization_id=organization_id,
                name=name,
                domains=() if domain is None else (domain,),
                contacts=(),
                project_count=0,
                default_role=role,
            )
        )
        self.global_role = role
        return organization_id

    def set_directory_organization_role(
        self,
        organization_id: int,
        role: InterlocutorType,
    ) -> list[object]:
        self.global_role = role
        self.directory_entries_list = [
            OrganizationDirectoryEntry(
                organization_id=entry.organization_id,
                name=entry.name,
                domains=entry.domains,
                contacts=entry.contacts,
                project_count=entry.project_count,
                default_role=(
                    role if entry.organization_id == organization_id else entry.default_role
                ),
            )
            for entry in self.directory_entries_list
        ]
        return self.preview_rows

    def delete_directory_organization(self, organization_id: int) -> None:
        self.directory_entries_list = [
            entry
            for entry in self.directory_entries_list
            if entry.organization_id != organization_id
        ]

    def current_project_number(self) -> str | None:
        return "2025-4893"

    def rename_directory_organization(self, organization_id: int, name: str) -> None:
        self.directory_entries_list = [
            OrganizationDirectoryEntry(
                organization_id=organization_id,
                name=name,
                domains=("gva.ch",),
                contacts=("contact@gva.ch",),
                project_count=1,
                default_role=self.global_role,
            )
        ]

    def merge_directory_organizations(
        self,
        source_organization_id: int,
        target_organization_id: int,
    ) -> None:
        self.directory_entries_list = [
            entry
            for entry in self.directory_entries_list
            if entry.organization_id != source_organization_id
            or entry.organization_id == target_organization_id
        ]

    def mark_all_ignored(self) -> list[object]:
        self.preview_rows = []
        return []

    def mark_selected_ignored(self, row_indexes: list[int]) -> list[object]:
        self.preview_rows = []
        return []

    def mark_all_archivable(self) -> list[object]:
        self.preview_rows = []
        return []

    def folder_tree(self) -> list[FolderTreeNode]:
        return []

    def folder_path_counts(self) -> list[FolderPathSummary]:
        return []

    def rename_preview_folder(
        self,
        source_relative_folder: str,
        new_folder_name: str,
    ) -> list[object]:
        return self.preview_rows

    def merge_preview_folder(
        self,
        source_relative_folder: str,
        target_relative_folder: str,
    ) -> list[object]:
        return self.preview_rows

    def rows_ready_for_archive(self, *, include_review: bool = False) -> list[object]:
        return []

    def suggested_account_identifier(self) -> str | None:
        return None

    def available_outlook_accounts(self) -> list[OutlookAccount]:
        return []

    def available_outlook_root_folders(
        self,
        account_identifier: str | None = None,
    ) -> list[str]:
        return ["Boite de reception"]

    def archive_ready(self, *, include_review: bool = False) -> object:
        self.archived_all = True
        return type(
            "Result",
            (),
            {"exported_count": 0, "skipped_count": 0, "failure_count": 0},
        )()

    def archive_selected(
        self,
        row_indexes: list[int],
        *,
        include_review: bool = False,
    ) -> object:
        return self.archive_ready(include_review=include_review)


def make_preview_row(
    tmp_path: Path,
    action: PreviewAction,
    *,
    entry_id: str | None = None,
) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id or f"ENTRY-{action.value}",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont",
        sent_at=datetime(2026, 5, 6, 10, 30),
        body_excerpt="Merci pour votre offre.",
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=action == PreviewAction.ARCHIVE,
        requires_review=action == PreviewAction.REVIEW,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Demande de prix",
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="ok",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=MailType.DEVIS,
                suggested_interlocutor=InterlocutorType.FOURNISSEUR,
                likely_archive=action == PreviewAction.ARCHIVE,
                confidence=0.9,
                matched_rules=["devis"],
                matched_terms=["offre"],
            )
        ),
        decision=decision,
        action=action,
    )


def test_ui_text_contains_expected_actions() -> None:
    assert UI_TEXT["scan_button"] == "Scanner Outlook"
    assert UI_TEXT["reset_workspace"] == "Réinitialiser"
    assert UI_TEXT["watch_outlook"] == "Surveillance Outlook"
    assert UI_TEXT["export_project_html"] == "Exporter le projet en HTML"
    assert UI_TEXT["import_directory"] == "Importer annuaire Outlook"
    assert UI_TEXT["rename_directory"] == "Renommer entreprise"
    assert UI_TEXT["archive"] == "Archiver"
    assert UI_TEXT["more_actions"] == "Plus"
    assert UI_TEXT["background_mode"] == "Passer en arrière-plan"
    assert UI_TEXT["tray_open"] == "Ouvrir MailFlow"
    assert UI_TEXT["tray_watch_active"] == "surveillance active"
    assert UI_TEXT["tray_quit"] == "Quitter"
    assert "Destination proposée" in PREVIEW_COLUMNS
    assert ACTION_LABELS[PreviewAction.REVIEW] == "À vérifier"
    assert UI_TEXT["archive_all_except_review"] == "Archiver tous les mails prêts"


@pytest.mark.parametrize(
    ("project_number", "project_filter", "expected"),
    [
        ("2025-4893", "", True),
        ("2025-4893", "4893", True),
        ("2025-4893", "2025-4893", True),
        ("2025-4893", "4900", False),
    ],
)
def test_project_folder_default_selection(
    project_number: str,
    project_filter: str,
    expected: bool,
) -> None:
    assert (
        project_folder_selected_by_default(project_number, project_filter)
        is expected
    )


def test_outlook_account_label_includes_smtp_address() -> None:
    account = OutlookAccount(display_name="Balz", smtp_address="lionel@balzmetal.test")

    assert format_outlook_account_label(account) == "Balz <lionel@balzmetal.test>"


def test_ai_settings_labels_are_french() -> None:
    assert ai_mode_label(AiMode.DISABLED) == "desactivee"
    assert ai_mode_label(AiMode.AMBIGUOUS_ONLY) == "activee"
    assert ai_mode_label(AiMode.ALL) == "activee"
    assert openai_key_status_text(True) == "Cle enregistree (non testee)"
    assert openai_key_status_text(True, valid=True) == "Cle valide - IA OK"
    assert openai_key_status_text(True, valid=False) == "Cle invalide ou indisponible"
    assert openai_key_status_text(False) == "Aucune cle"
    assert "#166534" in openai_key_status_style(True, valid=True)


def test_summarize_archive_selection_counts_ready_and_skipped_rows(tmp_path: Path) -> None:
    rows = [
        make_preview_row(tmp_path, PreviewAction.ARCHIVE),
        make_preview_row(tmp_path, PreviewAction.REVIEW),
        make_preview_row(tmp_path, PreviewAction.ARCHIVED),
    ]

    summary = summarize_archive_selection(rows, [0, 1, 2])

    assert summary.selected_count == 3
    assert summary.ready_count == 1
    assert summary.skipped_count == 2
    assert summary.can_archive
    assert "1 mail(s)" in build_archive_confirmation_message(summary)


def test_build_manual_classification_update_from_dialog_values() -> None:
    update = build_manual_classification_update(
        mail_type_value="devis",
        interlocutor_value="fournisseur",
        destination_value="Fournisseurs/Demande de prix",
        learning_term="Offerte",
        misleading_term="newsletter",
        manual_required=False,
    )

    assert update.mail_type == MailType.DEVIS
    assert update.interlocutor == InterlocutorType.FOURNISSEUR
    assert update.target_relative_folder == "Fournisseurs/Demande de prix"
    assert update.learning_term == "Offerte"
    assert update.misleading_term == "newsletter"
    assert not update.manual_required


def test_build_manual_classification_update_can_mark_manual_required() -> None:
    update = build_manual_classification_update(
        mail_type_value="a_verifier",
        interlocutor_value="inconnu",
        destination_value="A verifier",
        learning_term=None,
        manual_required=True,
    )

    assert update.mail_type == MailType.A_VERIFIER
    assert update.interlocutor == InterlocutorType.INCONNU
    assert update.learning_term is None
    assert update.manual_required


def test_build_manual_classification_update_accepts_external_role_label() -> None:
    update = build_manual_classification_update(
        mail_type_value="Correspondance",
        interlocutor_value="intervenant externe",
        destination_value="Correspondance",
    )

    assert update.interlocutor == InterlocutorType.INTERVENANT_EXTERNE


def test_build_manual_classification_update_keeps_supplier_role_without_category() -> None:
    update = build_manual_classification_update(
        mail_type_value="",
        interlocutor_value="fournisseur",
        destination_value="A verifier",
    )

    assert update.mail_type == MailType.A_VERIFIER
    assert update.interlocutor == InterlocutorType.FOURNISSEUR
    assert update.target_relative_folder == "A verifier"


def test_format_project_html_export_result_lists_paths(tmp_path: Path) -> None:
    result = type(
        "ProjectHtmlResult",
        (),
        {
            "mail_count": 2,
            "attachment_paths": [tmp_path / "plan.pdf"],
            "html_path": tmp_path / "2025-4893 - Correspondance projet.html",
        },
    )()

    message = format_project_html_export_result([result])

    assert "2 mail(s)" in message
    assert "1 piece(s) jointe(s)" in message
    assert "Correspondance projet.html" in message


def test_should_hide_to_tray_requires_watch_and_available_tray() -> None:
    assert should_hide_to_tray(
        watch_enabled=True,
        tray_available=True,
        force_quit=False,
    )
    assert not should_hide_to_tray(
        watch_enabled=False,
        tray_available=True,
        force_quit=False,
    )
    assert not should_hide_to_tray(
        watch_enabled=True,
        tray_available=False,
        force_quit=False,
    )
    assert not should_hide_to_tray(
        watch_enabled=True,
        tray_available=True,
        force_quit=True,
    )


def test_should_pause_watch_scan_only_when_preview_is_open() -> None:
    assert should_pause_watch_scan(window_visible=True, preview_has_rows=True)
    assert not should_pause_watch_scan(window_visible=False, preview_has_rows=True)
    assert not should_pause_watch_scan(window_visible=True, preview_has_rows=False)


def test_tray_tooltip_shows_watch_state() -> None:
    assert tray_tooltip_text(False) == "MailFlow Archivist - surveillance inactive"
    assert tray_tooltip_text(True) == "MailFlow Archivist - surveillance active"
    assert tray_tooltip_text(True, 3) == "MailFlow Archivist - surveillance active - 3 a verifier"


def test_review_reminder_time_helpers() -> None:
    assert parse_reminder_times("9:00, 14:30; 14:30") == ["09:00", "14:30"]
    assert format_reminder_times(["9:00", "14:30"]) == "09:00, 14:30"
    sent = {"2026-05-21 09:00"}
    assert (
        review_reminder_due_key(
            datetime(2026, 5, 21, 9, 0),
            ["09:00"],
            2,
            sent,
        )
        is None
    )
    assert review_reminder_due_key(
        datetime(2026, 5, 21, 14, 0),
        ["14:00"],
        2,
        sent,
    ) == "2026-05-21 14:00"


def test_main_window_instantiates_when_pyside6_is_available() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLineEdit

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(), controller=FakeController())
    dynamic_window = cast(Any, window)
    controller = cast(FakeController, dynamic_window.mailflow_controller)

    assert window.windowTitle() == "MailFlow Archivist"
    assert dynamic_window.mailflow_outlook_root_combo.currentText() == "Boite de reception"
    assert dynamic_window.mailflow_project_digest_preview.isReadOnly()
    assert "Aucun projet scanne" in dynamic_window.mailflow_project_digest_preview.toPlainText()
    assert dynamic_window.mailflow_mail_preview.isReadOnly()
    assert dynamic_window.mailflow_ai_mode_combo.currentData() == AiMode.ALL.value
    assert dynamic_window.mailflow_ai_model_input.currentText() == "gpt-6-astra"
    assert dynamic_window.mailflow_ai_model_input.count() >= len(AI_MODEL_OPTIONS)
    assert dynamic_window.mailflow_openai_key_input.echoMode() == QLineEdit.EchoMode.Password
    assert dynamic_window.mailflow_test_openai_key_button.text() == "Tester IA"
    assert dynamic_window.mailflow_check_updates_button.text() == "Rechercher une mise à jour"
    assert "Version" in dynamic_window.mailflow_update_status.text()
    assert dynamic_window.mailflow_ai_include_body_checkbox.isChecked()
    assert dynamic_window.mailflow_watch_checkbox.text() == "Surveillance Outlook"
    assert dynamic_window.mailflow_watch_timer.interval() == 300000
    assert dynamic_window.mailflow_review_reminder_timer.interval() == 60000
    assert dynamic_window.mailflow_review_reminder_times_input.text() == "09:00, 14:00"
    assert dynamic_window.mailflow_scan_button.text() == "Scanner Outlook"
    assert dynamic_window.mailflow_scan_status_label.text() == ""
    assert dynamic_window.mailflow_reset_button.text() == "Réinitialiser"
    assert not window.windowIcon().isNull()
    assert not dynamic_window.mailflow_tray_icon.icon().isNull()
    assert dynamic_window.mailflow_tray_icon.toolTip() == (
        "MailFlow Archivist - surveillance inactive"
    )
    assert dynamic_window.mailflow_tray_open_action.text() == "Ouvrir MailFlow"
    assert dynamic_window.mailflow_tray_watch_action.isCheckable()
    assert dynamic_window.mailflow_tray_quit_action.text() == "Quitter"
    assert dynamic_window.mailflow_folder_tree.headerItem().text(0) == "Dossier propose"
    assert dynamic_window.mailflow_rename_folder_button.text() == "Renommer dossier"
    assert dynamic_window.mailflow_merge_folder_button.text() == "Fusionner vers..."
    assert dynamic_window.mailflow_archive_button.text() == "Archiver"
    assert dynamic_window.mailflow_archive_selection_action.text() == "Archiver la sélection"
    assert dynamic_window.mailflow_archive_all_action.text() == "Archiver tous les mails prêts"
    assert dynamic_window.mailflow_more_actions_button.text() == "Plus"
    assert dynamic_window.mailflow_more_actions_menu.actions()[0].text() == "Ignorer la sélection"
    assert dynamic_window.mailflow_background_action.text() == "Passer en arrière-plan"
    assert dynamic_window.mailflow_restore_archivable_action.text() == (
        "Rétablir les mails ignorés"
    )
    assert dynamic_window.mailflow_import_directory_button.text() == "Importer annuaire Outlook"
    assert dynamic_window.mailflow_refresh_directory_button.text() == "Actualiser"
    assert dynamic_window.mailflow_add_directory_button.text() == "Ajouter entreprise"
    assert dynamic_window.mailflow_delete_directory_button.text() == "Supprimer entreprise"
    assert dynamic_window.mailflow_rename_directory_button.text() == "Renommer entreprise"
    assert dynamic_window.mailflow_merge_directory_button.text() == "Fusionner entreprise"
    assert dynamic_window.mailflow_directory_table.rowCount() == 1
    assert dynamic_window.mailflow_directory_table.item(0, 0).text() == "AIG"
    assert dynamic_window.mailflow_directory_table.item(0, 1).text() == "gva.ch"
    assert dynamic_window.mailflow_directory_table.cellWidget(0, 3).currentText() == "client"
    dynamic_window.mailflow_directory_table.cellWidget(0, 3).setCurrentText("fournisseur")
    assert controller.global_role == InterlocutorType.FOURNISSEUR
    assert dynamic_window.mailflow_navigation.count() == 5
    assert dynamic_window.mailflow_navigation.item(0).text() == "Mails"
    assert dynamic_window.mailflow_navigation.item(1).text() == "Arborescence"
    assert dynamic_window.mailflow_navigation.item(2).text() == "Annuaire"
    assert dynamic_window.mailflow_navigation.item(3).text() == "Boîte mail"
    assert dynamic_window.mailflow_navigation.item(4).text() == "Réglages"
    assert dynamic_window.mailflow_pages.count() == 5
    assert dynamic_window.mailflow_content_splitter.count() == 2
    assert dynamic_window.mailflow_workspace_splitter.count() == 2
    assert dynamic_window.mailflow_settings_scroll_area.widgetResizable()
    assert not dynamic_window.mailflow_logs.isVisible()
    dynamic_window.mailflow_reset_button.click()
    assert controller.reset_count == 1
    window.close()
    app.quit()


def test_preview_refresh_preserves_current_row_and_scroll_position(
    tmp_path: Path,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    controller.preview_rows = [
        make_preview_row(
            tmp_path,
            PreviewAction.ARCHIVE,
            entry_id=f"ENTRY-{index:03d}",
        )
        for index in range(80)
    ]
    window = MainWindow(AppSettings(), controller=controller)
    dynamic_window = cast(Any, window)
    table = dynamic_window.mailflow_preview_table
    window.show()
    dynamic_window.mailflow_refresh_table()
    app.processEvents()

    table.setCurrentCell(55, 4)
    table.clearSelection()
    table.selectRow(55)
    table.verticalScrollBar().setValue(35)
    expected_scroll = table.verticalScrollBar().value()
    assert expected_scroll > 0

    dynamic_window.mailflow_refresh_table()
    app.processEvents()

    assert table.currentRow() == 55
    assert [index.row() for index in table.selectionModel().selectedRows()] == [55]
    assert table.verticalScrollBar().value() == expected_scroll
    window.close()


def test_filters_hide_rows_without_changing_controller_indexes_or_hidden_selection(
    tmp_path: Path,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    rows = [
        make_preview_row(tmp_path, PreviewAction.ARCHIVE, entry_id="ready"),
        make_preview_row(tmp_path, PreviewAction.REVIEW, entry_id="review"),
        make_preview_row(tmp_path, PreviewAction.IGNORE, entry_id="ignored"),
    ]
    rows[1].mail.subject = "Rénovation façade"
    rows[1].mail.sender_name = "Élodie"
    controller.preview_rows = cast(list[object], rows)
    window = MainWindow(AppSettings(), controller=controller)
    table = window.mailflow_preview_table
    table.selectRow(0)

    window.mailflow_status_filter.setCurrentIndex(1)
    window.mailflow_search_input.setText("elodie facade")

    assert table.isRowHidden(0)
    assert not table.isRowHidden(1)
    assert table.isRowHidden(2)
    assert window.mailflow_selected_table_row_indexes() == []
    assert window.mailflow_mail_preview.toPlainText() == ""
    table.selectRow(1)
    assert window.mailflow_selected_table_row_indexes() == [1]
    assert [cast(PreviewRow, row).mail.entry_id for row in controller.preview_rows] == [
        "ready", "review", "ignored",
    ]

    window.mailflow_refresh_table()
    assert window.mailflow_search_input.text() == "elodie facade"
    assert window.mailflow_selected_table_row_indexes() == [1]
    assert table.isRowHidden(0)
    window.mailflow_search_input.setText("introuvable")
    assert window.mailflow_mail_results.currentIndex() == 1
    assert window.mailflow_selected_table_row_indexes() == []
    window.mailflow_clear_filters_button.click()
    assert all(not table.isRowHidden(index) for index in range(3))
    assert window.mailflow_mail_results.currentIndex() == 0
    window.close()
    app.processEvents()


def test_refresh_preserves_identity_and_column_width_after_rows_reorder(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    controller.preview_rows = [
        make_preview_row(tmp_path, PreviewAction.ARCHIVE, entry_id=f"id-{index}")
        for index in range(3)
    ]
    window = MainWindow(AppSettings(), controller=controller)
    table = window.mailflow_preview_table
    table.setCurrentCell(0, 4)
    table.selectRow(0)
    table.setColumnWidth(4, 410)

    controller.preview_rows.reverse()
    window.mailflow_refresh_table()

    assert table.currentRow() == 2
    assert window.mailflow_selected_table_row_indexes() == [2]
    assert table.columnWidth(4) == 410
    window.close()
    app.processEvents()


def test_cancelling_archive_all_does_not_restore_ignored_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    controller.preview_rows = [
        make_preview_row(tmp_path, PreviewAction.ARCHIVE),
        make_preview_row(tmp_path, PreviewAction.IGNORE),
    ]
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    window = MainWindow(AppSettings(), controller=controller)

    window.mailflow_archive_all_action.trigger()

    assert len(controller.preview_rows) == 2
    assert cast(PreviewRow, controller.preview_rows[1]).action == PreviewAction.IGNORE
    assert not controller.archived_all
    window.close()
    app.processEvents()


def test_busy_operation_blocks_mutating_controls_and_window_close(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    controller.preview_rows = [make_preview_row(tmp_path, PreviewAction.ARCHIVE)]
    window = MainWindow(AppSettings(), controller=controller)
    window.show()
    window.mailflow_preview_table.selectRow(0)
    window.mailflow_set_operation_busy(True)

    assert not window.mailflow_scan_button.isEnabled()
    assert not window.mailflow_reset_button.isEnabled()
    assert not window.mailflow_save_settings_button.isEnabled()
    assert not window.mailflow_watch_checkbox.isEnabled()
    assert not window.mailflow_archive_all_action.isEnabled()
    assert not window.mailflow_restore_archivable_action.isEnabled()
    assert not window.mailflow_report_action.isEnabled()
    window.mailflow_restore_archivable_action.trigger()
    assert len(controller.preview_rows) == 1
    window.mailflow_reset_button.click()
    assert controller.reset_count == 0
    assert not window.close()

    window.mailflow_set_operation_busy(False)
    assert window.mailflow_scan_button.isEnabled()
    assert window.mailflow_archive_all_action.isEnabled()
    assert window.close()
    app.processEvents()


def test_navigation_discloses_settings_without_mail_inspector() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(), controller=FakeController())
    assert window.mailflow_mail_results.currentIndex() == 1
    assert not window.mailflow_archive_button.isEnabled()
    window.mailflow_navigation.setCurrentRow(4)
    assert window.mailflow_pages.currentIndex() == 4
    assert window.mailflow_inspector.isHidden()
    window.mailflow_navigation.setCurrentRow(3)
    assert window.mailflow_pages.currentWidget() is window.mailflow_mailbox_page
    assert window.mailflow_inspector.isHidden()
    window.mailflow_navigation.setCurrentRow(0)
    assert not window.mailflow_inspector.isHidden()
    assert window.mailflow_preview_tabs.count() == 2
    window.close()
    app.processEvents()


def test_saving_ai_settings_updates_existing_pipeline_and_key_without_losing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow import config
    from mailflow.classifier import ai_classifier
    from mailflow.core import app_controller
    from mailflow.ui.background_call import ResponsiveAiClassifier
    from mailflow.ui.main_window import MainWindow

    key_store = {"key": "initial-test-key"}
    monkeypatch.setattr(config, "get_openai_api_key", lambda: key_store["key"])
    monkeypatch.setattr(app_controller, "get_openai_api_key", lambda: key_store["key"])
    monkeypatch.setattr(config, "set_openai_api_key", lambda key: key_store.update(key=key))
    monkeypatch.setattr(config, "save_settings", lambda _settings: None)
    monkeypatch.setattr(ai_classifier, "AiClassifier", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(app_controller, "AiClassifier", lambda **kwargs: SimpleNamespace(**kwargs))
    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    controller.preview_rows = [make_preview_row(tmp_path, PreviewAction.REVIEW)]
    original_rows = controller.preview_rows
    pipeline = SimpleNamespace(ai_classifier=None, ai_mode=AiMode.ALL)
    cast(Any, controller).preview_pipeline = pipeline
    window = MainWindow(AppSettings(), controller=controller)

    window.mailflow_ai_mode_combo.setCurrentIndex(0)
    window.mailflow_save_settings_button.click()
    assert pipeline.ai_mode == AiMode.DISABLED
    assert pipeline.ai_classifier is None

    window.mailflow_ai_mode_combo.setCurrentIndex(1)
    window.mailflow_ai_model_input.setCurrentText("gpt-6-astra-custom")
    window.mailflow_ai_include_body_checkbox.setChecked(False)
    window.mailflow_privacy_phone_checkbox.setChecked(True)
    window.mailflow_save_settings_button.click()
    assert pipeline.ai_mode == AiMode.ALL
    assert not pipeline.include_body_for_ai
    assert pipeline.privacy_mask_phone_numbers
    assert isinstance(pipeline.ai_classifier, ResponsiveAiClassifier)
    assert cast(Any, pipeline.ai_classifier.classifier).model == "gpt-6-astra-custom"
    assert pipeline.ai_classifier.classifier.api_key == "initial-test-key"

    window.mailflow_openai_key_input.setText("replacement-test-key")
    window.mailflow_save_openai_key_button.click()
    assert pipeline.ai_classifier.classifier.api_key == "replacement-test-key"
    assert controller.preview_rows is original_rows
    assert window.mailflow_controller is controller
    window.close()
    app.processEvents()


def test_archived_mail_is_read_only_in_review_and_inline_controls(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow
    from mailflow.ui.preview_table import DESTINATION_COLUMN, INTERLOCUTOR_COLUMN, TYPE_COLUMN

    app = QApplication.instance() or QApplication([])
    controller = FakeController()
    controller.preview_rows = [make_preview_row(tmp_path, PreviewAction.ARCHIVED)]
    window = MainWindow(AppSettings(), controller=controller)
    table = window.mailflow_preview_table
    table.selectRow(0)
    assert not window.mailflow_review_button.isEnabled()
    for column in (DESTINATION_COLUMN, INTERLOCUTOR_COLUMN, TYPE_COLUMN):
        assert not table.cellWidget(0, column).isEnabled()
    table.cellDoubleClicked.emit(0, 4)
    assert "déjà archivé" in window.mailflow_scan_status_label.text()
    window.close()
    app.processEvents()


def test_local_ai_settings_round_trip_and_switch_preserve_both_models_and_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow import config
    from mailflow.classifier.ollama_classifier import OllamaClassifier
    from mailflow.config import AppPaths, load_settings
    from mailflow.core import app_controller
    from mailflow.ui.background_call import ResponsiveAiClassifier
    from mailflow.ui.main_window import MainWindow

    monkeypatch.setattr(config, "get_openai_api_key", lambda: "test-key")
    monkeypatch.setattr(app_controller, "get_openai_api_key", lambda: "test-key")
    monkeypatch.setattr(app_controller, "AiClassifier", lambda **kwargs: SimpleNamespace(**kwargs))
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(paths=AppPaths(data_dir=tmp_path), ai_model="gpt-6-astra-custom")
    controller = FakeController()
    controller.preview_rows = [make_preview_row(tmp_path, PreviewAction.REVIEW)]
    original_rows = controller.preview_rows
    pipeline = SimpleNamespace(ai_classifier=None, ai_mode=AiMode.ALL)
    cast(Any, controller).preview_pipeline = pipeline
    window = MainWindow(settings, controller=controller)
    window.mailflow_ai_provider_combo.setCurrentIndex(1)
    window.mailflow_ollama_model_input.setCurrentText("custom-local:4b")
    window.mailflow_ollama_base_url_input.setText("http://localhost:11435/")
    window.mailflow_ollama_timeout_input.setValue(300.0)
    window.mailflow_save_settings_button.click()

    assert settings.ai_provider == "ollama"
    assert settings.ollama_base_url == "http://127.0.0.1:11435"
    assert not window.mailflow_openai_key_input.isEnabled()
    assert window.mailflow_ai_model_input.isHidden()
    assert window.mailflow_ollama_model_input.isEnabled()
    assert isinstance(pipeline.ai_classifier, ResponsiveAiClassifier)
    assert isinstance(pipeline.ai_classifier.classifier, OllamaClassifier)
    assert controller.preview_rows is original_rows

    window.mailflow_ai_provider_combo.setCurrentIndex(0)
    window.mailflow_save_settings_button.click()
    assert settings.ai_model == "gpt-6-astra-custom"
    assert settings.ollama_model == "custom-local:4b"
    assert cast(Any, pipeline.ai_classifier.classifier).model == "gpt-6-astra-custom"
    assert window.mailflow_openai_key_input.isEnabled()
    assert not window.mailflow_ollama_model_input.isEnabled()

    window.mailflow_ai_provider_combo.setCurrentIndex(1)
    window.mailflow_save_settings_button.click()
    reloaded = load_settings(settings.paths.config_file)
    assert reloaded.ai_provider == "ollama"
    assert reloaded.ollama_model == "custom-local:4b"
    assert reloaded.ollama_timeout_seconds == 300.0
    assert reloaded.ai_model == "gpt-6-astra-custom"
    window.close()
    second_window = MainWindow(reloaded, controller=FakeController())
    assert second_window.mailflow_ai_provider_combo.currentData() == "ollama"
    assert second_window.mailflow_ollama_model_input.currentText() == "custom-local:4b"
    assert second_window.mailflow_ollama_base_url_input.text() == "http://127.0.0.1:11435"
    assert second_window.mailflow_ollama_timeout_input.value() == 300.0
    second_window.close()
    app.processEvents()


def test_local_ai_test_is_responsive_and_never_reads_key_or_uses_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from mailflow import config
    from mailflow.classifier import ai_classifier, ollama_classifier
    from mailflow.classifier.ai_classifier import AiConnectionCheck
    from mailflow.core import app_controller
    from mailflow.ui.main_window import MainWindow

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Local mode must not access OpenAI or the key store")

    checks: list[dict[str, Any]] = []

    def make_local_classifier(**kwargs: Any) -> Any:
        def check() -> AiConnectionCheck:
            checks.append(kwargs)
            time.sleep(0.1)
            return AiConnectionCheck(ok=True, message="Connexion Ollama OK (mail fictif).")
        return SimpleNamespace(check_connection=check, list_models=forbidden)

    monkeypatch.setattr(config, "get_openai_api_key", forbidden)
    monkeypatch.setattr(app_controller, "get_openai_api_key", forbidden)
    monkeypatch.setattr(ai_classifier, "AiClassifier", forbidden)
    monkeypatch.setattr(app_controller, "AiClassifier", forbidden)
    monkeypatch.setattr(ollama_classifier, "OllamaClassifier", make_local_classifier)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(ai_provider="ollama"), controller=FakeController())
    assert checks == []
    assert "aucun envoi à OpenAI" in window.mailflow_ai_provider_hint.text()
    assert "à tester" in window.mailflow_ollama_status.text()
    assert not window.mailflow_test_openai_key_button.isEnabled()
    window.mailflow_ollama_timeout_input.setValue(240.0)
    observed_busy: list[bool] = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: observed_busy.append(not window.mailflow_pages.isEnabled()))
    timer.start()
    window.mailflow_test_ollama_button.click()
    timer.stop()
    assert len(checks) == 1
    assert checks[0]["model"] == "qwen3.5:4b"
    assert checks[0]["base_url"] == "http://127.0.0.1:11434"
    assert checks[0]["timeout_seconds"] == 240.0
    assert observed_busy and all(observed_busy)
    assert "Connexion Ollama OK" in window.mailflow_ollama_status.text()
    assert window.mailflow_pages.isEnabled()
    window.mailflow_ollama_model_input.setCurrentText("different:4b")
    assert "à tester" in window.mailflow_ollama_status.text()
    window.close()
    app.processEvents()


def test_jev_settings_key_and_test_never_touch_openai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLineEdit

    from mailflow import config
    from mailflow.classifier import ai_classifier, jev_classifier
    from mailflow.classifier.ai_classifier import AiConnectionCheck
    from mailflow.config import AppPaths, load_settings
    from mailflow.core import app_controller
    from mailflow.ui.background_call import ResponsiveAiClassifier
    from mailflow.ui.main_window import MainWindow

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Jev mode must not access OpenAI or its key")

    key_store: dict[str, str | None] = {"key": None}
    built: list[dict[str, Any]] = []

    def make_jev(**kwargs: Any) -> Any:
        built.append(kwargs)
        return SimpleNamespace(
            **kwargs,
            check_connection=lambda: AiConnectionCheck(
                ok=True, message="Connexion Jev OK (jev-2026-09-15 : Commande, 97%).",
            ),
        )

    monkeypatch.setattr(config, "get_openai_api_key", forbidden)
    monkeypatch.setattr(app_controller, "get_openai_api_key", forbidden)
    monkeypatch.setattr(ai_classifier, "AiClassifier", forbidden)
    monkeypatch.setattr(app_controller, "AiClassifier", forbidden)
    monkeypatch.setattr(config, "get_jev_api_key", lambda: key_store["key"])
    monkeypatch.setattr(app_controller, "get_jev_api_key", lambda: key_store["key"])
    monkeypatch.setattr(config, "set_jev_api_key", lambda key: key_store.update(key=key))
    monkeypatch.setattr(jev_classifier, "JevClassifier", make_jev)
    monkeypatch.setattr(app_controller, "JevClassifier", make_jev)
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(paths=AppPaths(data_dir=tmp_path), ai_provider="jev")
    controller = FakeController()
    pipeline = SimpleNamespace(ai_classifier=None, ai_mode=AiMode.ALL)
    cast(Any, controller).preview_pipeline = pipeline
    window = MainWindow(settings, controller=controller)

    assert window.mailflow_ai_provider_combo.currentIndex() == 2
    assert window.mailflow_ai_provider_combo.currentData() == "jev"
    assert "TypeSafe" in window.mailflow_ai_provider_hint.text()
    assert "Aucun envoi à OpenAI" in window.mailflow_ai_provider_hint.text()
    assert window.mailflow_jev_key_input.isEnabled()
    assert window.mailflow_jev_key_input.echoMode() == QLineEdit.EchoMode.Password
    assert not window.mailflow_openai_key_input.isEnabled()
    assert window.mailflow_ai_model_input.isHidden()
    assert not window.mailflow_ollama_model_input.isEnabled()
    assert window.mailflow_jev_key_status.text() == "Aucune cle"

    window.mailflow_jev_model_input.setCurrentText("jev-2026-09-15")
    window.mailflow_save_settings_button.click()
    assert settings.ai_provider == "jev"
    assert settings.jev_model == "jev-2026-09-15"
    assert pipeline.ai_classifier is None

    window.mailflow_jev_key_input.setText("  ts-test-key  ")
    window.mailflow_save_jev_key_button.click()
    assert key_store["key"] == "ts-test-key"
    assert window.mailflow_jev_key_input.text() == ""
    assert window.mailflow_jev_key_status.text() == "Cle enregistree (non testee)"
    assert isinstance(pipeline.ai_classifier, ResponsiveAiClassifier)
    assert built[-1] == {
        "api_key": "ts-test-key", "model": "jev-2026-09-15", "timeout_seconds": 20.0,
    }

    window.mailflow_test_jev_key_button.click()
    assert built[-1]["model"] == "jev-2026-09-15"
    assert window.mailflow_jev_key_status.text() == "Cle valide - IA OK"
    assert "Connexion Jev OK" in window.mailflow_logs.toPlainText()
    assert "ts-test-key" not in window.mailflow_logs.toPlainText()

    reloaded = load_settings(settings.paths.config_file)
    assert reloaded.ai_provider == "jev"
    assert reloaded.jev_model == "jev-2026-09-15"
    window.close()
    second_window = MainWindow(reloaded, controller=FakeController())
    assert second_window.mailflow_ai_provider_combo.currentData() == "jev"
    assert second_window.mailflow_jev_model_input.currentText() == "jev-2026-09-15"
    second_window.close()
    app.processEvents()


@pytest.mark.parametrize("models", [["qwen3.5:4b", "custom:4b"], [], ["another:4b"]])
def test_refresh_local_models_preserves_selection_and_reports_installed_models(
    models: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.classifier import ollama_classifier
    from mailflow.ui.main_window import MainWindow

    calls: list[str] = []

    def installed_models() -> list[str]:
        calls.append("list")
        return models

    monkeypatch.setattr(ollama_classifier, "OllamaClassifier",
                        lambda **_kwargs: SimpleNamespace(list_models=installed_models))
    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(ai_provider="ollama"), controller=FakeController())
    assert calls == []
    window.mailflow_refresh_ollama_models_button.click()
    assert calls == ["list"]
    assert window.mailflow_ollama_model_input.currentText() == "qwen3.5:4b"
    assert window.mailflow_ollama_model_input.count() == len(models)
    if not models:
        assert "aucun modèle" in window.mailflow_ollama_status.text()
    elif "qwen3.5:4b" not in models:
        assert "absent" in window.mailflow_ollama_status.text()
    else:
        assert "2 modèle(s) installé(s)" in window.mailflow_ollama_status.text()
    window.close()
    app.processEvents()


def test_local_failure_restores_controls_and_shows_error_without_api_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow import config
    from mailflow.classifier import ai_classifier, ollama_classifier
    from mailflow.classifier.ai_classifier import AiConnectionCheck
    from mailflow.ui.main_window import MainWindow

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Local failure must not fall back to OpenAI")

    def unavailable() -> list[str]:
        raise ollama_classifier.OllamaError("Démarrez Ollama sur ce PC.")

    monkeypatch.setattr(config, "get_openai_api_key", forbidden)
    monkeypatch.setattr(ai_classifier, "AiClassifier", forbidden)
    monkeypatch.setattr(ollama_classifier, "OllamaClassifier", lambda **_kwargs: SimpleNamespace(
        check_connection=lambda: AiConnectionCheck(ok=False, message="Le modèle est absent."),
        list_models=unavailable,
    ))
    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(ai_provider="ollama"), controller=FakeController())
    window.mailflow_test_ollama_button.click()
    assert "modèle est absent" in window.mailflow_ollama_status.text()
    assert window.mailflow_test_ollama_button.isEnabled()
    window.mailflow_refresh_ollama_models_button.click()
    assert "Démarrez Ollama" in window.mailflow_ollama_status.text()
    assert window.mailflow_refresh_ollama_models_button.isEnabled()
    assert window.mailflow_ollama_model_input.currentText() == "qwen3.5:4b"
    window.close()
    app.processEvents()


def test_saving_invalid_local_address_keeps_settings_and_pipeline_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow import config
    from mailflow.ui.main_window import MainWindow

    saved: list[AppSettings] = []
    monkeypatch.setattr(config, "save_settings", saved.append)
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(ai_provider="ollama")
    controller = FakeController()
    pipeline = SimpleNamespace(ai_classifier=None, ai_mode=AiMode.ALL)
    cast(Any, controller).preview_pipeline = pipeline
    window = MainWindow(settings, controller=controller)
    window.mailflow_ollama_base_url_input.setText("https://outside.example.invalid")
    window.mailflow_ollama_model_input.setCurrentText("changed:4b")
    window.mailflow_save_settings_button.click()
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.ollama_model == "qwen3.5:4b"
    assert pipeline.ai_classifier is None
    assert saved == []
    assert "non enregistrés" in window.mailflow_scan_status_label.text()
    window.close()
    app.processEvents()


def test_light_theme_is_forced_for_dark_windows_sessions() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.theme import APP_STYLESHEET, apply_light_theme

    app = cast(QApplication, QApplication.instance() or QApplication([]))
    original = app.palette()
    dark = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base):
        dark.setColor(role, QColor("#202020"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    app.setPalette(dark)
    try:
        apply_light_theme(app)
        palette = app.palette()
        assert palette.color(QPalette.ColorRole.Base).name() == "#ffffff"
        assert palette.color(QPalette.ColorRole.Text).name() == "#243247"
        assert palette.color(QPalette.ColorRole.Window).name() == "#f3f6fa"
    finally:
        app.setPalette(original)
    # Open drop-down lists get explicit colors whatever the system theme.
    assert "QComboBox QAbstractItemView { background: white; color: #243247;" in APP_STYLESHEET


class MailboxFakeController(FakeController):
    def __init__(self, analysis: Any) -> None:
        super().__init__()
        self.mailbox_analysis: Any = None
        self.next_analysis = analysis
        self.mailbox_requests: list[Any] = []
        self.suggesters: list[Any] = []
        self.sort_choices: list[dict[str, tuple[str, ...]]] = []
        self.opened: list[str] = []

    def analyze_mailbox(
        self, request: Any, *, progress: Any = None, suggester: Any = None,
    ) -> Any:
        self.mailbox_requests.append(request)
        self.suggesters.append(suggester)
        assert progress(1, 1, "Lecture des mails 1/1...")
        self.mailbox_analysis = self.next_analysis
        return self.mailbox_analysis

    def sort_mailbox(self, choices: dict[str, tuple[str, ...]]) -> Any:
        from mailflow.core.mailbox_sorting import MailboxSortResult

        self.sort_choices.append(dict(choices))
        self.mailbox_analysis.proposals = [
            proposal for proposal in self.mailbox_analysis.proposals
            if proposal.entry_id not in choices
        ]
        return MailboxSortResult(
            moved_count=len(choices), copy_count=1, sorted_entry_ids=list(choices),
        )

    def open_mailbox_mail(self, entry_id: str) -> None:
        self.opened.append(entry_id)


def mailbox_analysis() -> Any:
    from mailflow.core.mailbox_sorting import (
        MailboxAnalysis,
        ProjectSuggestion,
        SortProposal,
        SortStatus,
    )
    from mailflow.outlook.mailbox import MailboxSourceKind, ProjectFolder

    def proposal(entry_id: str, status: SortStatus, **values: Any) -> SortProposal:
        return SortProposal(
            entry_id=entry_id, source=MailboxSourceKind.INBOX, source_label="Boîte de réception",
            subject=f"Mail {entry_id}", correspondent="Dupont", sent_at=datetime(2026, 9, 28),
            direction=Direction.RECEIVED, status=status, **values,
        )

    return MailboxAnalysis(
        proposals=[
            proposal("A", SortStatus.READY, destinations=("2025-4893", "2025-5012")),
            proposal(
                "B", SortStatus.SUGGESTED, suggestion=ProjectSuggestion("2025-5012", 0.9),
            ),
            proposal("C", SortStatus.NO_NUMBER),
        ],
        project_folders={
            number: ProjectFolder(number, number, number, None)
            for number in ("2025-4893", "2025-5012")
        },
        warnings=["Dossier Outlook « A CLASSER » introuvable."],
    )


def test_mailbox_page_analyzes_then_sorts_only_checked_visible_mails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from mailflow import config
    from mailflow.config import AppPaths, load_settings
    from mailflow.outlook.mailbox import MailboxSourceKind
    from mailflow.ui.main_window import MainWindow

    monkeypatch.setattr(config, "get_jev_api_key", lambda: None)
    questions: list[str] = []

    def answer_yes(_parent: Any, _title: str, text: str, *_args: Any) -> Any:
        questions.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(paths=AppPaths(data_dir=tmp_path), mailbox_sort_days=30)
    controller = MailboxFakeController(mailbox_analysis())
    window = MainWindow(settings, controller=controller)
    table = window.mailflow_mailbox_table

    window.mailflow_navigation.setCurrentRow(3)
    assert not window.mailflow_mailbox_jev_checkbox.isEnabled()
    assert window.mailflow_mailbox_period_combo.currentData() == 30
    assert not window.mailflow_mailbox_sort_button.isEnabled()
    window.mailflow_mailbox_sent_checkbox.setChecked(False)
    window.mailflow_mailbox_pending_input.setText("  ")
    window.mailflow_mailbox_analyze_button.click()

    request = controller.mailbox_requests[0]
    assert request.sources == frozenset({MailboxSourceKind.INBOX, MailboxSourceKind.PENDING})
    assert request.pending_folder_name == "A CLASSER"
    assert request.since is not None
    assert controller.suggesters == [None]
    assert table.rowCount() == 3
    assert table.item(0, 0).checkState() == Qt.CheckState.Checked
    assert table.item(1, 0).checkState() == Qt.CheckState.Unchecked
    assert not table.item(2, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert "1 mail(s) prêt(s) à ranger (1 copie(s))" in (
        window.mailflow_mailbox_summary_label.text()
    )
    assert "A CLASSER" in window.mailflow_mailbox_status_label.text()
    assert window.mailflow_mailbox_sort_button.isEnabled()
    assert load_settings(tmp_path / "config.json").mailbox_sort_days == 30

    # Only visible checked rows are sorted: the ready mail is hidden by the filter.
    window.mailflow_mailbox_filter_combo.setCurrentIndex(2)
    assert table.isRowHidden(0) and not table.isRowHidden(1)
    window.mailflow_mailbox_check_all_button.click()
    assert window.mailflow_mailbox_sort_button.text() == "Ranger les mails cochés"
    window.mailflow_mailbox_sort_button.click()

    assert controller.sort_choices == [{"B": ("2025-5012",)}]
    assert questions[0].startswith("Ranger 1 mail(s)")
    assert table.rowCount() == 2
    assert "1 mail(s) déplacé(s)" in window.mailflow_mailbox_status_label.text()

    window.mailflow_mailbox_filter_combo.setCurrentIndex(0)
    table.cellDoubleClicked.emit(1, 4)
    assert controller.opened == ["C"]
    window.close()
    app.processEvents()


def test_mailbox_page_offers_jev_only_with_a_saved_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow import config
    from mailflow.config import AppPaths
    from mailflow.core import app_controller
    from mailflow.ui.background_call import ResponsiveProjectSuggester
    from mailflow.ui.main_window import MainWindow

    monkeypatch.setattr(config, "get_jev_api_key", lambda: "ts-key")
    monkeypatch.setattr(app_controller, "get_jev_api_key", lambda: "ts-key")
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(paths=AppPaths(data_dir=tmp_path), mailbox_suggest_with_jev=True)
    controller = MailboxFakeController(mailbox_analysis())
    window = MainWindow(settings, controller=controller)

    assert window.mailflow_mailbox_jev_checkbox.isEnabled()
    assert window.mailflow_mailbox_jev_checkbox.isChecked()
    window.mailflow_mailbox_analyze_button.click()

    assert isinstance(controller.suggesters[0], ResponsiveProjectSuggester)
    window.close()
    app.processEvents()
