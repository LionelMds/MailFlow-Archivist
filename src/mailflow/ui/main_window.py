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
    AiMode,
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
    "save_settings": "Enregistrer parametres",
    "save_openai_key": "Enregistrer cle",
    "test_openai_key": "Tester IA",
    "archive_selection": "Archiver selection",
    "archive_all_except_review": "Tout archiver sauf a verifier",
    "mark_ignored": "Ignorer selection",
    "restore_archivable": "Tout remettre a archiver",
    "open_project_folder": "Ouvrir dossier projet",
    "export_project_html": "Exporter HTML projet",
    "export_report": "Exporter rapport",
    "tray_open": "Ouvrir MailFlow",
    "tray_enable_watch": "Activer surveillance Outlook",
    "tray_disable_watch": "Desactiver surveillance Outlook",
    "tray_quit": "Quitter",
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
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QAction, QColor
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QStyle,
        QSystemTrayIcon,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    from mailflow.classifier.ai_classifier import AiClassifier
    from mailflow.config import (
        get_openai_api_key,
        save_settings,
        set_openai_api_key,
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

    class MailFlowMainWindow(QMainWindow):
        def closeEvent(self, event: Any) -> None:
            handler = getattr(self, "mailflow_close_handler", None)
            if callable(handler):
                handler(event)
                return
            super().closeEvent(event)

    controller_was_injected = controller is not None
    active_controller = controller or build_default_controller(settings)
    window = MailFlowMainWindow()
    dynamic_window = cast(Any, window)
    window.setWindowTitle(UI_TEXT["window_title"])
    dynamic_window.mailflow_force_quit = False
    combo_by_cell: dict[tuple[int, int], Any] = {}
    refreshing_table = False
    refreshing_outlook_options = False
    watch_paused_logged = False
    watch_state = WatchState()
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.setContentsMargins(0, 0, 0, 0)
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    main_splitter = QSplitter(Qt.Orientation.Vertical)
    main_splitter.setChildrenCollapsible(False)
    main_splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    section_toggles: dict[str, Any] = {}
    section_widgets: dict[str, Any] = {}
    section_contents: dict[str, Any] = {}
    section_min_heights = {
        UI_TEXT["configuration"]: 235,
        UI_TEXT["scan"]: 150,
        UI_TEXT["preview"]: 320,
        "Arborescence": 230,
        "Apercu du mail": 220,
        UI_TEXT["actions"]: 125,
        UI_TEXT["logs"]: 190,
    }
    collapsed_section_height = 34
    max_widget_height = 16777215
    scroll_area.setWidget(main_splitter)
    layout.addWidget(scroll_area)

    def update_splitter_layout() -> None:
        sizes = []
        total_height = 0
        for index in range(main_splitter.count()):
            section = main_splitter.widget(index)
            if section is None:
                continue
            title = str(section.property("mailflow_section_title") or "")
            content = section_contents.get(title)
            if content is not None and content.isVisible():
                height = section_min_heights.get(title, section.minimumSizeHint().height())
                section.setMinimumHeight(height)
                section.setMaximumHeight(max_widget_height)
            else:
                height = collapsed_section_height
                section.setMinimumHeight(height)
                section.setMaximumHeight(height)
            sizes.append(height)
            total_height += height
        if sizes:
            total_height += max(0, len(sizes) - 1) * main_splitter.handleWidth()
            main_splitter.setMinimumHeight(total_height)
            main_splitter.setSizes(sizes)
        main_splitter.updateGeometry()
        scroll_area.updateGeometry()

    def make_collapsible_section(title: str, content: Any) -> Any:
        section = QWidget()
        section.setProperty("mailflow_section_title", title)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(2)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 2, 4, 2)
        toggle = QToolButton()
        toggle.setAutoRaise(True)
        toggle.setArrowType(Qt.ArrowType.DownArrow)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600;")
        header_layout.addWidget(toggle)
        header_layout.addWidget(title_label)
        header_layout.addStretch(1)
        section_layout.addWidget(header)
        section_layout.addWidget(content)

        def toggle_content() -> None:
            content.setVisible(not content.isVisible())
            toggle.setArrowType(
                Qt.ArrowType.DownArrow if content.isVisible() else Qt.ArrowType.RightArrow
            )
            update_splitter_layout()

        toggle.clicked.connect(toggle_content)
        section_toggles[title] = toggle
        section_widgets[title] = section
        section_contents[title] = content
        return section

    config = QGroupBox()
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
    ai_mode_combo = QComboBox()
    for mode in AiMode:
        ai_mode_combo.addItem(ai_mode_label(mode), mode.value)
    set_combo_value_by_data(ai_mode_combo, settings.ai_mode.value)
    grid.addWidget(ai_mode_combo, 3, 1)
    grid.addWidget(QLabel("Modele IA"), 4, 0)
    ai_model_input = QLineEdit(settings.ai_model)
    grid.addWidget(ai_model_input, 4, 1)
    grid.addWidget(QLabel("Cle API OpenAI"), 5, 0)
    openai_key_widget = QWidget()
    openai_key_layout = QHBoxLayout(openai_key_widget)
    openai_key_layout.setContentsMargins(0, 0, 0, 0)
    openai_key_input = QLineEdit()
    openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    openai_key_input.setPlaceholderText("Coller une nouvelle cle puis enregistrer")
    save_openai_key_button = QPushButton(UI_TEXT["save_openai_key"])
    test_openai_key_button = QPushButton(UI_TEXT["test_openai_key"])
    openai_key_status = QLabel(openai_key_status_text(get_openai_api_key() is not None))
    openai_key_status.setStyleSheet(openai_key_status_style(get_openai_api_key() is not None))
    openai_key_layout.addWidget(openai_key_input)
    openai_key_layout.addWidget(save_openai_key_button)
    openai_key_layout.addWidget(test_openai_key_button)
    openai_key_layout.addWidget(openai_key_status)
    grid.addWidget(openai_key_widget, 5, 1)
    ai_include_body_checkbox = QCheckBox("Envoyer l'extrait nettoye du corps a l'IA")
    ai_include_body_checkbox.setChecked(settings.ai_include_body_excerpt)
    grid.addWidget(ai_include_body_checkbox, 6, 1)
    privacy_phone_checkbox = QCheckBox("Masquer les numeros de telephone avant IA")
    privacy_phone_checkbox.setChecked(settings.privacy_mask_phone_numbers)
    grid.addWidget(privacy_phone_checkbox, 7, 1)
    save_settings_button = QPushButton(UI_TEXT["save_settings"])
    grid.addWidget(save_settings_button, 8, 1)
    main_splitter.addWidget(make_collapsible_section(UI_TEXT["configuration"], config))

    scan = QGroupBox()
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
    main_splitter.addWidget(make_collapsible_section(UI_TEXT["scan"], scan))

    table = QTableWidget(0, len(PREVIEW_COLUMNS))
    table.setHorizontalHeaderLabels(list(PREVIEW_COLUMNS))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setMinimumHeight(260)
    table.horizontalHeader().setSectionsMovable(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    main_splitter.addWidget(make_collapsible_section(UI_TEXT["preview"], table))

    tree_widget = QWidget()
    tree_layout = QVBoxLayout(tree_widget)
    tree_layout.setContentsMargins(0, 0, 0, 0)
    folder_tree = QTreeWidget()
    folder_tree.setHeaderLabels(["Dossier propose", "Mails"])
    folder_tree.setMinimumHeight(170)
    folder_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    folder_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    tree_buttons = QWidget()
    tree_buttons_layout = QHBoxLayout(tree_buttons)
    tree_buttons_layout.setContentsMargins(0, 0, 0, 0)
    rename_folder_button = QPushButton("Renommer dossier")
    merge_folder_button = QPushButton("Fusionner vers...")
    tree_buttons_layout.addWidget(rename_folder_button)
    tree_buttons_layout.addWidget(merge_folder_button)
    tree_buttons_layout.addStretch(1)
    tree_layout.addWidget(folder_tree)
    tree_layout.addWidget(tree_buttons)
    main_splitter.addWidget(make_collapsible_section("Arborescence", tree_widget))

    preview = QGroupBox()
    preview_layout = QVBoxLayout(preview)
    mail_preview = QTextEdit()
    mail_preview.setReadOnly(True)
    mail_preview.setMinimumHeight(150)
    preview_layout.addWidget(mail_preview)
    main_splitter.addWidget(make_collapsible_section("Apercu du mail", preview))

    actions = QGroupBox()
    actions_layout = QGridLayout(actions)
    archive_button = QPushButton(UI_TEXT["archive_selection"])
    archive_all_button = QPushButton(UI_TEXT["archive_all_except_review"])
    ignore_button = QPushButton(UI_TEXT["mark_ignored"])
    restore_archivable_button = QPushButton(UI_TEXT["restore_archivable"])
    open_folder_button = QPushButton(UI_TEXT["open_project_folder"])
    export_html_button = QPushButton(UI_TEXT["export_project_html"])
    report_button = QPushButton(UI_TEXT["export_report"])
    for index, button in enumerate(
        [
            archive_button,
            archive_all_button,
            ignore_button,
            restore_archivable_button,
            open_folder_button,
            export_html_button,
            report_button,
        ]
    ):
        actions_layout.addWidget(button, index // 3, index % 3)
    main_splitter.addWidget(make_collapsible_section(UI_TEXT["actions"], actions))

    logs = QTextEdit()
    logs.setReadOnly(True)
    logs.setPlaceholderText(UI_TEXT["logs"])
    main_splitter.addWidget(make_collapsible_section(UI_TEXT["logs"], logs))
    main_splitter.setStretchFactor(0, 0)
    main_splitter.setStretchFactor(1, 0)
    main_splitter.setStretchFactor(2, 6)
    main_splitter.setStretchFactor(3, 2)
    main_splitter.setStretchFactor(4, 3)
    main_splitter.setStretchFactor(5, 0)
    main_splitter.setStretchFactor(6, 2)
    update_splitter_layout()
    window.setCentralWidget(central)
    watch_timer = QTimer(window)
    watch_timer.setInterval(WATCH_INTERVAL_MS)
    app_icon = window.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    window.setWindowIcon(app_icon)
    tray_icon = QSystemTrayIcon(app_icon, window)
    tray_icon.setToolTip(UI_TEXT["window_title"])
    tray_menu = QMenu(window)
    tray_open_action = QAction(UI_TEXT["tray_open"], window)
    tray_watch_action = QAction(UI_TEXT["tray_enable_watch"], window)
    tray_watch_action.setCheckable(True)
    tray_quit_action = QAction(UI_TEXT["tray_quit"], window)
    tray_menu.addAction(tray_open_action)
    tray_menu.addAction(tray_watch_action)
    tray_menu.addSeparator()
    tray_menu.addAction(tray_quit_action)
    tray_icon.setContextMenu(tray_menu)
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray_icon.show()

    def append_log(message: str) -> None:
        logs.append(message)

    def has_openai_api_key() -> bool:
        return get_openai_api_key() is not None

    def set_openai_key_status(
        *,
        has_key: bool,
        valid: bool | None = None,
        testing: bool = False,
    ) -> None:
        openai_key_status.setText(
            openai_key_status_text(has_key=has_key, valid=valid, testing=testing)
        )
        openai_key_status.setStyleSheet(
            openai_key_status_style(has_key=has_key, valid=valid, testing=testing)
        )

    def update_openai_key_status(*, valid: bool | None = None) -> None:
        set_openai_key_status(has_key=has_openai_api_key(), valid=valid)

    def show_window_from_tray() -> None:
        window.show()
        window.raise_()
        window.activateWindow()

    def tray_available() -> bool:
        return bool(tray_icon.isVisible() and QSystemTrayIcon.isSystemTrayAvailable())

    def notify_user(
        title: str,
        message: str,
        icon: Any = QSystemTrayIcon.MessageIcon.Information,
    ) -> None:
        if tray_icon.isVisible():
            tray_icon.showMessage(title, message, icon, 6000)

    def sync_tray_watch_action(enabled: bool) -> None:
        tray_watch_action.blockSignals(True)
        tray_watch_action.setChecked(enabled)
        tray_watch_action.setText(
            UI_TEXT["tray_disable_watch"] if enabled else UI_TEXT["tray_enable_watch"]
        )
        tray_watch_action.blockSignals(False)

    def request_watch_from_tray(enabled: bool) -> None:
        if watch_checkbox.isChecked() != enabled:
            watch_checkbox.setChecked(enabled)

    def quit_application() -> None:
        dynamic_window.mailflow_force_quit = True
        watch_timer.stop()
        tray_icon.hide()
        QApplication.quit()

    def handle_window_close(event: Any) -> None:
        if should_hide_to_tray(
            watch_enabled=watch_checkbox.isChecked(),
            tray_available=tray_available(),
            force_quit=bool(dynamic_window.mailflow_force_quit),
        ):
            event.ignore()
            window.hide()
            notify_user(
                "MailFlow reste actif",
                "La surveillance Outlook continue en arriere-plan.",
            )
            append_log("Fenetre masquee: MailFlow continue dans la zone de notification.")
            return
        event.accept()

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
                if value and value not in options:
                    combo.addItem(value)
                if value in options:
                    combo.setCurrentText(value)
                elif value:
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
        refresh_folder_tree()
        update_mail_preview(table.currentRow())

    def refresh_folder_tree() -> None:
        folder_tree.clear()
        for node in active_controller.folder_tree():
            folder_tree.addTopLevelItem(folder_tree_item(node))
        folder_tree.expandAll()

    def folder_tree_item(node: Any) -> Any:
        item = QTreeWidgetItem([str(node.name), str(node.mail_count)])
        item.setData(0, Qt.ItemDataRole.UserRole, str(node.relative_folder))
        for child in node.children:
            item.addChild(folder_tree_item(child))
        return item

    def selected_folder_path() -> str | None:
        item = folder_tree.currentItem()
        if item is None:
            return None
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return clean_optional_text(str(value))

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

    def rename_selected_folder() -> None:
        source = selected_folder_path()
        if source is None:
            append_log("Selectionner un dossier dans l'arborescence.")
            return
        current_name = source.split("/")[-1]
        new_name, accepted = QInputDialog.getText(
            window,
            "Renommer dossier",
            "Nouveau nom du dossier selectionne",
            text=current_name,
        )
        if not accepted:
            return
        try:
            active_controller.rename_preview_folder(source, new_name)
            refresh_table()
            append_log(f"Dossier renomme: {source} -> {new_name.strip()}.")
        except Exception as exc:
            append_log(f"Erreur renommage dossier: {exc}")

    def merge_selected_folder() -> None:
        source = selected_folder_path()
        if source is None:
            append_log("Selectionner un dossier dans l'arborescence.")
            return
        options = [
            summary.relative_folder
            for summary in active_controller.folder_path_counts()
            if summary.relative_folder != source
            and not summary.relative_folder.startswith(f"{source}/")
        ]
        if not options:
            append_log("Aucun dossier cible disponible pour la fusion.")
            return
        target, accepted = QInputDialog.getItem(
            window,
            "Fusionner dossier",
            "Fusionner le dossier selectionne vers",
            options,
            editable=False,
        )
        if not accepted:
            return
        try:
            active_controller.merge_preview_folder(source, target)
            refresh_table()
            append_log(f"Dossier fusionne: {source} -> {target}.")
        except Exception as exc:
            append_log(f"Erreur fusion dossier: {exc}")

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
        destination_combo.setEditable(True)
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
        settings.ai_mode = AiMode(str(ai_mode_combo.currentData()))
        settings.ai_model = clean_optional_text(ai_model_input.text()) or "gpt-5.4-nano"
        settings.ai_include_body_excerpt = ai_include_body_checkbox.isChecked()
        settings.privacy_mask_phone_numbers = privacy_phone_checkbox.isChecked()

    def save_current_settings() -> None:
        try:
            update_projects_root()
            save_settings(settings)
            append_log("Parametres enregistres.")
        except Exception as exc:
            append_log(f"Erreur enregistrement parametres: {exc}")

    def save_openai_key_from_input() -> None:
        api_key = clean_optional_text(openai_key_input.text())
        if api_key is None:
            append_log("Aucune nouvelle cle OpenAI a enregistrer.")
            return
        try:
            set_openai_api_key(api_key)
            openai_key_input.clear()
            update_openai_key_status(valid=None)
            append_log("Cle OpenAI enregistree dans le coffre du systeme.")
        except Exception as exc:
            append_log(f"Erreur enregistrement cle OpenAI: {exc}")

    def test_openai_key_from_input() -> None:
        api_key = clean_optional_text(openai_key_input.text()) or get_openai_api_key()
        if api_key is None:
            set_openai_key_status(has_key=False, valid=False)
            append_log("Aucune cle OpenAI a tester.")
            return
        model = clean_optional_text(ai_model_input.text()) or "gpt-5.4-nano"
        set_openai_key_status(has_key=True, testing=True)
        test_openai_key_button.setEnabled(False)
        QApplication.processEvents()
        try:
            result = AiClassifier(api_key=api_key, model=model).check_connection()
        finally:
            test_openai_key_button.setEnabled(True)
        set_openai_key_status(has_key=True, valid=result.ok)
        append_log(f"Test OpenAI: {result.message}")

    def scan_current_preview() -> list[PreviewRow]:
        nonlocal active_controller
        update_projects_root()
        if settings.ai_mode != AiMode.DISABLED and not has_openai_api_key():
            append_log("Mode IA actif sans cle OpenAI: classement par regles uniquement.")
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
        indexes = selected_table_row_indexes()
        if not indexes:
            append_log("Aucune ligne selectionnee a ignorer.")
            return
        active_controller.mark_selected_ignored(indexes)
        refresh_table()
        append_log(f"{len(indexes)} ligne(s) marquee(s) comme ignoree(s).")

    def on_restore_archivable() -> None:
        active_controller.mark_all_archivable()
        refresh_table()
        append_log("Toutes les lignes archivables sont remises en Archiver.")

    def on_watch_toggled(enabled: bool) -> None:
        if not enabled:
            watch_timer.stop()
            sync_tray_watch_action(False)
            append_log("Surveillance Outlook desactivee.")
            return
        try:
            if not active_controller.preview_rows:
                rows = scan_current_preview()
                refresh_table()
                append_log(f"{len(rows)} mails charges pour initialiser la surveillance.")
            watch_state.reset(active_controller.preview_rows)
            watch_timer.start()
            sync_tray_watch_action(True)
            notify_user(
                "Surveillance activee",
                "MailFlow surveille Outlook toutes les 5 minutes.",
            )
            append_log("Surveillance Outlook activee: scan toutes les 5 minutes.")
        except Exception as exc:
            watch_checkbox.blockSignals(True)
            watch_checkbox.setChecked(False)
            watch_checkbox.blockSignals(False)
            sync_tray_watch_action(False)
            append_log(f"Impossible d'activer la surveillance Outlook: {exc}")

    def run_watch_scan() -> None:
        nonlocal watch_paused_logged
        if should_pause_watch_scan(
            window_visible=window.isVisible(),
            preview_has_rows=bool(active_controller.preview_rows),
        ):
            if not watch_paused_logged:
                append_log(
                    "Surveillance Outlook en attente: previsualisation ouverte, "
                    "aucun scan automatique."
                )
            watch_paused_logged = True
            return
        watch_paused_logged = False
        try:
            rows = scan_current_preview()
            change = watch_state.update(rows)
            refresh_table()
        except Exception as exc:
            append_log(f"Surveillance Outlook en attente: {exc}")
            notify_user(
                "Surveillance Outlook en attente",
                "Outlook n'est pas disponible pour le moment.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
            return
        if change.new_count == 0:
            return
        append_log(f"Surveillance Outlook: {change.new_count} nouveau(x) mail(s) detecte(s).")
        notify_user(
            "Nouveaux mails detectes",
            f"{change.new_count} nouveau(x) mail(s) dans Outlook.",
        )
        show_window_from_tray()
        append_log("Previsualisation et arborescence mises a jour pour verification.")
        if not confirm_watch_html_update(change.new_count, parent=window):
            append_log(
                "Mise a jour HTML differee: verifier l'arborescence puis exporter manuellement."
            )
            return
        try:
            results = active_controller.export_project_html(overwrite_html=True)
            append_log(format_project_html_export_result(results))
        except Exception as exc:
            append_log(f"Erreur mise a jour HTML automatique: {exc}")
            notify_user(
                "Erreur export HTML",
                "La mise a jour du journal HTML a echoue.",
                QSystemTrayIcon.MessageIcon.Warning,
            )

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
    tray_watch_action.toggled.connect(request_watch_from_tray)
    tray_open_action.triggered.connect(show_window_from_tray)
    tray_quit_action.triggered.connect(quit_application)
    tray_icon.activated.connect(
        lambda reason: show_window_from_tray()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    watch_timer.timeout.connect(run_watch_scan)
    report_button.clicked.connect(on_export_report)
    export_html_button.clicked.connect(on_export_project_html)
    save_openai_key_button.clicked.connect(save_openai_key_from_input)
    test_openai_key_button.clicked.connect(test_openai_key_from_input)
    save_settings_button.clicked.connect(save_current_settings)
    ignore_button.clicked.connect(on_mark_ignored)
    restore_archivable_button.clicked.connect(on_restore_archivable)
    archive_button.clicked.connect(on_archive_selection)
    archive_all_button.clicked.connect(on_archive_all_except_review)
    rename_folder_button.clicked.connect(rename_selected_folder)
    merge_folder_button.clicked.connect(merge_selected_folder)
    browse_projects_button.clicked.connect(browse_projects_root)
    account_combo.currentIndexChanged.connect(lambda _index: populate_outlook_root_options())
    open_folder_button.clicked.connect(lambda: append_log(str(settings.local_projects_root)))
    table.cellDoubleClicked.connect(lambda row, _column: open_manual_dialog(row))
    table.currentCellChanged.connect(lambda row, _col, _old_row, _old_col: update_mail_preview(row))
    table.itemSelectionChanged.connect(update_preview_from_selection)
    dynamic_window.mailflow_close_handler = handle_window_close
    populate_account_options()

    dynamic_window.mailflow_controller = active_controller
    dynamic_window.mailflow_preview_table = table
    dynamic_window.mailflow_folder_tree = folder_tree
    dynamic_window.mailflow_rename_folder_button = rename_folder_button
    dynamic_window.mailflow_merge_folder_button = merge_folder_button
    dynamic_window.mailflow_restore_archivable_button = restore_archivable_button
    dynamic_window.mailflow_logs = logs
    dynamic_window.mailflow_mail_preview = mail_preview
    dynamic_window.mailflow_export_html_button = export_html_button
    dynamic_window.mailflow_watch_checkbox = watch_checkbox
    dynamic_window.mailflow_watch_timer = watch_timer
    dynamic_window.mailflow_ai_mode_combo = ai_mode_combo
    dynamic_window.mailflow_ai_model_input = ai_model_input
    dynamic_window.mailflow_openai_key_input = openai_key_input
    dynamic_window.mailflow_openai_key_status = openai_key_status
    dynamic_window.mailflow_save_openai_key_button = save_openai_key_button
    dynamic_window.mailflow_test_openai_key_button = test_openai_key_button
    dynamic_window.mailflow_ai_include_body_checkbox = ai_include_body_checkbox
    dynamic_window.mailflow_privacy_phone_checkbox = privacy_phone_checkbox
    dynamic_window.mailflow_save_settings_button = save_settings_button
    dynamic_window.mailflow_tray_icon = tray_icon
    dynamic_window.mailflow_tray_open_action = tray_open_action
    dynamic_window.mailflow_tray_watch_action = tray_watch_action
    dynamic_window.mailflow_tray_quit_action = tray_quit_action
    dynamic_window.mailflow_account_combo = account_combo
    dynamic_window.mailflow_outlook_root_combo = outlook_root_combo
    dynamic_window.mailflow_projects_root_input = projects_root_input
    dynamic_window.mailflow_scroll_area = scroll_area
    dynamic_window.mailflow_main_splitter = main_splitter
    dynamic_window.mailflow_section_toggles = section_toggles
    dynamic_window.mailflow_section_widgets = section_widgets
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


def ai_mode_label(mode: AiMode) -> str:
    labels = {
        AiMode.DISABLED: "desactivee",
        AiMode.AMBIGUOUS_ONLY: "ambigu seulement",
        AiMode.ALL: "tout classifier",
    }
    return labels[mode]


def openai_key_status_text(
    has_key: bool,
    *,
    valid: bool | None = None,
    testing: bool = False,
) -> str:
    if testing:
        return "Test IA en cours..."
    if not has_key:
        return "Aucune cle"
    if valid is True:
        return "Cle valide - IA OK"
    if valid is False:
        return "Cle invalide ou indisponible"
    return "Cle enregistree (non testee)"


def openai_key_status_style(
    has_key: bool,
    *,
    valid: bool | None = None,
    testing: bool = False,
) -> str:
    if testing:
        color = "#8a5a00"
        background = "#fff8e6"
    elif not has_key or valid is False:
        color = "#9f1239"
        background = "#fff1f2"
    elif valid is True:
        color = "#166534"
        background = "#ecfdf3"
    else:
        color = "#334155"
        background = "#f1f5f9"
    return (
        f"QLabel {{ color: {color}; background: {background}; "
        "border: 1px solid rgba(15, 23, 42, 0.12); border-radius: 4px; "
        "padding: 3px 6px; }}"
    )


def set_combo_value_by_data(combo: Any, value: str) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return
    if combo.currentIndex() < 0 and combo.count() > 0:
        combo.setCurrentIndex(0)


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


def should_hide_to_tray(
    *,
    watch_enabled: bool,
    tray_available: bool,
    force_quit: bool,
) -> bool:
    return watch_enabled and tray_available and not force_quit


def should_pause_watch_scan(*, window_visible: bool, preview_has_rows: bool) -> bool:
    return window_visible and preview_has_rows


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
            "La previsualisation et l'arborescence sont affichees dans MailFlow.\n"
            "Choisir Non pour verifier ou corriger les dossiers avant export.\n\n"
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
