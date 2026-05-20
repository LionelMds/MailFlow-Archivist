from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mailflow.core.archive_actions import rows_to_archive
from mailflow.core.archive_batch import ArchiveBatchResult
from mailflow.core.background_watcher import WatchState
from mailflow.models import (
    InterlocutorType,
    MailType,
    ManualClassificationUpdate,
    OutlookAccount,
    PreviewRow,
)

if TYPE_CHECKING:
    from mailflow.config import AppSettings


UI_TEXT = {
    "window_title": "MailFlow Archivist",
    "configuration": "Configuration",
    "scan": "Scan",
    "preview": "Previsualisation",
    "actions": "Actions",
    "logs": "Logs",
    "scan_button": "Scanner Outlook",
    "watch_outlook": "Surveillance Outlook",
    "archive_selection": "Archiver selection",
    "archive_all_except_review": "Tout archiver sauf a verifier",
    "mark_ignored": "Marquer comme ignore",
    "open_project_folder": "Ouvrir dossier projet",
    "export_project_html": "Exporter HTML projet",
    "export_report": "Exporter rapport",
}

WATCH_INTERVAL_MS = 5 * 60 * 1000

@dataclass(frozen=True)
class ArchiveSelectionSummary:
    selected_count: int
    ready_count: int
    skipped_count: int

    @property
    def can_archive(self) -> bool:
        return self.ready_count > 0


def run_desktop_app(settings: AppSettings) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        msg = "PySide6 est requis pour lancer l'interface desktop"
        raise RuntimeError(msg) from exc

    app = QApplication([])
    window = MainWindow(settings)
    window.show()
    return int(app.exec())


