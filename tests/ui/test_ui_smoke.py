from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

from mailflow.config import AppSettings
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
    openai_key_status_style,
    openai_key_status_text,
    should_hide_to_tray,
    should_pause_watch_scan,
    summarize_archive_selection,
)
from mailflow.ui.preview_table import ACTION_LABELS, PREVIEW_COLUMNS


class FakeController:
    def __init__(self) -> None:
        self.preview_rows: list[object] = []
        self.report_path = Path("rapport.csv")
        self.archived_all = False

    def scan_and_preview(self, _request: object) -> list[object]:
        self.preview_rows = []
        return []

    def export_report(self) -> Path:
        return self.report_path

    def export_project_html(self, *, overwrite_html: bool = False) -> list[object]:
        return []

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


def make_preview_row(tmp_path: Path, action: PreviewAction) -> PreviewRow:
    mail = MailMetadata(
        entry_id=f"ENTRY-{action.value}",
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
    assert UI_TEXT["watch_outlook"] == "Surveillance Outlook"
    assert UI_TEXT["export_project_html"] == "Exporter HTML projet"
    assert UI_TEXT["tray_open"] == "Ouvrir MailFlow"
    assert UI_TEXT["tray_quit"] == "Quitter"
    assert "Destination proposee" in PREVIEW_COLUMNS
    assert ACTION_LABELS[PreviewAction.REVIEW] == "A verifier"
    assert UI_TEXT["archive_all_except_review"] == "Tout archiver sauf a verifier"


def test_outlook_account_label_includes_smtp_address() -> None:
    account = OutlookAccount(display_name="Balz", smtp_address="lionel@balzmetal.test")

    assert format_outlook_account_label(account) == "Balz <lionel@balzmetal.test>"


def test_ai_settings_labels_are_french() -> None:
    assert ai_mode_label(AiMode.DISABLED) == "desactivee"
    assert ai_mode_label(AiMode.AMBIGUOUS_ONLY) == "ambigu seulement"
    assert ai_mode_label(AiMode.ALL) == "tout classifier"
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


def test_main_window_instantiates_when_pyside6_is_available() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLineEdit

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(), controller=FakeController())
    dynamic_window = cast(Any, window)

    assert window.windowTitle() == "MailFlow Archivist"
    assert dynamic_window.mailflow_outlook_root_combo.currentText() == "Boite de reception"
    assert dynamic_window.mailflow_mail_preview.isReadOnly()
    assert dynamic_window.mailflow_ai_mode_combo.currentData() == AiMode.AMBIGUOUS_ONLY.value
    assert dynamic_window.mailflow_ai_model_input.text() == "gpt-5.4-nano"
    assert dynamic_window.mailflow_openai_key_input.echoMode() == QLineEdit.EchoMode.Password
    assert dynamic_window.mailflow_test_openai_key_button.text() == "Tester IA"
    assert dynamic_window.mailflow_ai_include_body_checkbox.isChecked()
    assert dynamic_window.mailflow_watch_checkbox.text() == "Surveillance Outlook"
    assert dynamic_window.mailflow_watch_timer.interval() == 300000
    assert dynamic_window.mailflow_tray_icon.toolTip() == "MailFlow Archivist"
    assert dynamic_window.mailflow_tray_open_action.text() == "Ouvrir MailFlow"
    assert dynamic_window.mailflow_tray_watch_action.isCheckable()
    assert dynamic_window.mailflow_tray_quit_action.text() == "Quitter"
    assert dynamic_window.mailflow_folder_tree.headerItem().text(0) == "Dossier propose"
    assert dynamic_window.mailflow_rename_folder_button.text() == "Renommer dossier"
    assert dynamic_window.mailflow_merge_folder_button.text() == "Fusionner vers..."
    assert dynamic_window.mailflow_restore_archivable_button.text() == (
        "Tout remettre a archiver"
    )
    assert dynamic_window.mailflow_main_splitter.count() == 7
    assert dynamic_window.mailflow_scroll_area.widget() == dynamic_window.mailflow_main_splitter
    assert dynamic_window.mailflow_main_splitter.minimumHeight() > 0
    assert "Previsualisation" in dynamic_window.mailflow_section_toggles
    assert "Arborescence" in dynamic_window.mailflow_section_toggles
    assert "Previsualisation" in dynamic_window.mailflow_section_widgets
    window.close()
    app.quit()