def MainWindow(settings: AppSettings, controller: Any | None = None) -> Any:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    from mailflow.core.app_controller import PreviewRequest, build_default_controller
    from mailflow.ui.mail_preview import preview_row_to_html
    from mailflow.ui.preview_table import (
        DESTINATION_COLUMN,
        DESTINATION_OPTIONS,
        INTERLOCUTOR_COLUMN,
        INTERLOCUTOR_OPTIONS,
        MAIL_TYPE_OPTIONS,
        PREVIEW_COLUMNS,
        TYPE_COLUMN,
        editable_options_for_column,
        preview_row_to_cells,
        should_highlight_cell,
    )

    controller_was_injected = controller is not None
    active_controller = controller or build_default_controller(settings)
    window = QMainWindow()
    dynamic_window = cast(Any, window)
    window.setWindowTitle(UI_TEXT["window_title"])
    combo_by_cell: dict[tuple[int, int], Any] = {}
    refreshing_table = False
    refreshing_outlook_options = False
    watch_state = WatchState()
    central = QWidget()
    layout = QVBoxLayout(central)

    config = QGroupBox(UI_TEXT["configuration"])
    grid = QGridLayout(config)
    grid.addWidget(QLabel("Racine projets locale"), 0, 0)
    projects_root_input = QLineEdit(str(settings.local_projects_root))
    projects_root_picker = QWidget()
    projects_root_layout = QHBoxLayout(projects_root_picker)
    projects_root_layout.setContentsMargins(0, 0, 0, 0)
    projects_root_layout.addWidget(projects_root_input)
    browse_projects_button = QPushButton("Parcourir")
    projects_root_layout.addWidget(browse_projects_button)
    grid.addWidget(projects_root_picker, 0, 1)
    grid.addWidget(QLabel("Compte Outlook"), 1, 0)
    account_combo = QComboBox()
    account_combo.setEditable(True)
    grid.addWidget(account_combo, 1, 1)
    grid.addWidget(QLabel("Dossier Outlook racine"), 2, 0)
    outlook_root_combo = QComboBox()
    outlook_root_combo.setEditable(True)
    grid.addWidget(outlook_root_combo, 2, 1)
    grid.addWidget(QLabel("Mode IA"), 3, 0)
    grid.addWidget(QLineEdit(settings.ai_mode.value), 3, 1)
    layout.addWidget(config)

    scan = QGroupBox(UI_TEXT["scan"])
    scan_layout = QGridLayout(scan)
    scan_layout.addWidget(QLabel("Annee"), 0, 0)
    year_input = QLineEdit(settings.selected_year or "")
    scan_layout.addWidget(year_input, 0, 1)
    scan_layout.addWidget(QLabel("Projet specifique"), 1, 0)
    project_input = QLineEdit("")
    scan_layout.addWidget(project_input, 1, 1)
    scan_button = QPushButton(UI_TEXT["scan_button"])
    scan_layout.addWidget(scan_button, 2, 0, 1, 2)
    watch_checkbox = QCheckBox(UI_TEXT["watch_outlook"])
    scan_layout.addWidget(watch_checkbox, 3, 0, 1, 2)
    layout.addWidget(scan)

    table = QTableWidget(0, 10)
    table.setHorizontalHeaderLabels(list(PREVIEW_COLUMNS))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    layout.addWidget(table)

    preview = QGroupBox("Apercu du mail")
    preview_layout = QVBoxLayout(preview)
    mail_preview = QTextEdit()
    mail_preview.setReadOnly(True)
    mail_preview.setMinimumHeight(150)
    preview_layout.addWidget(mail_preview)
    layout.addWidget(preview)

    actions = QGroupBox(UI_TEXT["actions"])
    actions_layout = QGridLayout(actions)
    archive_button = QPushButton(UI_TEXT["archive_selection"])
    archive_all_button = QPushButton(UI_TEXT["archive_all_except_review"])
    ignore_button = QPushButton(UI_TEXT["mark_ignored"])
    open_folder_button = QPushButton(UI_TEXT["open_project_folder"])
    export_html_button = QPushButton(UI_TEXT["export_project_html"])
    report_button = QPushButton(UI_TEXT["export_report"])
    for index, button in enumerate(
        [
            archive_button,
            archive_all_button,
            ignore_button,
            open_folder_button,
            export_html_button,
            report_button,
        ]
    ):
        actions_layout.addWidget(button, index // 3, index % 3)
    layout.addWidget(actions)

    logs = QTextEdit()
    logs.setReadOnly(True)
    logs.setPlaceholderText(UI_TEXT["logs"])
    layout.addWidget(logs)
    window.setCentralWidget(central)
    watch_timer = QTimer(window)
    watch_timer.setInterval(WATCH_INTERVAL_MS)

    def append_log(message: str) -> None:
        logs.append(message)

    def refresh_table() -> None:
        nonlocal refreshing_table
        refreshing_table = True
        combo_by_cell.clear()
        table.clearContents()
        table.setColumnCount(len(PREVIEW_COLUMNS))
        table.setRowCount(len(active_controller.preview_rows))
        table.setHorizontalHeaderLabels(list(PREVIEW_COLUMNS))
        for row_index, row in enumerate(active_controller.preview_rows):
            for column_index, value in enumerate(preview_row_to_cells(row)):
                options = editable_options_for_column(column_index)
                if options is None:
                    item = QTableWidgetItem(value)
                    if should_highlight_cell(row, column_index):
                        item.setBackground(QColor("#fff3b0"))
                    table.setItem(row_index, column_index, item)
                    continue
                combo = QComboBox()
                combo.addItems(list(options))
                if value in options:
                    combo.setCurrentText(value)
                if should_highlight_cell(row, column_index):
                    combo.setStyleSheet("QComboBox { background-color: #fff3b0; }")
                combo.currentTextChanged.connect(
                    lambda _text, row=row_index: open_manual_dialog(row)
                )
                table.setCellWidget(row_index, column_index, combo)
                combo_by_cell[(row_index, column_index)] = combo
        table.resizeColumnsToContents()
        refreshing_table = False
        update_mail_preview(table.currentRow())

    def combo_text(row_index: int, column_index: int) -> str:
        combo = combo_by_cell.get((row_index, column_index))
        if combo is None:
            return ""
        return str(combo.currentText())

    def selected_account_identifier() -> str | None:
        current_index = account_combo.currentIndex()
        current_text = account_combo.currentText().strip()
        if current_index >= 0 and account_combo.itemText(current_index) == current_text:
            data = account_combo.itemData(current_index)
            if data:
                return str(data)
        return clean_optional_text(current_text)

    def current_outlook_root_folder() -> str:
        return outlook_root_combo.currentText().strip()

    def set_account_combo_value(identifier: str | None) -> None:
        if not identifier:
            if account_combo.count() > 0:
                account_combo.setCurrentIndex(0)
            return
        for index in range(account_combo.count()):
            data = account_combo.itemData(index)
            if _same_choice(str(data), identifier) or _same_choice(
                account_combo.itemText(index),
                identifier,
            ):
                account_combo.setCurrentIndex(index)
                return
        account_combo.setEditText(identifier)

    def set_folder_combo_value(combo: Any, value: str, options: list[str]) -> None:
        for option in options:
            if _same_choice(option, value):
                combo.setCurrentText(option)
                return
        if value:
            combo.setEditText(value)

    def populate_account_options() -> None:
        nonlocal refreshing_outlook_options
        current = selected_account_identifier() or settings.selected_outlook_account
        refreshing_outlook_options = True
        account_combo.blockSignals(True)
        account_combo.clear()
        try:
            accounts = active_controller.available_outlook_accounts()
        except Exception as exc:
            accounts = []
            append_log(f"Impossible de lire les comptes Outlook: {exc}")
        for account in accounts:
            account_combo.addItem(
                format_outlook_account_label(account),
                account_identifier(account),
            )
        if not accounts and current:
            account_combo.addItem(current, current)
        set_account_combo_value(current or active_controller.suggested_account_identifier())
        account_combo.blockSignals(False)
        refreshing_outlook_options = False
        populate_outlook_root_options()

    def populate_outlook_root_options() -> None:
        if refreshing_outlook_options:
            return
        current = current_outlook_root_folder() or settings.outlook_root_folder
        outlook_root_combo.blockSignals(True)
        outlook_root_combo.clear()
        try:
            folders = active_controller.available_outlook_root_folders(
                selected_account_identifier()
            )
        except Exception as exc:
            folders = []
            append_log(f"Impossible de lire les dossiers Outlook: {exc}")
        for folder in folders:
            outlook_root_combo.addItem(folder)
        if not folders and current:
            outlook_root_combo.addItem(current)
        set_folder_combo_value(outlook_root_combo, current, folders)
        outlook_root_combo.blockSignals(False)

    def browse_projects_root() -> None:
        selected = QFileDialog.getExistingDirectory(
            window,
            "Selectionner la racine projets locale",
            projects_root_input.text().strip(),
        )
        if selected:
            projects_root_input.setText(selected)

    def update_mail_preview(row_index: int) -> None:
        if row_index < 0 or row_index >= len(active_controller.preview_rows):
            mail_preview.clear()
            return
        mail_preview.setHtml(preview_row_to_html(active_controller.preview_rows[row_index]))

    def open_manual_dialog(row_index: int) -> None:
        if refreshing_table:
            return
        update = ask_manual_classification(row_index)
        if update is None:
            refresh_table()
            return
        try:
            updated = active_controller.apply_manual_update(row_index, update)
            refresh_table()
            append_log(
                "Classement manuel enregistre pour "
                f"{updated.mail.project_number}: "
                f"{updated.decision.mail_type.value} -> "
                f"{updated.decision.target_relative_folder}."
            )
        except Exception as exc:
            refresh_table()
            append_log(f"Erreur classement manuel: {exc}")

    def ask_manual_classification(row_index: int) -> ManualClassificationUpdate | None:
        if row_index < 0 or row_index >= len(active_controller.preview_rows):
            return None
        row = active_controller.preview_rows[row_index]
        dialog = QDialog(window)
        dialog.setWindowTitle("Classement manuel")
        dialog.setMinimumWidth(560)
        form = QFormLayout(dialog)

        subject_label = QLabel(row.mail.subject)
        subject_label.setWordWrap(True)
        form.addRow("Projet", QLabel(row.mail.project_number))
        form.addRow("Date", QLabel(row.mail.sent_at.strftime("%Y-%m-%d %H:%M")))
        form.addRow("Sens", QLabel("Envoye" if row.mail.direction.value == "sent" else "Recu"))
        form.addRow("Expediteur", QLabel(row.mail.sender_name or row.mail.sender_email))
        form.addRow("Sujet", subject_label)

        mail_type_combo = QComboBox()
        mail_type_combo.addItems(list(MAIL_TYPE_OPTIONS))
        set_combo_value(
            mail_type_combo,
            combo_text(row_index, TYPE_COLUMN) or row.decision.mail_type.value,
            MAIL_TYPE_OPTIONS,
        )
        interlocutor_combo = QComboBox()
        interlocutor_combo.addItems(list(INTERLOCUTOR_OPTIONS))
        set_combo_value(
            interlocutor_combo,
            combo_text(row_index, INTERLOCUTOR_COLUMN) or row.decision.interlocutor.value,
            INTERLOCUTOR_OPTIONS,
        )
        destination_combo = QComboBox()
        destination_combo.addItems(list(DESTINATION_OPTIONS))
        set_combo_value(
            destination_combo,
            combo_text(row_index, DESTINATION_COLUMN) or row.decision.target_relative_folder,
            DESTINATION_OPTIONS,
        )
        form.addRow("Type detecte", mail_type_combo)
        form.addRow("Interlocuteur", interlocutor_combo)
        form.addRow("Destination proposee", destination_combo)

        term_input = QLineEdit()
        misleading_term_input = QLineEdit()
        none_checkbox = QCheckBox("Aucun terme: classement manuel necessaire")
        form.addRow("Terme qui identifie le classement", term_input)
        form.addRow("Terme trompeur / mauvais indice", misleading_term_input)
        form.addRow("", none_checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        none_checkbox.toggled.connect(term_input.setDisabled)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        if none_checkbox.isChecked():
            return build_manual_classification_update(
                mail_type_value=mail_type_combo.currentText(),
                interlocutor_value=interlocutor_combo.currentText(),
                destination_value=destination_combo.currentText(),
                learning_term=None,
                misleading_term=clean_optional_text(misleading_term_input.text()),
                manual_required=True,
            )
        return build_manual_classification_update(
            mail_type_value=mail_type_combo.currentText(),
            interlocutor_value=interlocutor_combo.currentText(),
            destination_value=destination_combo.currentText(),
            learning_term=clean_optional_text(term_input.text()),
            misleading_term=clean_optional_text(misleading_term_input.text()),
            manual_required=False,
        )

    def set_combo_value(combo: Any, value: str, options: tuple[str, ...]) -> None:
        if value in options:
            combo.setCurrentText(value)

    def update_projects_root() -> None:
        settings.local_projects_root = Path(projects_root_input.text())
        settings.selected_outlook_account = selected_account_identifier()
        settings.outlook_root_folder = current_outlook_root_folder()
        settings.selected_year = clean_optional_text(year_input.text())

    def scan_current_preview() -> list[PreviewRow]:
        nonlocal active_controller
        update_projects_root()
        if not controller_was_injected:
            active_controller = build_default_controller(settings)
            dynamic_window.mailflow_controller = active_controller
        return active_controller.scan_and_preview(
            PreviewRequest(
                account_identifier=selected_account_identifier(),
                outlook_root_folder=current_outlook_root_folder(),
                year=year_input.text(),
                project_number=project_input.text(),
            )
        )

    def on_scan() -> None:
        try:
            rows = scan_current_preview()
            watch_state.reset(rows)
            refresh_table()
            append_log(f"{len(rows)} mails charges en previsualisation.")
        except Exception as exc:
            append_log(f"Erreur scan: {exc}")

    def on_export_report() -> None:
        try:
            path = active_controller.export_report()
            append_log(f"Rapport exporte: {path}")
        except Exception as exc:
            append_log(f"Erreur export rapport: {exc}")

    def on_export_project_html() -> None:
        if not active_controller.preview_rows:
            append_log("Aucun mail en previsualisation.")
            return
        try:
            results = active_controller.export_project_html(overwrite_html=False)
        except FileExistsError as exc:
            path = Path(str(exc))
            if not confirm_html_overwrite(path, parent=window):
                append_log("Export HTML annule.")
                return
            try:
                results = active_controller.export_project_html(overwrite_html=True)
            except Exception as retry_exc:
                append_log(f"Erreur export HTML projet: {retry_exc}")
                return
        except Exception as exc:
            append_log(f"Erreur export HTML projet: {exc}")
            return
        append_log(format_project_html_export_result(results))

    def on_mark_ignored() -> None:
        active_controller.mark_all_ignored()
        refresh_table()
        append_log("Lignes marquees comme ignorees.")

    def on_watch_toggled(enabled: bool) -> None:
        if not enabled:
            watch_timer.stop()
            append_log("Surveillance Outlook desactivee.")
            return
        try:
            if not active_controller.preview_rows:
                rows = scan_current_preview()
                refresh_table()
                append_log(f"{len(rows)} mails charges pour initialiser la surveillance.")
            watch_state.reset(active_controller.preview_rows)
            watch_timer.start()
            append_log("Surveillance Outlook activee: scan toutes les 5 minutes.")
        except Exception as exc:
            watch_checkbox.blockSignals(True)
            watch_checkbox.setChecked(False)
            watch_checkbox.blockSignals(False)
            append_log(f"Impossible d'activer la surveillance Outlook: {exc}")

    def run_watch_scan() -> None:
        try:
            rows = scan_current_preview()
            change = watch_state.update(rows)
            refresh_table()
        except Exception as exc:
            append_log(f"Surveillance Outlook en attente: {exc}")
            return
        if change.new_count == 0:
            return
        append_log(f"Surveillance Outlook: {change.new_count} nouveau(x) mail(s) detecte(s).")
        if not confirm_watch_html_update(change.new_count, parent=window):
            append_log("Mise a jour HTML differee.")
            return
        try:
            results = active_controller.export_project_html(overwrite_html=True)
            append_log(format_project_html_export_result(results))
        except Exception as exc:
            append_log(f"Erreur mise a jour HTML automatique: {exc}")

    def selected_table_row_indexes() -> list[int]:
        selection_model = table.selectionModel()
        if selection_model is None:
            return []
        rows = {index.row() for index in selection_model.selectedRows()}
        if not rows:
            rows = {index.row() for index in table.selectedIndexes()}
        return sorted(rows)

    def update_preview_from_selection() -> None:
        selected = selected_table_row_indexes()
        update_mail_preview(selected[0] if selected else table.currentRow())

    def confirm_archive(summary: ArchiveSelectionSummary, *, title: str) -> bool:
        response = QMessageBox.question(
            window,
            title,
            build_archive_confirmation_message(summary),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return response == QMessageBox.StandardButton.Yes

    def on_archive_selection() -> None:
        indexes = selected_table_row_indexes()
        summary = summarize_archive_selection(active_controller.preview_rows, indexes)
        if summary.selected_count == 0:
            append_log("Aucune ligne selectionnee.")
            return
        if not summary.can_archive:
            append_log("Aucune ligne selectionnee n'est prete a archiver.")
            return
        if not confirm_archive(summary, title="Confirmer l'archivage de la selection"):
            append_log("Archivage annule.")
            return
        try:
            result = active_controller.archive_selected(indexes, include_review=False)
            refresh_table()
            append_log(format_archive_result(result))
            for failure in result.failures[:5]:
                append_log(f"Echec {failure.mail_id}: {failure.reason}")
        except Exception as exc:
            append_log(f"Erreur archivage: {exc}")

    def on_archive_all_except_review() -> None:
        indexes = list(range(len(active_controller.preview_rows)))
        summary = summarize_archive_selection(active_controller.preview_rows, indexes)
        if summary.selected_count == 0:
            append_log("Aucune ligne en previsualisation.")
            return
        if not summary.can_archive:
            append_log("Aucune ligne n'est prete a archiver.")
            return
        if not confirm_archive(summary, title="Confirmer l'archivage global"):
            append_log("Archivage annule.")
            return
        try:
            result = active_controller.archive_ready(include_review=False)
            refresh_table()
            append_log(format_archive_result(result))
            for failure in result.failures[:5]:
                append_log(f"Echec {failure.mail_id}: {failure.reason}")
        except Exception as exc:
            append_log(f"Erreur archivage global: {exc}")

    scan_button.clicked.connect(on_scan)
    watch_checkbox.toggled.connect(on_watch_toggled)
    watch_timer.timeout.connect(run_watch_scan)
    report_button.clicked.connect(on_export_report)
    export_html_button.clicked.connect(on_export_project_html)
    ignore_button.clicked.connect(on_mark_ignored)
    archive_button.clicked.connect(on_archive_selection)
    archive_all_button.clicked.connect(on_archive_all_except_review)
    browse_projects_button.clicked.connect(browse_projects_root)
    account_combo.currentIndexChanged.connect(lambda _index: populate_outlook_root_options())
    open_folder_button.clicked.connect(lambda: append_log(str(settings.local_projects_root)))
    table.cellDoubleClicked.connect(lambda row, _column: open_manual_dialog(row))
    table.currentCellChanged.connect(lambda row, _col, _old_row, _old_col: update_mail_preview(row))
    table.itemSelectionChanged.connect(update_preview_from_selection)
    populate_account_options()

    dynamic_window.mailflow_controller = active_controller
    dynamic_window.mailflow_preview_table = table
    dynamic_window.mailflow_logs = logs
    dynamic_window.mailflow_mail_preview = mail_preview
    dynamic_window.mailflow_export_html_button = export_html_button
    dynamic_window.mailflow_watch_checkbox = watch_checkbox
    dynamic_window.mailflow_watch_timer = watch_timer
    dynamic_window.mailflow_account_combo = account_combo
    dynamic_window.mailflow_outlook_root_combo = outlook_root_combo
    dynamic_window.mailflow_projects_root_input = projects_root_input
    return window


def clean_optional_text(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


def account_identifier(account: OutlookAccount) -> str:
    return account.smtp_address or account.display_name


def format_outlook_account_label(account: OutlookAccount) -> str:
    if account.smtp_address:
        return f"{account.display_name} <{account.smtp_address}>"
    return account.display_name


def summarize_archive_selection(
    rows: Sequence[PreviewRow],
    row_indexes: Sequence[int],
) -> ArchiveSelectionSummary:
    selected_rows = [
        rows[index]
        for index in sorted(set(row_indexes))
        if 0 <= index < len(rows)
    ]
    ready_rows = rows_to_archive(list(selected_rows), include_review=False)
    return ArchiveSelectionSummary(
        selected_count=len(selected_rows),
        ready_count=len(ready_rows),
        skipped_count=len(selected_rows) - len(ready_rows),
    )


def build_archive_confirmation_message(summary: ArchiveSelectionSummary) -> str:
    lines = [
        f"{summary.ready_count} mail(s) pret(s) vont etre archives.",
        "Les mails a verifier, ignores ou deja archives ne seront pas exportes.",
        "Aucun fichier .msg existant ne sera ecrase.",
    ]
    if summary.skipped_count:
        lines.insert(1, f"{summary.skipped_count} ligne(s) selectionnee(s) seront ignorees.")
    return "\n".join(lines)


def confirm_html_overwrite(path: Path, *, parent: Any | None = None) -> bool:
    from PySide6.QtWidgets import QMessageBox

    response = QMessageBox.question(
        parent,
        "Mettre a jour le journal HTML",
        (
            "Le fichier HTML existe deja:\n"
            f"{path}\n\n"
            "Le mettre a jour avec la previsualisation actuelle ?"
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return response == QMessageBox.StandardButton.Yes


def confirm_watch_html_update(new_count: int, *, parent: Any | None = None) -> bool:
    from PySide6.QtWidgets import QMessageBox

    response = QMessageBox.question(
        parent,
        "Nouveaux mails detectes",
        (
            f"{new_count} nouveau(x) mail(s) ont ete detectes dans Outlook.\n\n"
            "Mettre a jour le journal HTML projet maintenant ?"
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return response == QMessageBox.StandardButton.Yes


def format_project_html_export_result(results: Sequence[object]) -> str:
    if not results:
        return "Aucun journal HTML exporte."
    lines = ["Export HTML termine:"]
    for result in results:
        path = getattr(result, "html_path", "")
        count = getattr(result, "mail_count", 0)
        attachment_count = len(getattr(result, "attachment_paths", []))
        lines.append(f"- {count} mail(s), {attachment_count} piece(s) jointe(s): {path}")
    return "\n".join(lines)


def format_archive_result(result: ArchiveBatchResult) -> str:
    return (
        "Archivage termine: "
        f"{result.exported_count} exporte(s), "
        f"{result.skipped_count} ignore(s), "
        f"{result.failure_count} erreur(s)."
    )


def _same_choice(left: str, right: str) -> bool:
    return _normalize_choice(left) == _normalize_choice(right)


def _normalize_choice(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())


def build_manual_classification_update(
    *,
    mail_type_value: str,
    interlocutor_value: str,
    destination_value: str,
    learning_term: str | None,
    misleading_term: str | None = None,
    manual_required: bool = False,
) -> ManualClassificationUpdate:
    return ManualClassificationUpdate(
        mail_type=MailType(mail_type_value),
        interlocutor=InterlocutorType(interlocutor_value),
        target_relative_folder=destination_value,
        learning_term=learning_term,
        misleading_term=misleading_term,
        manual_required=manual_required,
    )
