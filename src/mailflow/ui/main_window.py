from __future__ import annotations

import re
import unicodedata
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mailflow.core.archive_actions import rows_to_archive
from mailflow.core.archive_batch import ArchiveBatchResult
from mailflow.core.background_watcher import ReviewQueue, WatchState
from mailflow.core.update_installer import download_update_installer, launch_update_installer
from mailflow.core.updates import UpdateCheckResult, check_for_updates
from mailflow.models import (
    AiMode,
    InterlocutorType,
    MailType,
    ManualClassificationUpdate,
    OutlookAccount,
    PreviewAction,
    PreviewRow,
    RoutingCategory,
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
    "reset_workspace": "Réinitialiser",
    "watch_outlook": "Surveillance Outlook",
    "save_settings": "Enregistrer les réglages",
    "save_openai_key": "Enregistrer la clé",
    "test_openai_key": "Tester IA",
    "check_updates": "Rechercher une mise à jour",
    "archive_selection": "Archiver la sélection",
    "archive": "Archiver",
    "archive_all_except_review": "Archiver tous les mails prêts",
    "mark_ignored": "Ignorer la sélection",
    "restore_archivable": "Rétablir les mails ignorés",
    "reclassify": "Reclasser avec l'IA",
    "background_mode": "Passer en arrière-plan",
    "open_project_folder": "Ouvrir dossier projet",
    "export_project_html": "Exporter le projet en HTML",
    "export_report": "Exporter rapport",
    "import_directory": "Importer annuaire Outlook",
    "refresh_directory": "Actualiser",
    "add_directory": "Ajouter entreprise",
    "delete_directory": "Supprimer entreprise",
    "rename_directory": "Renommer entreprise",
    "merge_directory": "Fusionner entreprise",
    "more_actions": "Plus",
    "tray_open": "Ouvrir MailFlow",
    "tray_enable_watch": "Activer la surveillance Outlook",
    "tray_disable_watch": "Désactiver la surveillance Outlook",
    "tray_watch_active": "surveillance active",
    "tray_watch_inactive": "surveillance inactive",
    "tray_quit": "Quitter",
}

WATCH_INTERVAL_MS = 5 * 60 * 1000
REMINDER_CHECK_INTERVAL_MS = 60 * 1000


def project_folder_selected_by_default(
    project_number: str,
    project_filter: str,
) -> bool:
    cleaned_filter = project_filter.strip()
    if not cleaned_filter:
        return True
    return (
        project_number == cleaned_filter
        or project_number.endswith(f"-{cleaned_filter}")
    )


@dataclass(frozen=True)
class ArchiveSelectionSummary:
    selected_count: int
    ready_count: int
    skipped_count: int

    @property
    def can_archive(self) -> bool:
        return self.ready_count > 0


def run_desktop_app(settings: AppSettings, *, startup_warning: str | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from mailflow.ui.single_instance import acquire_instance_lock
    except Exception as exc:
        msg = "PySide6 est requis pour lancer l'interface desktop"
        raise RuntimeError(msg) from exc

    app = QApplication([])
    instance_lock = acquire_instance_lock(settings.paths.data_dir)
    if instance_lock is None:
        QMessageBox.information(
            None,
            "MailFlow Archivist",
            "MailFlow est deja ouvert. Retrouvez-le dans la barre des taches "
            "ou dans la zone de notification.",
        )
        return 0
    try:
        window = MainWindow(settings)
        window.show()
        if startup_warning:
            QMessageBox.warning(window, "Reglages reinitialises", startup_warning)
        return int(app.exec())
    finally:
        instance_lock.unlock()


def MainWindow(settings: AppSettings, controller: Any | None = None) -> Any:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import (
        QAction,
        QBrush,
        QColor,
        QFont,
        QIcon,
        QKeySequence,
        QPainter,
        QPen,
        QPixmap,
    )
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QStackedWidget,
        QSystemTrayIcon,
        QTableWidget,
        QTableWidgetItem,
        QTableWidgetSelectionRange,
        QTabWidget,
        QTextEdit,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    from mailflow import __version__
    from mailflow.classifier.ai_classifier import AiClassifier
    from mailflow.classifier.ollama_classifier import OllamaClassifier
    from mailflow.config import (
        AI_MODEL_OPTIONS,
        DEFAULT_AI_MODEL,
        DEFAULT_OLLAMA_BASE_URL,
        DEFAULT_OLLAMA_MODEL,
        get_openai_api_key,
        save_settings,
        set_openai_api_key,
    )
    from mailflow.core.app_controller import (
        PreviewRequest,
        build_ai_classifier,
        build_default_controller,
    )
    from mailflow.core.manual_review import suggested_manual_destination
    from mailflow.core.project_digest import build_project_digest
    from mailflow.resources import app_icon_path
    from mailflow.ui.background_call import ResponsiveAiClassifier, run_with_event_loop
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
        interlocutor_option_label,
        interlocutor_type_from_option,
        preview_row_to_cells,
        should_highlight_cell,
    )
    from mailflow.ui.project_digest_preview import project_digest_to_html
    from mailflow.ui.theme import APP_STYLESHEET

    class MailFlowMainWindow(QMainWindow):
        def closeEvent(self, event: Any) -> None:
            handler = getattr(self, "mailflow_close_handler", None)
            if callable(handler):
                handler(event)
                return
            super().closeEvent(event)

    controller_was_injected = controller is not None
    active_controller = controller or build_default_controller(settings)

    def enable_responsive_ai() -> None:
        pipeline = getattr(active_controller, "preview_pipeline", None)
        if pipeline is None:
            return
        classifier = getattr(pipeline, "ai_classifier", None)
        if classifier is not None and not isinstance(classifier, ResponsiveAiClassifier):
            pipeline.ai_classifier = ResponsiveAiClassifier(classifier)

    def apply_current_ai_settings() -> None:
        pipeline = getattr(active_controller, "preview_pipeline", None)
        if pipeline is None:
            return
        # Update the existing pipeline so saving privacy choices also applies to
        # the watcher and reclassification, without discarding the current review.
        pipeline.ai_mode = settings.ai_mode
        pipeline.include_body_for_ai = settings.ai_include_body_excerpt
        pipeline.privacy_mask_phone_numbers = settings.privacy_mask_phone_numbers
        classifier = build_ai_classifier(settings)
        pipeline.ai_classifier = (
            ResponsiveAiClassifier(classifier) if classifier is not None else None
        )

    enable_responsive_ai()
    window = MailFlowMainWindow()
    dynamic_window = cast(Any, window)
    window.setWindowTitle(UI_TEXT["window_title"])
    dynamic_window.mailflow_force_quit = False
    combo_by_cell: dict[tuple[int, int], Any] = {}
    refreshing_table = False
    refreshing_outlook_options = False
    watch_paused_logged = False
    refreshing_directory_table = False
    operation_in_progress = False
    watch_state = WatchState()
    review_queue = ReviewQueue()
    sent_review_reminders: set[str] = set()
    central = QWidget()
    central.setObjectName("workspace")
    layout = QVBoxLayout(central)
    layout.setContentsMargins(18, 14, 18, 10)
    layout.setSpacing(12)
    window.setStyleSheet(APP_STYLESHEET)
    window.resize(1440, 900)
    window.setMinimumSize(1040, 720)

    top_bar = QWidget()
    top_layout = QHBoxLayout(top_bar)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(8)
    app_title = QLabel("MailFlow")
    app_title.setProperty("role", "title")
    workflow_label = QLabel("1. Scanner   →   2. Vérifier   →   3. Archiver")
    workflow_label.setProperty("role", "muted")
    account_combo = QComboBox()
    account_combo.setEditable(True)
    account_combo.setMinimumWidth(190)
    account_combo.setAccessibleName("Compte Outlook")
    outlook_root_combo = QComboBox()
    outlook_root_combo.setEditable(True)
    outlook_root_combo.setMinimumWidth(180)
    outlook_root_combo.setAccessibleName("Dossier source Outlook")
    year_input = QLineEdit(settings.selected_year or "")
    year_input.setPlaceholderText("Annee")
    year_input.setFixedWidth(82)
    project_input = QLineEdit("")
    project_input.setPlaceholderText("Projet")
    project_input.setFixedWidth(120)
    scan_button = QPushButton(UI_TEXT["scan_button"])
    scan_button.setProperty("role", "primary")
    scan_button.setToolTip("Analyser les dossiers sélectionnés (Ctrl+R)")
    reset_button = QPushButton(UI_TEXT["reset_workspace"])
    watch_checkbox = QCheckBox(UI_TEXT["watch_outlook"])
    scan_status_label = QLabel("")
    scan_status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    scan_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    scan_status_label.setStyleSheet("QLabel { color: #64748b; }")
    top_layout.addWidget(app_title)
    top_layout.addWidget(workflow_label)
    top_layout.addStretch(1)
    top_layout.addWidget(watch_checkbox)
    layout.addWidget(top_bar)

    scan_panel = QWidget()
    scan_panel.setObjectName("scanPanel")
    scan_layout = QGridLayout(scan_panel)
    scan_layout.setContentsMargins(14, 10, 14, 10)
    scan_layout.setHorizontalSpacing(12)
    for column, (label, field) in enumerate((
        ("Compte Outlook", account_combo),
        ("Dossier source", outlook_root_combo),
        ("Année", year_input),
        ("Projet (facultatif)", project_input),
    )):
        field_label = QLabel(label)
        field_label.setBuddy(field)
        field_label.setProperty("role", "muted")
        scan_layout.addWidget(field_label, 0, column)
        scan_layout.addWidget(field, 1, column)
    scan_layout.setColumnStretch(0, 2)
    scan_layout.setColumnStretch(1, 2)
    scan_layout.addWidget(scan_button, 1, 4)
    scan_layout.addWidget(reset_button, 1, 5)
    layout.addWidget(scan_panel)

    content_splitter = QSplitter(Qt.Orientation.Horizontal)
    content_splitter.setChildrenCollapsible(False)
    navigation = QListWidget()
    navigation.setObjectName("navigation")
    navigation.setAccessibleName("Navigation principale")
    navigation.addItems(["Mails", "Arborescence", "Annuaire", "Réglages"])
    navigation.setFixedWidth(154)
    navigation.setCurrentRow(0)
    content_splitter.addWidget(navigation)

    workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
    workspace_splitter.setChildrenCollapsible(False)
    pages = QStackedWidget()

    mail_page = QWidget()
    mail_layout = QVBoxLayout(mail_page)
    mail_layout.setContentsMargins(0, 0, 0, 0)
    mail_layout.setSpacing(10)
    mail_heading = QLabel("Votre espace de classement")
    mail_heading.setProperty("role", "heading")
    mail_layout.addWidget(mail_heading)
    summary_label = QLabel("Prêt pour votre première analyse")
    summary_label.setProperty("role", "summary")
    summary_label.setWordWrap(True)
    mail_layout.addWidget(summary_label)
    filters = QWidget()
    filter_layout = QHBoxLayout(filters)
    filter_layout.setContentsMargins(0, 0, 0, 0)
    search_input = QLineEdit()
    search_input.setPlaceholderText("Rechercher un sujet, expéditeur, projet…")
    search_input.setClearButtonEnabled(True)
    search_input.setAccessibleName("Rechercher dans les mails")
    search_input.setToolTip("Rechercher dans les mails affichés (Ctrl+F)")
    status_filter = QComboBox()
    status_filter.setAccessibleName("Filtrer les mails par état")
    for label, value in (
        ("Tous les états", "all"), ("À vérifier", "review"),
        ("Prêts à archiver", "ready"), ("Ignorés", "ignore"), ("Archivés", "archived"),
    ):
        status_filter.addItem(label, value)
    clear_filters_button = QPushButton("Effacer les filtres")
    filter_layout.addWidget(search_input, 1)
    filter_layout.addWidget(status_filter)
    filter_layout.addWidget(clear_filters_button)
    mail_layout.addWidget(filters)
    actions = QWidget()
    actions_layout = QHBoxLayout(actions)
    actions_layout.setContentsMargins(0, 0, 0, 0)
    archive_button = QToolButton()
    archive_button.setText(UI_TEXT["archive"])
    archive_button.setProperty("role", "primary")
    archive_button.setToolTip("Archiver les mails prêts de la sélection (Ctrl+Entrée)")
    archive_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    archive_menu = QMenu(archive_button)
    archive_selection_action = QAction(UI_TEXT["archive_selection"], window)
    archive_all_action = QAction(UI_TEXT["archive_all_except_review"], window)
    archive_menu.addAction(archive_selection_action)
    archive_menu.addAction(archive_all_action)
    archive_button.setMenu(archive_menu)
    export_html_button = QPushButton(UI_TEXT["export_project_html"])
    more_actions_button = QToolButton()
    more_actions_button.setText(UI_TEXT["more_actions"])
    more_actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    more_actions_menu = QMenu(more_actions_button)
    ignore_action = QAction(UI_TEXT["mark_ignored"], window)
    restore_archivable_action = QAction(UI_TEXT["restore_archivable"], window)
    reclassify_action = QAction(UI_TEXT["reclassify"], window)
    detail_columns_action = QAction("Afficher les colonnes détaillées", window)
    detail_columns_action.setCheckable(True)
    inspector_action = QAction("Afficher l'aperçu du mail", window)
    inspector_action.setCheckable(True)
    inspector_action.setChecked(True)
    background_action = QAction(UI_TEXT["background_mode"], window)
    open_folder_action = QAction(UI_TEXT["open_project_folder"], window)
    report_action = QAction(UI_TEXT["export_report"], window)
    more_actions_menu.addAction(ignore_action)
    more_actions_menu.addAction(restore_archivable_action)
    more_actions_menu.addAction(reclassify_action)
    more_actions_menu.addSeparator()
    more_actions_menu.addAction(detail_columns_action)
    more_actions_menu.addAction(inspector_action)
    more_actions_menu.addSeparator()
    more_actions_menu.addAction(background_action)
    more_actions_menu.addSeparator()
    more_actions_menu.addAction(open_folder_action)
    more_actions_menu.addAction(report_action)
    more_actions_button.setMenu(more_actions_menu)
    actions_layout.addWidget(archive_button)
    review_button = QPushButton("Vérifier la sélection")
    review_button.setToolTip("Modifier le classement du mail courant (Entrée)")
    actions_layout.addWidget(review_button)
    actions_layout.addWidget(export_html_button)
    actions_layout.addWidget(more_actions_button)
    actions_layout.addStretch(1)
    table = QTableWidget(0, len(PREVIEW_COLUMNS))
    table.setHorizontalHeaderLabels(list(PREVIEW_COLUMNS))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setMinimumHeight(220)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(38)
    table.setAccessibleName("Mails analysés et propositions de classement")
    table.horizontalHeader().setSectionsMovable(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    # Keep the information used to make a decision visible first on smaller displays.
    for visual_index, logical_index in enumerate((4, 9, 3, 7)):
        table.horizontalHeader().moveSection(
            table.horizontalHeader().visualIndex(logical_index), visual_index
        )
    for column in (0, 1, 2, TYPE_COLUMN, INTERLOCUTOR_COLUMN, 8):
        table.setColumnHidden(column, True)
    mail_layout.addWidget(actions)
    mail_results = QStackedWidget()
    mail_results.addWidget(table)
    empty_state = QWidget()
    empty_state.setObjectName("emptyState")
    empty_layout = QVBoxLayout(empty_state)
    empty_layout.setContentsMargins(30, 24, 30, 24)
    empty_layout.addStretch(1)
    empty_title = QLabel("Commencez par une analyse Outlook")
    empty_title.setProperty("role", "heading")
    empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_title.setWordWrap(True)
    empty_body = QLabel(
        "Choisissez votre compte et votre dossier source, puis cliquez sur Scanner Outlook.\n\n"
        "Les propositions apparaîtront ici pour être vérifiées avant archivage."
    )
    empty_body.setProperty("role", "muted")
    empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    empty_body.setWordWrap(True)
    empty_settings_button = QPushButton("Configurer les dossiers et l'IA")
    empty_layout.addWidget(empty_title)
    empty_layout.addSpacing(12)
    empty_layout.addWidget(empty_body)
    empty_layout.addSpacing(18)
    empty_layout.addWidget(empty_settings_button, 0, Qt.AlignmentFlag.AlignHCenter)
    empty_layout.addStretch(1)
    mail_results.addWidget(empty_state)
    mail_results.setCurrentIndex(1)
    mail_layout.addWidget(mail_results, 1)
    selection_status = QLabel("Aucun mail sélectionné")
    selection_status.setProperty("role", "muted")
    mail_layout.addWidget(selection_status)
    pages.addWidget(mail_page)

    tree_widget = QWidget()
    tree_layout = QVBoxLayout(tree_widget)
    tree_layout.setContentsMargins(0, 0, 0, 0)
    tree_layout.setSpacing(6)
    tree_heading = QLabel("Dossiers proposés")
    tree_heading.setProperty("role", "heading")
    tree_hint = QLabel("Organisez les destinations avant de lancer l'archivage.")
    tree_hint.setProperty("role", "muted")
    tree_layout.addWidget(tree_heading)
    tree_layout.addWidget(tree_hint)
    folder_tree = QTreeWidget()
    folder_tree.setHeaderLabels(["Dossier propose", "Mails"])
    folder_tree.setMinimumHeight(320)
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
    tree_layout.addWidget(tree_buttons)
    tree_layout.addWidget(folder_tree, 1)
    pages.addWidget(tree_widget)

    directory_page = QWidget()
    directory_layout = QVBoxLayout(directory_page)
    directory_layout.setContentsMargins(0, 0, 0, 0)
    directory_layout.setSpacing(8)
    directory_heading = QLabel("Votre annuaire d'entreprises")
    directory_heading.setProperty("role", "heading")
    directory_hint = QLabel("Les rôles enregistrés aident à proposer le bon classement.")
    directory_hint.setProperty("role", "muted")
    directory_layout.addWidget(directory_heading)
    directory_layout.addWidget(directory_hint)
    directory_actions = QWidget()
    directory_actions_layout = QHBoxLayout(directory_actions)
    directory_actions_layout.setContentsMargins(0, 0, 0, 0)
    import_directory_button = QPushButton(UI_TEXT["import_directory"])
    refresh_directory_button = QPushButton(UI_TEXT["refresh_directory"])
    add_directory_button = QPushButton(UI_TEXT["add_directory"])
    delete_directory_button = QPushButton(UI_TEXT["delete_directory"])
    rename_directory_button = QPushButton(UI_TEXT["rename_directory"])
    merge_directory_button = QPushButton(UI_TEXT["merge_directory"])
    directory_actions_layout.addWidget(import_directory_button)
    directory_actions_layout.addWidget(refresh_directory_button)
    directory_actions_layout.addWidget(add_directory_button)
    directory_actions_layout.addStretch(1)
    directory_edit_actions = QWidget()
    directory_edit_layout = QHBoxLayout(directory_edit_actions)
    directory_edit_layout.setContentsMargins(0, 0, 0, 0)
    directory_edit_layout.addWidget(rename_directory_button)
    directory_edit_layout.addWidget(merge_directory_button)
    directory_edit_layout.addWidget(delete_directory_button)
    directory_edit_layout.addStretch(1)
    directory_table = QTableWidget(0, 5)
    directory_table.setHorizontalHeaderLabels(
        ["Entreprise", "Domaines", "Contacts", "Role global", "Projets"]
    )
    directory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    directory_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    directory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    directory_table.setAlternatingRowColors(True)
    directory_table.verticalHeader().hide()
    directory_table.verticalHeader().setDefaultSectionSize(38)
    directory_table.horizontalHeader().setSectionsMovable(True)
    directory_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    directory_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    directory_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    directory_table.horizontalHeader().setSectionResizeMode(
        3,
        QHeaderView.ResizeMode.Interactive,
    )
    directory_table.horizontalHeader().setSectionResizeMode(
        4,
        QHeaderView.ResizeMode.ResizeToContents,
    )
    directory_status_label = QLabel("Annuaire local")
    directory_status_label.setWordWrap(True)
    directory_status_label.setStyleSheet("QLabel { color: #334155; }")
    directory_layout.addWidget(directory_actions)
    directory_layout.addWidget(directory_edit_actions)
    directory_layout.addWidget(directory_table, 1)
    directory_layout.addWidget(directory_status_label)
    pages.addWidget(directory_page)

    settings_page = QWidget()
    settings_layout = QVBoxLayout(settings_page)
    settings_layout.setContentsMargins(0, 0, 0, 0)
    settings_layout.setSpacing(8)
    settings_heading = QLabel("Réglages")
    settings_heading.setProperty("role", "heading")
    settings_layout.addWidget(settings_heading)
    settings_hint = QLabel(
        "Enregistrez pour appliquer les choix IA et confidentialité. "
        "Le dossier local sera utilisé au prochain scan."
    )
    settings_hint.setProperty("role", "muted")
    settings_hint.setWordWrap(True)
    settings_layout.addWidget(settings_hint)
    config = QGroupBox("Dossiers, intelligence artificielle et suivi")
    grid = QGridLayout(config)
    grid.setVerticalSpacing(14)
    grid.setColumnStretch(1, 1)
    grid.addWidget(QLabel("Dossier local des projets"), 0, 0)
    projects_root_input = QLineEdit(str(settings.local_projects_root))
    projects_root_picker = QWidget()
    projects_root_layout = QHBoxLayout(projects_root_picker)
    projects_root_layout.setContentsMargins(0, 0, 0, 0)
    projects_root_layout.addWidget(projects_root_input)
    browse_projects_button = QPushButton("Parcourir")
    projects_root_layout.addWidget(browse_projects_button)
    grid.addWidget(projects_root_picker, 0, 1)
    grid.addWidget(QLabel("Mode IA"), 1, 0)
    ai_mode_combo = QComboBox()
    for mode in (AiMode.DISABLED, AiMode.ALL):
        ai_mode_combo.addItem(ai_mode_label(mode), mode.value)
    selected_ai_mode = (
        AiMode.ALL if settings.ai_mode == AiMode.AMBIGUOUS_ONLY else settings.ai_mode
    )
    set_combo_value_by_data(ai_mode_combo, selected_ai_mode.value)
    grid.addWidget(ai_mode_combo, 1, 1)
    grid.addWidget(QLabel("Moteur IA"), 2, 0)
    ai_provider_combo = QComboBox()
    ai_provider_combo.addItem("OpenAI — API", "openai")
    ai_provider_combo.addItem("Ollama — IA locale sur ce PC", "ollama")
    set_combo_value_by_data(ai_provider_combo, settings.ai_provider)
    grid.addWidget(ai_provider_combo, 2, 1)
    ai_model_label = QLabel("Modèle OpenAI")
    grid.addWidget(ai_model_label, 3, 0)
    ai_model_input = QComboBox()
    ai_model_input.setEditable(True)
    ai_model_input.addItems(list(AI_MODEL_OPTIONS))
    set_combo_value_by_text(ai_model_input, settings.ai_model)
    grid.addWidget(ai_model_input, 3, 1)
    openai_key_label = QLabel("Clé API OpenAI")
    grid.addWidget(openai_key_label, 4, 0)
    openai_key_widget = QWidget()
    openai_key_layout = QHBoxLayout(openai_key_widget)
    openai_key_layout.setContentsMargins(0, 0, 0, 0)
    openai_key_input = QLineEdit()
    openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    openai_key_input.setPlaceholderText("Coller une nouvelle clé puis enregistrer")
    save_openai_key_button = QPushButton(UI_TEXT["save_openai_key"])
    test_openai_key_button = QPushButton(UI_TEXT["test_openai_key"])
    openai_key_status = QLabel()
    openai_key_layout.addWidget(openai_key_input)
    openai_key_layout.addWidget(save_openai_key_button)
    openai_key_layout.addWidget(test_openai_key_button)
    key_fields = QWidget()
    key_fields_layout = QVBoxLayout(key_fields)
    key_fields_layout.setContentsMargins(0, 0, 0, 0)
    key_fields_layout.addWidget(openai_key_widget)
    key_fields_layout.addWidget(openai_key_status)
    grid.addWidget(key_fields, 4, 1)
    ollama_model_label = QLabel("Modèle local installé")
    grid.addWidget(ollama_model_label, 5, 0)
    ollama_models_widget = QWidget()
    ollama_models_layout = QHBoxLayout(ollama_models_widget)
    ollama_models_layout.setContentsMargins(0, 0, 0, 0)
    ollama_model_input = QComboBox()
    ollama_model_input.setEditable(True)
    ollama_model_input.addItem(settings.ollama_model)
    ollama_models_layout.addWidget(ollama_model_input, 1)
    refresh_ollama_models_button = QPushButton("Actualiser les modèles")
    ollama_models_layout.addWidget(refresh_ollama_models_button)
    grid.addWidget(ollama_models_widget, 5, 1)
    ollama_url_label = QLabel("Adresse Ollama sur ce PC")
    grid.addWidget(ollama_url_label, 6, 0)
    ollama_base_url_input = QLineEdit(settings.ollama_base_url)
    ollama_base_url_input.setPlaceholderText(DEFAULT_OLLAMA_BASE_URL)
    ollama_address_widget = QWidget()
    ollama_address_layout = QHBoxLayout(ollama_address_widget)
    ollama_address_layout.setContentsMargins(0, 0, 0, 0)
    ollama_address_layout.addWidget(ollama_base_url_input, 1)
    ollama_address_layout.addWidget(QLabel("Délai maximal par mail"))
    ollama_timeout_input = QDoubleSpinBox()
    ollama_timeout_input.setRange(0.1, 3600.0)
    ollama_timeout_input.setDecimals(1)
    ollama_timeout_input.setSingleStep(30.0)
    ollama_timeout_input.setSuffix(" s")
    ollama_timeout_input.setValue(settings.ollama_timeout_seconds)
    ollama_address_layout.addWidget(ollama_timeout_input)
    grid.addWidget(ollama_address_widget, 6, 1)
    ollama_test_widget = QWidget()
    ollama_test_layout = QVBoxLayout(ollama_test_widget)
    ollama_test_layout.setContentsMargins(0, 0, 0, 0)
    test_ollama_button = QPushButton("Tester IA locale")
    test_ollama_button.setToolTip("Classer un mail fictif pour vérifier Ollama et le modèle.")
    ollama_test_layout.addWidget(test_ollama_button, 0, Qt.AlignmentFlag.AlignLeft)
    ollama_status = QLabel("Connexion locale à tester.")
    ollama_status.setWordWrap(True)
    ollama_test_layout.addWidget(ollama_status)
    grid.addWidget(ollama_test_widget, 7, 1)
    ai_provider_hint = QLabel()
    ai_provider_hint.setWordWrap(True)
    ai_provider_hint.setProperty("role", "muted")
    grid.addWidget(ai_provider_hint, 8, 1)
    ai_include_body_checkbox = QCheckBox("Inclure l'extrait nettoyé du corps dans l'analyse IA")
    ai_include_body_checkbox.setChecked(settings.ai_include_body_excerpt)
    grid.addWidget(ai_include_body_checkbox, 9, 1)
    privacy_phone_checkbox = QCheckBox("Masquer les numéros de téléphone avant l'analyse IA")
    privacy_phone_checkbox.setChecked(settings.privacy_mask_phone_numbers)
    grid.addWidget(privacy_phone_checkbox, 10, 1)
    grid.addWidget(QLabel("Horaires des rappels"), 11, 0)
    review_reminder_times_input = QLineEdit(format_reminder_times(settings.review_reminder_times))
    review_reminder_times_input.setPlaceholderText("09:00, 14:00, 16:30")
    grid.addWidget(review_reminder_times_input, 11, 1)
    grid.addWidget(QLabel("Mises à jour"), 12, 0)
    update_widget = QWidget()
    update_layout = QHBoxLayout(update_widget)
    update_layout.setContentsMargins(0, 0, 0, 0)
    check_updates_button = QPushButton(UI_TEXT["check_updates"])
    update_status = QLabel(f"Version {__version__}")
    update_status.setStyleSheet("QLabel { color: #334155; }")
    update_layout.addWidget(check_updates_button)
    update_layout.addWidget(update_status)
    update_layout.addStretch(1)
    grid.addWidget(update_widget, 12, 1)
    save_settings_button = QPushButton(UI_TEXT["save_settings"])
    save_settings_button.setProperty("role", "primary")
    grid.addWidget(save_settings_button, 13, 1)
    settings_layout.addWidget(config)
    settings_layout.addStretch(1)
    settings_scroll_area = QScrollArea()
    settings_scroll_area.setWidgetResizable(True)
    settings_scroll_area.setWidget(settings_page)
    pages.addWidget(settings_scroll_area)

    preview = QGroupBox("Comprendre le classement")
    preview_layout = QVBoxLayout(preview)
    preview_tabs = QTabWidget()
    project_digest_preview = QTextEdit()
    project_digest_preview.setReadOnly(True)
    project_digest_preview.setMinimumHeight(160)
    project_digest_preview.setHtml(project_digest_to_html(build_project_digest([])))
    mail_preview = QTextEdit()
    mail_preview.setReadOnly(True)
    mail_preview.setMinimumWidth(300)
    mail_preview.setPlaceholderText(
        "Sélectionnez un mail pour lire son contenu et comprendre le classement proposé.\n\n"
        "Double-cliquez sur une ligne pour corriger et mémoriser votre choix."
    )
    preview_tabs.addTab(mail_preview, "Mail sélectionné")
    preview_tabs.addTab(project_digest_preview, "Bilan du projet")
    preview_layout.addWidget(preview_tabs)

    logs = QTextEdit()
    logs.setReadOnly(True)
    logs.setPlaceholderText(UI_TEXT["logs"])
    logs.setVisible(False)
    logs_panel = QWidget()
    logs_layout = QVBoxLayout(logs_panel)
    logs_layout.setContentsMargins(0, 0, 0, 0)
    logs_layout.setSpacing(2)
    logs_header = QWidget()
    logs_header_layout = QHBoxLayout(logs_header)
    logs_header_layout.setContentsMargins(0, 0, 0, 0)
    logs_toggle = QToolButton()
    logs_toggle.setAutoRaise(True)
    logs_toggle.setToolTip("Afficher ou masquer le journal d'activité")
    logs_toggle.setArrowType(Qt.ArrowType.RightArrow)
    logs_label = QLabel("Journal d'activité")
    logs_label.setStyleSheet("font-weight: 600;")
    logs_header_layout.addWidget(logs_toggle)
    logs_header_layout.addWidget(logs_label)
    logs_header_layout.addStretch(1)
    logs_header_layout.addWidget(scan_status_label, 1)
    logs_layout.addWidget(logs_header)
    logs_layout.addWidget(logs)
    logs_panel.setMaximumHeight(32)

    def toggle_logs() -> None:
        logs.setVisible(not logs.isVisible())
        logs_toggle.setArrowType(
            Qt.ArrowType.DownArrow if logs.isVisible() else Qt.ArrowType.RightArrow
        )
        logs_panel.setMaximumHeight(190 if logs.isVisible() else 32)

    logs_toggle.clicked.connect(toggle_logs)

    workspace_splitter.addWidget(pages)
    workspace_splitter.addWidget(preview)
    workspace_splitter.setStretchFactor(0, 5)
    workspace_splitter.setStretchFactor(1, 2)
    workspace_splitter.setSizes([900, 360])
    content_splitter.addWidget(workspace_splitter)
    content_splitter.setStretchFactor(0, 0)
    content_splitter.setStretchFactor(1, 1)
    content_splitter.setSizes([150, 1250])
    layout.addWidget(content_splitter, 1)
    layout.addWidget(logs_panel)
    navigation.currentRowChanged.connect(pages.setCurrentIndex)
    navigation.currentRowChanged.connect(
        lambda index: preview.setVisible(index < 2 and inspector_action.isChecked())
    )
    inspector_action.toggled.connect(
        lambda checked: preview.setVisible(checked and navigation.currentRow() < 2)
    )
    def show_detail_columns(checked: bool) -> None:
        for column in (0, 1, 2, TYPE_COLUMN, INTERLOCUTOR_COLUMN, 8):
            table.setColumnHidden(column, not checked)

    detail_columns_action.toggled.connect(show_detail_columns)
    empty_settings_button.clicked.connect(lambda: navigation.setCurrentRow(3))
    window.setCentralWidget(central)
    watch_timer = QTimer(window)
    watch_timer.setInterval(WATCH_INTERVAL_MS)
    review_reminder_timer = QTimer(window)
    review_reminder_timer.setInterval(REMINDER_CHECK_INTERVAL_MS)
    app_icon = QIcon(str(app_icon_path()))

    def tray_status_icon(watch_enabled: bool, review_count: int) -> Any:
        pixmap = app_icon.pixmap(64, 64)
        if pixmap.isNull():
            pixmap = QPixmap(64, 64)
            pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#ffffff"), 5))
        painter.setBrush(QBrush(QColor("#16a34a" if watch_enabled else "#94a3b8")))
        painter.drawEllipse(39, 39, 20, 20)
        if review_count > 0:
            badge_text = "99+" if review_count > 99 else str(review_count)
            painter.setPen(QPen(QColor("#ffffff"), 3))
            painter.setBrush(QBrush(QColor("#dc2626")))
            painter.drawEllipse(2, 2, 28, 28)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            font = QFont()
            font.setBold(True)
            font.setPointSize(8 if review_count <= 99 else 7)
            painter.setFont(font)
            painter.drawText(2, 2, 28, 28, Qt.AlignmentFlag.AlignCenter, badge_text)
        painter.end()
        return QIcon(pixmap)

    window.setWindowIcon(app_icon)
    tray_icon = QSystemTrayIcon(tray_status_icon(False, 0), window)
    tray_icon.setToolTip(tray_tooltip_text(False, 0))
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

    def set_scan_status(message: str, *, success: bool | None = None) -> None:
        if success is True:
            color = "#166534"
        elif success is False:
            color = "#9f1239"
        else:
            color = "#475569"
        scan_status_label.setText(message)
        scan_status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")

    def expand_logs() -> None:
        if not logs.isVisible():
            toggle_logs()

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

    def update_ai_provider_fields() -> None:
        local = ai_provider_combo.currentData() == "ollama"
        for widget in (ai_model_label, ai_model_input, openai_key_label, key_fields):
            widget.setVisible(not local)
            widget.setEnabled(not local)
        for widget in (
            ollama_model_label, ollama_models_widget, ollama_url_label,
            ollama_address_widget, ollama_test_widget,
        ):
            widget.setVisible(local)
            widget.setEnabled(local)
        ai_provider_hint.setText(
            "Les mails sont analysés sur ce PC. Aucune clé API requise ; aucun envoi à OpenAI. "
            "Ollama doit être démarré et le modèle installé."
            if local else
            "Les informations utilisées pour le classement sont envoyées à l'API OpenAI."
        )
        if not local:
            update_openai_key_status()

    update_ai_provider_fields()

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
        update_tray_indicator()

    def update_tray_indicator() -> None:
        tray_icon.setIcon(tray_status_icon(watch_checkbox.isChecked(), review_queue.count))
        tray_icon.setToolTip(tray_tooltip_text(watch_checkbox.isChecked(), review_queue.count))

    def sync_review_queue_from_preview() -> int:
        new_pending = review_queue.sync(active_controller.preview_rows)
        update_tray_indicator()
        return new_pending

    def request_watch_from_tray(enabled: bool) -> None:
        if watch_checkbox.isChecked() != enabled:
            watch_checkbox.setChecked(enabled)

    def hide_to_background() -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            append_log("Mode arriere-plan indisponible: zone de notification introuvable.")
            return
        if not tray_icon.isVisible():
            tray_icon.show()
        window.hide()
        notify_user(
            "MailFlow en arriere-plan",
            tray_tooltip_text(watch_checkbox.isChecked(), review_queue.count),
        )
        status = (
            UI_TEXT["tray_watch_active"]
            if watch_checkbox.isChecked()
            else UI_TEXT["tray_watch_inactive"]
        )
        append_log(
            "Mode arriere-plan actif: "
            f"{status}."
        )

    def quit_application() -> None:
        if operation_in_progress:
            return
        dynamic_window.mailflow_force_quit = True
        watch_timer.stop()
        review_reminder_timer.stop()
        tray_icon.hide()
        QApplication.quit()

    def handle_window_close(event: Any) -> None:
        if operation_in_progress:
            event.ignore()
            set_scan_status("Opération en cours : patientez avant de fermer MailFlow.")
            return
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

    def table_entry_id(row_index: int) -> str | None:
        if row_index < 0:
            return None
        item = table.item(row_index, 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def update_selection_actions() -> None:
        selected = selected_table_row_indexes()
        ready_selected = summarize_archive_selection(active_controller.preview_rows, selected)
        ready_count = len(rows_to_archive(active_controller.preview_rows))
        archive_button.setEnabled(ready_count > 0 and not operation_in_progress)
        archive_selection_action.setEnabled(
            ready_selected.can_archive and not operation_in_progress
        )
        archive_all_action.setEnabled(ready_count > 0 and not operation_in_progress)
        editable_selection = (
            len(selected) == 1
            and active_controller.preview_rows[selected[0]].action != PreviewAction.ARCHIVED
        )
        review_button.setEnabled(editable_selection and not operation_in_progress)
        ignore_action.setEnabled(bool(selected) and not operation_in_progress)
        has_rows = bool(active_controller.preview_rows) and not operation_in_progress
        reclassify_action.setEnabled(has_rows)
        export_html_button.setEnabled(has_rows)
        selection_status.setText(
            f"{len(selected)} sélectionné(s) · {ready_selected.ready_count} prêt(s) à archiver"
            if selected else "Sélectionnez un mail pour le vérifier · Ctrl+F pour rechercher"
        )

    def apply_mail_filters() -> None:
        if refreshing_table:
            return
        terms = _normalize_choice(search_input.text()).split()
        selected_status = status_filter.currentData()
        visible_count = 0
        for row_index, row in enumerate(active_controller.preview_rows):
            search_text = _normalize_choice(" ".join(preview_row_to_cells(row)))
            searchable = f"{search_text} {_normalize_choice(row.mail.sender_email)}"
            status_matches = (
                selected_status == "all"
                or (selected_status == "ready" and bool(rows_to_archive([row])))
                or (selected_status == "review" and row.action == PreviewAction.REVIEW)
                or (selected_status == "ignore" and row.action == PreviewAction.IGNORE)
                or (selected_status == "archived" and row.action == PreviewAction.ARCHIVED)
            )
            visible = status_matches and all(term in searchable for term in terms)
            table.setRowHidden(row_index, not visible)
            if visible:
                visible_count += 1
            else:
                # Hidden selections must never be passed to archive or ignore actions.
                table.setRangeSelected(
                    QTableWidgetSelectionRange(row_index, 0, row_index, table.columnCount() - 1),
                    False,
                )
        if table.currentRow() >= 0 and table.isRowHidden(table.currentRow()):
            table.setCurrentCell(-1, -1)
        total = len(active_controller.preview_rows)
        ready = len(rows_to_archive(active_controller.preview_rows))
        review = sum(row.action == PreviewAction.REVIEW for row in active_controller.preview_rows)
        archived = sum(
            row.action == PreviewAction.ARCHIVED for row in active_controller.preview_rows
        )
        summary_label.setText(
            f"{visible_count} / {total} mails · {ready} prêts · "
            f"{review} à vérifier · {archived} archivés"
            if total else "Prêt pour votre première analyse"
        )
        mail_results.setCurrentIndex(0 if visible_count else 1)
        if total:
            empty_title.setText("Aucun mail ne correspond à vos filtres")
            empty_body.setText(
                "Modifiez votre recherche ou effacez les filtres pour retrouver vos mails."
            )
        else:
            empty_title.setText("Commencez par une analyse Outlook")
            empty_body.setText(
                "Choisissez votre compte et votre dossier source, "
                "puis cliquez sur Scanner Outlook.\n\n"
                "Les propositions apparaîtront ici pour être vérifiées avant archivage."
            )
        empty_settings_button.setVisible(not total)
        clear_filters_button.setEnabled(bool(terms) or selected_status != "all")
        update_selection_actions()
        update_preview_from_selection()

    def clear_mail_filters() -> None:
        search_input.clear()
        status_filter.setCurrentIndex(0)

    def set_operation_busy(busy: bool) -> None:
        nonlocal operation_in_progress
        operation_in_progress = busy
        scan_panel.setEnabled(not busy)
        pages.setEnabled(not busy)
        watch_checkbox.setEnabled(not busy)
        tray_watch_action.setEnabled(not busy)
        tray_quit_action.setEnabled(not busy)
        for action in (
            restore_archivable_action, report_action, open_folder_action, background_action,
        ):
            action.setEnabled(not busy)
        for shortcut_action in shortcut_actions:
            shortcut_action.setEnabled(not busy)
        update_selection_actions()

    def exclusive_operation(callback: Callable[[], None]) -> Callable[[], None]:
        def guarded() -> None:
            if operation_in_progress:
                return
            set_operation_busy(True)
            try:
                callback()
            finally:
                set_operation_busy(False)
        return guarded

    def refresh_table(*, preferred_row_index: int | None = None) -> None:
        nonlocal refreshing_table
        selected_entry_ids = {
            entry_id
            for index in table.selectionModel().selectedRows()
            if (entry_id := table_entry_id(index.row())) is not None
        }
        current_entry_id = table_entry_id(table.currentRow())
        current_column = max(table.currentColumn(), 0)
        vertical_scroll = table.verticalScrollBar().value()
        horizontal_scroll = table.horizontalScrollBar().value()
        preferred_entry_id = (
            active_controller.preview_rows[preferred_row_index].mail.entry_id
            if preferred_row_index is not None
            and 0 <= preferred_row_index < len(active_controller.preview_rows)
            else None
        )
        if preferred_entry_id is not None:
            selected_entry_ids = {preferred_entry_id}
        refreshing_table = True
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
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
                    if column_index == 0:
                        item.setData(Qt.ItemDataRole.UserRole, row.mail.entry_id)
                    if should_highlight_cell(row, column_index):
                        item.setBackground(QColor("#fff3d6"))
                    if column_index == 9:
                        foreground, background = {
                            PreviewAction.ARCHIVE: ("#1c6546", "#e8f4ed"),
                            PreviewAction.REVIEW: ("#865500", "#fff3d6"),
                            PreviewAction.IGNORE: ("#596779", "#edf1f5"),
                            PreviewAction.ARCHIVED: ("#245c8a", "#eaf1fa"),
                        }[row.action]
                        item.setForeground(QColor(foreground))
                        item.setBackground(QColor(background))
                    item.setToolTip(value)
                    table.setItem(row_index, column_index, item)
                    continue
                combo = QComboBox()
                combo.setEnabled(row.action != PreviewAction.ARCHIVED)
                combo.addItems(list(options))
                if value and value not in options:
                    combo.addItem(value)
                if value in options:
                    combo.setCurrentText(value)
                elif value:
                    combo.setCurrentText(value)
                if should_highlight_cell(row, column_index):
                    combo.setStyleSheet("QComboBox { background-color: #fff3d6; }")
                combo.currentTextChanged.connect(
                    lambda _text, row=row_index: open_manual_dialog(row)
                )
                table.setCellWidget(row_index, column_index, combo)
                combo_by_cell[(row_index, column_index)] = combo
        # Keep the user's column widths between edits; long subjects stay in tooltips.
        if not dynamic_window.property("mailflow_columns_initialized"):
            for column_index, width in enumerate((110, 140, 80, 155, 270, 155, 145, 235, 95, 110)):
                table.setColumnWidth(column_index, width)
            dynamic_window.setProperty("mailflow_columns_initialized", True)
        row_by_entry_id = {
            row.mail.entry_id: row_index
            for row_index, row in enumerate(active_controller.preview_rows)
        }
        target_entry_id = preferred_entry_id or current_entry_id
        target_row = (
            row_by_entry_id.get(target_entry_id)
            if target_entry_id is not None
            else None
        )
        table.clearSelection()
        restored_selection = False
        for entry_id in selected_entry_ids:
            selected_row = row_by_entry_id.get(entry_id)
            if selected_row is None:
                continue
            table.setRangeSelected(
                QTableWidgetSelectionRange(
                    selected_row,
                    0,
                    selected_row,
                    len(PREVIEW_COLUMNS) - 1,
                ),
                True,
            )
            restored_selection = True
        if target_row is not None and not restored_selection:
            table.selectRow(target_row)
        if target_row is not None:
            table.setCurrentCell(
                target_row,
                min(current_column, len(PREVIEW_COLUMNS) - 1),
            )
        refreshing_table = False
        apply_mail_filters()
        table.verticalScrollBar().setValue(vertical_scroll)
        table.horizontalScrollBar().setValue(horizontal_scroll)
        table.blockSignals(False)
        table.setUpdatesEnabled(True)
        refresh_folder_tree()
        refresh_project_digest()
        sync_review_queue_from_preview()
        update_mail_preview(table.currentRow())

    def refresh_project_digest() -> None:
        project_digest_preview.setHtml(
            project_digest_to_html(build_project_digest(active_controller.preview_rows))
        )

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
        if (
            row_index < 0 or row_index >= len(active_controller.preview_rows)
            or table.isRowHidden(row_index)
        ):
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

    def refresh_directory_table() -> None:
        nonlocal refreshing_directory_table
        refreshing_directory_table = True
        try:
            entries = active_controller.directory_entries()
        except Exception as exc:
            refreshing_directory_table = False
            directory_table.setRowCount(0)
            directory_status_label.setText(f"Annuaire indisponible: {exc}")
            return
        directory_table.clearContents()
        for row_index in range(directory_table.rowCount()):
            directory_table.removeCellWidget(row_index, 3)
        directory_table.setRowCount(len(entries))
        directory_table.setHorizontalHeaderLabels(
            ["Entreprise", "Domaines", "Contacts", "Role global", "Projets"]
        )
        for row_index, entry in enumerate(entries):
            organization_id = int(entry.organization_id)
            name = str(entry.name)
            domains = tuple(str(item) for item in entry.domains)
            contacts = tuple(str(item) for item in entry.contacts)
            project_count = int(getattr(entry, "project_count", getattr(entry, "mail_count", 0)))
            role = getattr(entry, "default_role", InterlocutorType.INCONNU)

            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, organization_id)
            domain_item = QTableWidgetItem(format_directory_values(domains, limit=8))
            contact_item = QTableWidgetItem(format_directory_values(contacts, limit=6))
            project_item = QTableWidgetItem(str(project_count))
            domain_item.setToolTip("\n".join(domains))
            contact_item.setToolTip("\n".join(contacts))
            directory_table.setItem(row_index, 0, name_item)
            directory_table.setItem(row_index, 1, domain_item)
            directory_table.setItem(row_index, 2, contact_item)
            directory_table.setCellWidget(
                row_index,
                3,
                global_role_combo(organization_id, role),
            )
            directory_table.setItem(row_index, 4, project_item)
        directory_table.resizeRowsToContents()
        refreshing_directory_table = False
        directory_status_label.setText(
            f"{len(entries)} entreprise(s) dans l'annuaire global."
        )

    def global_role_combo(organization_id: int, role: Any) -> Any:
        combo = QComboBox()
        for item in InterlocutorType:
            combo.addItem(interlocutor_label(item), item.value)
        current_role = role if isinstance(role, InterlocutorType) else InterlocutorType.INCONNU
        set_combo_value_by_data(combo, current_role.value)

        def role_changed(_value: str = "") -> None:
            if refreshing_directory_table:
                return
            selected_role = InterlocutorType(str(combo.currentData()))
            try:
                active_controller.set_directory_organization_role(
                    organization_id,
                    selected_role,
                )
                refresh_table()
                refresh_directory_table()
                update_mail_preview(table.currentRow())
                append_log(
                    "Role global applique: "
                    f"{selected_role.value} pour l'entreprise #{organization_id}."
                )
            except Exception as exc:
                append_log(f"Erreur role global: {exc}")
                refresh_directory_table()

        combo.currentTextChanged.connect(role_changed)
        return combo

    def selected_directory_organization() -> tuple[int, str] | None:
        selection_model = directory_table.selectionModel()
        selected_rows = (
            selection_model.selectedRows()
            if selection_model is not None
            else []
        )
        row_index = selected_rows[0].row() if selected_rows else directory_table.currentRow()
        if row_index < 0:
            return None
        item = directory_table.item(row_index, 0)
        if item is None:
            return None
        organization_id = int(item.data(Qt.ItemDataRole.UserRole))
        return organization_id, item.text()

    def add_directory_organization() -> None:
        dialog = QDialog(window)
        dialog.setWindowTitle("Ajouter une entreprise")
        form = QFormLayout(dialog)
        name_input = QLineEdit()
        domain_input = QLineEdit()
        domain_input.setPlaceholderText("exemple.ch")
        role_combo = QComboBox()
        for item in InterlocutorType:
            role_combo.addItem(interlocutor_label(item), item.value)
        set_combo_value_by_data(role_combo, InterlocutorType.INCONNU.value)
        form.addRow("Entreprise", name_input)
        form.addRow("Domaine e-mail", domain_input)
        form.addRow("Role global", role_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        name_input.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        role = InterlocutorType(str(role_combo.currentData()))
        try:
            organization_id = active_controller.add_directory_organization(
                name_input.text(),
                domain=clean_optional_text(domain_input.text()),
                role=role,
            )
            refresh_directory_table()
            append_log(
                f"Entreprise ajoutee a l'annuaire: {name_input.text().strip()} "
                f"(#{organization_id})."
            )
        except Exception as exc:
            append_log(f"Erreur ajout entreprise: {exc}")
            QMessageBox.warning(window, "Ajout impossible", str(exc))

    def delete_selected_directory_organization() -> None:
        selected = selected_directory_organization()
        if selected is None:
            append_log("Selectionner une entreprise dans l'annuaire.")
            return
        organization_id, name = selected
        entry = next(
            (
                item
                for item in active_controller.directory_entries()
                if int(item.organization_id) == organization_id
            ),
            None,
        )
        if entry is None:
            append_log("Entreprise introuvable dans l'annuaire.")
            refresh_directory_table()
            return
        confirmation = QMessageBox.question(
            window,
            "Supprimer l'entreprise",
            (
                f"Supprimer {name} de l'annuaire ?\n\n"
                f"{len(entry.domains)} domaine(s), {len(entry.contacts)} contact(s) "
                "et son role global seront retires.\n"
                "Aucun e-mail ni fichier archive ne sera supprime."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            active_controller.delete_directory_organization(organization_id)
            refresh_directory_table()
            append_log(f"Entreprise supprimee de l'annuaire: {name}.")
        except Exception as exc:
            append_log(f"Erreur suppression entreprise: {exc}")
            QMessageBox.warning(window, "Suppression impossible", str(exc))

    def rename_selected_directory_organization() -> None:
        selected = selected_directory_organization()
        if selected is None:
            append_log("Selectionner une entreprise dans l'annuaire.")
            return
        organization_id, current_name = selected
        new_name, accepted = QInputDialog.getText(
            window,
            "Renommer entreprise",
            "Nouveau nom de l'entreprise",
            text=current_name,
        )
        if not accepted:
            return
        try:
            active_controller.rename_directory_organization(organization_id, new_name)
            refresh_directory_table()
            append_log(f"Entreprise renommee: {current_name} -> {new_name.strip()}.")
        except Exception as exc:
            append_log(f"Erreur renommage entreprise: {exc}")

    def merge_selected_directory_organization() -> None:
        selected = selected_directory_organization()
        if selected is None:
            append_log("Selectionner une entreprise dans l'annuaire.")
            return
        source_id, source_name = selected
        try:
            entries = active_controller.directory_entries()
        except Exception as exc:
            append_log(f"Annuaire indisponible: {exc}")
            return
        options_by_label = {
            format_directory_entry_label(entry): int(entry.organization_id)
            for entry in entries
            if int(entry.organization_id) != source_id
        }
        if not options_by_label:
            append_log("Aucune autre entreprise disponible pour la fusion.")
            return
        target_label, accepted = QInputDialog.getItem(
            window,
            "Fusionner entreprise",
            f"Fusionner {source_name} vers",
            list(options_by_label),
            editable=False,
        )
        if not accepted:
            return
        target_id = options_by_label[str(target_label)]
        try:
            active_controller.merge_directory_organizations(source_id, target_id)
            refresh_directory_table()
            append_log(f"Entreprise fusionnee: {source_name} -> {target_label}.")
        except Exception as exc:
            append_log(f"Erreur fusion entreprise: {exc}")

    def log_scan_directory_update() -> bool:
        """Log what the last scan added to the directory; True if it changed."""
        update = getattr(active_controller, "last_scan_directory_update", None)
        if update is None:
            return False
        append_log(
            f"Annuaire mis a jour : {update.contact_count} contact(s) vus, "
            f"{update.new_organizations} nouvelle(s) entreprise(s), "
            f"{update.new_contacts} nouveau(x) contact(s)."
        )
        return bool(update.new_organizations or update.new_contacts)

    def open_manual_dialog(row_index: int) -> None:
        if refreshing_table or operation_in_progress:
            return
        if not 0 <= row_index < len(active_controller.preview_rows):
            return
        if active_controller.preview_rows[row_index].action == PreviewAction.ARCHIVED:
            set_scan_status("Ce mail est déjà archivé. Son classement est conservé.")
            return
        update = ask_manual_classification(row_index)
        if update is None:
            refresh_table(preferred_row_index=row_index)
            return
        try:
            updated = active_controller.apply_manual_update(row_index, update)
            refresh_table(preferred_row_index=row_index)
            append_log(
                "Classement manuel enregistre pour "
                f"{updated.mail.project_number}: "
                f"{updated.decision.mail_type.value} -> "
                f"{updated.decision.target_relative_folder}."
            )
            role_change = getattr(active_controller, "last_directory_role_change", None)
            if role_change is not None:
                refresh_directory_table()
                message = (
                    f"Annuaire : {role_change.organization_name} enregistre comme "
                    f"{role_change.role.value}. {role_change.updated_row_count} autre(s) "
                    "mail(s) de cette entreprise mis a jour."
                )
                append_log(message)
                set_scan_status(message, success=True)
        except Exception as exc:
            refresh_table(preferred_row_index=row_index)
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
        mail_type_combo.setPlaceholderText("Choisir le classement")
        initial_mail_type = (
            combo_text(row_index, TYPE_COLUMN) or preview_row_to_cells(row)[TYPE_COLUMN]
        )
        if initial_mail_type in MAIL_TYPE_OPTIONS:
            mail_type_combo.setCurrentText(initial_mail_type)
        else:
            mail_type_combo.setCurrentIndex(-1)
        interlocutor_combo = QComboBox()
        interlocutor_combo.addItems(list(INTERLOCUTOR_OPTIONS))
        set_combo_value(
            interlocutor_combo,
            combo_text(row_index, INTERLOCUTOR_COLUMN)
            or interlocutor_option_label(row.decision.interlocutor),
            INTERLOCUTOR_OPTIONS,
        )
        destination_combo = QComboBox()
        destination_combo.setEditable(False)
        destination_combo.addItems(list(DESTINATION_OPTIONS))
        initial_destination = (
            combo_text(row_index, DESTINATION_COLUMN) or row.decision.target_relative_folder
        )
        set_combo_value(
            destination_combo,
            initial_destination,
            DESTINATION_OPTIONS,
        )
        auto_destination = True
        routing_warning = QLabel("")
        routing_warning.setWordWrap(True)
        routing_warning.setStyleSheet("QLabel { color: #9a6700; font-weight: 600; }")

        def sync_suggested_destination(_value: str = "") -> None:
            if not auto_destination:
                return
            try:
                selected_role = interlocutor_type_from_option(
                    interlocutor_combo.currentText()
                )
                type_by_label = {
                    RoutingCategory.CORRESPONDANCE.value: MailType.CORRESPONDANCE_GENERALE,
                    RoutingCategory.DEMANDE_DE_PRIX.value: MailType.DEMANDE_DE_PRIX,
                    RoutingCategory.COMMANDE.value: MailType.COMMANDE,
                }
                selected_type = type_by_label.get(
                    mail_type_combo.currentText(),
                    MailType.A_VERIFIER,
                )
            except ValueError:
                return
            if selected_role == InterlocutorType.CLIENT:
                selected_type = MailType.CORRESPONDANCE_GENERALE
                if mail_type_combo.currentText() != RoutingCategory.CORRESPONDANCE.value:
                    mail_type_combo.blockSignals(True)
                    mail_type_combo.setCurrentText(RoutingCategory.CORRESPONDANCE.value)
                    mail_type_combo.blockSignals(False)
                routing_warning.clear()
            elif (
                selected_role == InterlocutorType.FOURNISSEUR
                and selected_type == MailType.CORRESPONDANCE_GENERALE
            ):
                selected_type = MailType.A_VERIFIER
                mail_type_combo.blockSignals(True)
                mail_type_combo.setCurrentIndex(-1)
                mail_type_combo.blockSignals(False)
                routing_warning.setText(
                    "Role fournisseur enregistre. Choisissez Demande de prix ou "
                    "Commande, ou utilisez ensuite Reclasser avec l'IA."
                )
            elif (
                selected_role == InterlocutorType.FOURNISSEUR
                and selected_type == MailType.A_VERIFIER
            ):
                routing_warning.setText(
                    "Role fournisseur enregistre. Le classement restera A verifier "
                    "jusqu'au choix Demande de prix ou Commande."
                )
            else:
                routing_warning.clear()
            suggested = suggested_manual_destination(selected_type, selected_role)
            destination_combo.blockSignals(True)
            set_combo_value(destination_combo, suggested, DESTINATION_OPTIONS)
            destination_combo.blockSignals(False)

        mail_type_combo.currentTextChanged.connect(sync_suggested_destination)
        interlocutor_combo.currentTextChanged.connect(sync_suggested_destination)
        sync_suggested_destination()
        form.addRow("Classement", mail_type_combo)
        form.addRow("Role de l'entreprise", interlocutor_combo)
        form.addRow("Destination finale", destination_combo)
        form.addRow("", routing_warning)

        learning_note = QLabel(
            "La correction s'applique à ce mail. Un classement complet devient un exemple "
            "vérifié pour les prochaines analyses. Pour définir le rôle d'une entreprise "
            "sur tous ses mails, utilisez l'Annuaire."
        )
        learning_note.setWordWrap(True)
        form.addRow("Apprentissage", learning_note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return build_manual_classification_update(
            mail_type_value=mail_type_combo.currentText(),
            interlocutor_value=interlocutor_combo.currentText(),
            destination_value=destination_combo.currentText(),
        )

    def set_combo_value(combo: Any, value: str, options: tuple[str, ...]) -> None:
        if value in options:
            combo.setCurrentText(value)
            return
        if value:
            combo.addItem(value)
            combo.setCurrentText(value)

    def update_projects_root() -> None:
        reminder_times = parse_reminder_times(review_reminder_times_input.text())
        values = {
            "local_projects_root": Path(projects_root_input.text()),
            "selected_outlook_account": selected_account_identifier(),
            "outlook_root_folder": current_outlook_root_folder(),
            "selected_year": clean_optional_text(year_input.text()),
            "ai_mode": AiMode(str(ai_mode_combo.currentData())),
            "ai_provider": str(ai_provider_combo.currentData()),
            "ai_model": clean_optional_text(ai_model_input.currentText()) or DEFAULT_AI_MODEL,
            "ollama_model": (
                clean_optional_text(ollama_model_input.currentText()) or DEFAULT_OLLAMA_MODEL
            ),
            "ollama_base_url": (
                clean_optional_text(ollama_base_url_input.text()) or DEFAULT_OLLAMA_BASE_URL
            ),
            "ollama_timeout_seconds": ollama_timeout_input.value(),
            "ai_include_body_excerpt": ai_include_body_checkbox.isChecked(),
            "privacy_mask_phone_numbers": privacy_phone_checkbox.isChecked(),
            "review_reminder_times": reminder_times,
        }
        validated = type(settings).model_validate(settings.model_dump() | values)
        for field in values:
            setattr(settings, field, getattr(validated, field))

    def save_current_settings() -> None:
        if operation_in_progress:
            return
        try:
            update_projects_root()
            apply_current_ai_settings()
            save_settings(settings)
            set_scan_status("Réglages enregistrés. Les choix IA et confidentialité sont appliqués.")
            append_log("Réglages enregistrés et paramètres IA appliqués à la session.")
        except Exception as exc:
            set_scan_status("Réglages non enregistrés : vérifiez les champs et le journal.",
                            success=False)
            append_log(f"Erreur enregistrement parametres: {exc}")

    def save_openai_key_from_input() -> None:
        if operation_in_progress or ai_provider_combo.currentData() != "openai":
            return
        api_key = clean_optional_text(openai_key_input.text())
        if api_key is None:
            append_log("Aucune nouvelle cle OpenAI a enregistrer.")
            return
        try:
            set_openai_api_key(api_key)
            apply_current_ai_settings()
            openai_key_input.clear()
            update_openai_key_status(valid=None)
            append_log("Cle OpenAI enregistree dans le coffre du systeme.")
        except Exception as exc:
            append_log(f"Erreur enregistrement cle OpenAI: {exc}")

    @exclusive_operation
    def test_openai_key_from_input() -> None:
        if ai_provider_combo.currentData() != "openai":
            return
        api_key = clean_optional_text(openai_key_input.text()) or get_openai_api_key()
        if api_key is None:
            set_openai_key_status(has_key=False, valid=False)
            append_log("Aucune cle OpenAI a tester.")
            return
        model = clean_optional_text(ai_model_input.currentText()) or DEFAULT_AI_MODEL
        set_openai_key_status(has_key=True, testing=True)
        test_openai_key_button.setEnabled(False)
        QApplication.processEvents()
        try:
            result = run_with_event_loop(
                AiClassifier(api_key=api_key, model=model).check_connection
            )
        except Exception as exc:
            set_openai_key_status(has_key=True, valid=False)
            append_log(f"Test OpenAI impossible : {exc}")
            return
        finally:
            test_openai_key_button.setEnabled(True)
        set_openai_key_status(has_key=True, valid=result.ok)
        append_log(f"Test OpenAI: {result.message}")

    def ollama_classifier_from_input() -> OllamaClassifier:
        return OllamaClassifier(
            base_url=clean_optional_text(ollama_base_url_input.text()) or DEFAULT_OLLAMA_BASE_URL,
            model=clean_optional_text(ollama_model_input.currentText()) or DEFAULT_OLLAMA_MODEL,
            timeout_seconds=ollama_timeout_input.value(),
        )

    def set_ollama_status(message: str, *, success: bool | None = None) -> None:
        color = "#166534" if success is True else "#9f1239" if success is False else "#334155"
        ollama_status.setText(message)
        ollama_status.setStyleSheet(f"QLabel {{ color: {color}; }}")

    def reset_ollama_status() -> None:
        set_ollama_status("Connexion locale à tester.")

    @exclusive_operation
    def test_ollama_from_input() -> None:
        if ai_provider_combo.currentData() != "ollama":
            return
        set_ollama_status("Test local en cours sur un mail fictif… Le modèle peut prendre un "
                          "moment à démarrer.")
        try:
            result = run_with_event_loop(ollama_classifier_from_input().check_connection)
        except Exception as exc:
            set_ollama_status(f"Test local impossible : {exc}", success=False)
            append_log(f"Test Ollama impossible : {exc}")
            return
        set_ollama_status(result.message, success=result.ok)
        append_log(f"Test Ollama : {result.message}")

    @exclusive_operation
    def refresh_ollama_models() -> None:
        if ai_provider_combo.currentData() != "ollama":
            return
        selected_model = ollama_model_input.currentText().strip() or DEFAULT_OLLAMA_MODEL
        set_ollama_status("Recherche des modèles installés sur ce PC…")
        try:
            models = run_with_event_loop(ollama_classifier_from_input().list_models)
        except Exception as exc:
            set_ollama_status(f"Ollama indisponible : {exc}", success=False)
            append_log(f"Actualisation des modèles Ollama impossible : {exc}")
            return
        ollama_model_input.clear()
        ollama_model_input.addItems(models)
        ollama_model_input.setCurrentText(selected_model)
        if not models:
            set_ollama_status("Ollama répond, mais aucun modèle n'est installé. "
                              "Installez un modèle dans Ollama, puis actualisez.", success=False)
        elif selected_model not in models:
            set_ollama_status(f"{len(models)} modèle(s) installé(s). "
                              "Choisissez un modèle de la liste ; le modèle saisi est absent.",
                              success=False)
        else:
            set_ollama_status(f"{len(models)} modèle(s) installé(s). "
                              "Cliquez sur « Tester IA locale » pour vérifier le classement.")

    def set_update_status(message: str, *, success: bool | None = None) -> None:
        if success is True:
            color = "#166534"
        elif success is False:
            color = "#9f1239"
        else:
            color = "#334155"
        update_status.setText(message)
        update_status.setStyleSheet(f"QLabel {{ color: {color}; }}")

    @exclusive_operation
    def check_updates_from_ui() -> None:
        check_updates_button.setEnabled(False)
        set_update_status("Recherche en cours...")
        QApplication.processEvents()
        try:
            result = check_for_updates(__version__)
        except Exception as exc:
            set_update_status("Recherche impossible", success=False)
            append_log(f"Erreur recherche mise a jour: {exc}")
            return
        finally:
            check_updates_button.setEnabled(True)
        handle_update_result(result)

    def handle_update_result(result: UpdateCheckResult) -> None:
        if not result.update_available:
            set_update_status(f"Version {result.current_version} a jour", success=True)
            append_log("Aucune mise a jour disponible.")
            return
        set_update_status(f"Version {result.latest_version} disponible")
        if result.installer_asset is None:
            append_log(
                "Mise a jour disponible, mais aucun installateur adapte a cette plateforme."
            )
            if confirm_open_release_page(result.release_url, parent=window):
                webbrowser.open(result.release_url)
            return
        if not confirm_update_install(result, parent=window):
            append_log("Mise a jour ignoree pour le moment.")
            return
        try:
            installer_path = download_update_installer(result.installer_asset)
            command = launch_update_installer(installer_path)
            set_update_status(f"Installateur lance: {result.latest_version}", success=True)
            append_log(f"Installateur telecharge: {installer_path}")
            append_log(f"Commande lancee: {' '.join(command)}")
            notify_user(
                "Mise a jour MailFlow",
                "L'installateur de mise a jour a ete lance.",
            )
        except Exception as exc:
            set_update_status("Installation impossible", success=False)
            append_log(f"Erreur lancement installateur: {exc}")

    def preview_request(
        *,
        project_numbers: Sequence[str] | None = None,
        all_projects: bool = False,
    ) -> PreviewRequest:
        return PreviewRequest(
            account_identifier=selected_account_identifier(),
            outlook_root_folder=current_outlook_root_folder(),
            year=year_input.text(),
            project_number=None if all_projects else project_input.text(),
            project_numbers=(
                None if project_numbers is None else tuple(project_numbers)
            ),
        )

    def scan_current_preview(
        *,
        project_numbers: Sequence[str] | None = None,
        progress_callback: Any | None = None,
    ) -> list[PreviewRow]:
        nonlocal active_controller
        update_projects_root()
        if (settings.ai_mode != AiMode.DISABLED and settings.ai_provider == "openai"
                and not has_openai_api_key()):
            append_log("Mode IA actif sans cle OpenAI: les lignes resteront a verifier.")
        if not controller_was_injected:
            active_controller = build_default_controller(settings)
            enable_responsive_ai()
            dynamic_window.mailflow_controller = active_controller
        return active_controller.scan_and_preview(
            preview_request(project_numbers=project_numbers),
            progress_callback=progress_callback,
        )

    def choose_project_folders(options: Sequence[Any]) -> list[str] | None:
        dialog = QDialog(window)
        dialog.setWindowTitle("Dossiers Outlook a scanner")
        dialog.resize(620, 520)
        dialog_layout = QVBoxLayout(dialog)
        folder_list = QListWidget()
        preferred = project_input.text().strip()
        for option in options:
            item = QListWidgetItem(str(option.folder_name))
            item.setData(Qt.ItemDataRole.UserRole, str(option.project_number))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = project_folder_selected_by_default(
                str(option.project_number),
                preferred,
            )
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            folder_list.addItem(item)
        dialog_layout.addWidget(folder_list, 1)

        selection_actions = QWidget()
        selection_layout = QHBoxLayout(selection_actions)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        select_all_button = QPushButton("Tout selectionner")
        select_none_button = QPushButton("Tout deselectionner")
        selection_layout.addWidget(select_all_button)
        selection_layout.addWidget(select_none_button)
        selection_layout.addStretch(1)
        dialog_layout.addWidget(selection_actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)

        def set_all_folders(check_state: Qt.CheckState) -> None:
            for index in range(folder_list.count()):
                folder_list.item(index).setCheckState(check_state)

        select_all_button.clicked.connect(
            lambda: set_all_folders(Qt.CheckState.Checked)
        )
        select_none_button.clicked.connect(
            lambda: set_all_folders(Qt.CheckState.Unchecked)
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return [
            str(folder_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(folder_list.count())
            if folder_list.item(index).checkState() == Qt.CheckState.Checked
        ]

    @exclusive_operation
    def on_scan() -> None:
        scan_button.setEnabled(False)
        set_scan_status("Lecture des dossiers Outlook...")
        append_log("Lecture des dossiers Outlook...")
        QApplication.processEvents()

        def progress(message: str) -> None:
            set_scan_status(message)
            QApplication.processEvents()

        try:
            folders = active_controller.available_project_folders(
                preview_request(all_projects=True)
            )
            if not folders:
                set_scan_status("Aucun dossier projet trouve", success=False)
                append_log("Aucun dossier projet Outlook trouve pour cette annee.")
                return
            selected_projects = choose_project_folders(folders)
            if selected_projects is None:
                set_scan_status("Scan annule")
                append_log("Scan Outlook annule avant classification.")
                return
            if not selected_projects:
                set_scan_status("Aucun dossier selectionne", success=False)
                append_log("Selectionner au moins un dossier projet a scanner.")
                return
            set_scan_status("Scan Outlook en cours...")
            append_log(
                f"Scan Outlook: {len(selected_projects)} dossier(s) selectionne(s)."
            )
            rows = scan_current_preview(
                project_numbers=selected_projects,
                progress_callback=progress,
            )
            if watch_checkbox.isChecked():
                watch_state.reset_entry_ids(
                    active_controller.scan_entry_ids(
                        preview_request(all_projects=True)
                    )
                )
            else:
                watch_state.reset(rows)
            refresh_table()
            refresh_directory_table()
            navigation.setCurrentRow(0)
            set_scan_status(f"{len(rows)} mail(s) charges.", success=True)
            append_log(f"{len(rows)} mails charges en previsualisation.")
            log_scan_directory_update()
        except Exception as exc:
            set_scan_status("Erreur scan Outlook", success=False)
            expand_logs()
            append_log(f"Erreur scan: {exc}")
            QMessageBox.warning(window, "Erreur scan Outlook", str(exc))
        finally:
            scan_button.setEnabled(True)

    def on_reset_workspace() -> None:
        if operation_in_progress:
            return
        try:
            if watch_checkbox.isChecked():
                watch_checkbox.setChecked(False)
            active_controller.reset_preview()
            watch_state.reset([])
            review_queue.clear()
            project_input.clear()
            table.clearSelection()
            refresh_table()
            mail_preview.clear()
            navigation.setCurrentRow(0)
            append_log(
                "Espace de travail reinitialise. Réglages, annuaire et archives conserves."
            )
        except Exception as exc:
            append_log(f"Erreur reinitialisation: {exc}")

    def on_export_report() -> None:
        if operation_in_progress:
            return
        try:
            path = active_controller.export_report()
            append_log(f"Rapport exporte: {path}")
        except Exception as exc:
            append_log(f"Erreur export rapport: {exc}")

    def on_export_project_html() -> None:
        if operation_in_progress:
            return
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

    def on_import_directory() -> None:
        try:
            result = active_controller.import_contact_directory(
                account_identifier=selected_account_identifier(),
                outlook_root_folder=current_outlook_root_folder(),
            )
            message = format_directory_import_result(result)
            directory_status_label.setText(message)
            refresh_directory_table()
            append_log(message)
        except Exception as exc:
            append_log(f"Erreur import annuaire: {exc}")

    def on_mark_ignored() -> None:
        if operation_in_progress:
            return
        indexes = selected_table_row_indexes()
        if not indexes:
            append_log("Aucune ligne selectionnee a ignorer.")
            return
        active_controller.mark_selected_ignored(indexes)
        refresh_table()
        append_log(f"{len(indexes)} ligne(s) marquee(s) comme ignoree(s).")

    def on_restore_archivable() -> None:
        if operation_in_progress:
            return
        active_controller.mark_all_archivable()
        refresh_table()
        append_log("Toutes les lignes archivables sont remises en Archiver.")

    @exclusive_operation
    def on_reclassify() -> None:
        if not active_controller.preview_rows:
            append_log("Aucun mail a reclassifier.")
            return
        try:
            scan_status_label.setText("Reclassification IA...")

            def reclassify_progress(message: str) -> None:
                scan_status_label.setText(message)
                QApplication.processEvents()

            active_controller.reclassify_preview(
                progress_callback=reclassify_progress
            )
            refresh_table()
            refresh_directory_table()
            scan_status_label.setText("Reclassification terminee")
            append_log("Projet reclasse avec l'IA et les roles actuels de l'annuaire.")
        except Exception as exc:
            scan_status_label.setText("Echec reclassification")
            append_log(f"Erreur reclassification: {exc}")

    def archive_new_ready_rows(new_entry_ids: Sequence[str]) -> ArchiveBatchResult:
        new_ids = set(new_entry_ids)
        new_rows = [row for row in active_controller.preview_rows if row.mail.entry_id in new_ids]
        ready_ids = {
            row.mail.entry_id
            for row in rows_to_archive(new_rows, include_review=False)
        }
        ready_indexes = [
            index
            for index, row in enumerate(active_controller.preview_rows)
            if row.mail.entry_id in ready_ids
        ]
        if not ready_indexes:
            return ArchiveBatchResult()
        return active_controller.archive_selected(ready_indexes, include_review=False)

    def on_watch_toggled(enabled: bool) -> None:
        if not enabled:
            watch_timer.stop()
            sync_tray_watch_action(False)
            append_log("Surveillance Outlook desactivee.")
            return
        try:
            entry_ids = active_controller.scan_entry_ids(
                preview_request(all_projects=True)
            )
            watch_state.reset_entry_ids(entry_ids)
            sync_review_queue_from_preview()
            watch_timer.start()
            sync_tray_watch_action(True)
            notify_user(
                "Surveillance activee",
                "MailFlow surveille tous les dossiers projet toutes les 5 minutes.",
            )
            append_log(
                "Surveillance Outlook activee sur tous les dossiers projet: "
                f"{len(entry_ids)} mail(s) connus, controle toutes les 5 minutes."
            )
        except Exception as exc:
            watch_checkbox.blockSignals(True)
            watch_checkbox.setChecked(False)
            watch_checkbox.blockSignals(False)
            sync_tray_watch_action(False)
            append_log(f"Impossible d'activer la surveillance Outlook: {exc}")

    @exclusive_operation
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
        previous_entry_ids = set(watch_state.known_entry_ids)
        try:
            request = preview_request(all_projects=True)
            current_entry_ids = active_controller.scan_entry_ids(request)
            change = watch_state.update_entry_ids(current_entry_ids)
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
        try:
            active_controller.scan_incremental_preview(
                request,
                change.new_entry_ids,
            )
        except Exception as exc:
            watch_state.reset_entry_ids(previous_entry_ids)
            append_log(f"Surveillance Outlook en attente: {exc}")
            notify_user(
                "Nouveaux mails en attente",
                "La lecture ou la classification a echoue; MailFlow reessaiera.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
            return
        if log_scan_directory_update():
            refresh_directory_table()
        archive_result = ArchiveBatchResult()
        try:
            archive_result = archive_new_ready_rows(change.new_entry_ids)
        except Exception as exc:
            append_log(f"Erreur archivage automatique: {exc}")
            notify_user(
                "Archivage automatique en erreur",
                "Verifier MailFlow pour traiter les nouveaux mails.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        new_pending_count = sync_review_queue_from_preview()
        refresh_table()
        append_log(f"Surveillance Outlook: {change.new_count} nouveau(x) mail(s) detecte(s).")
        if archive_result.exported_count:
            append_log(f"Archivage automatique: {format_archive_result(archive_result)}")
            notify_user(
                "Archivage automatique",
                f"{archive_result.exported_count} nouveau(x) mail(s) archive(s).",
            )
        if archive_result.failure_count:
            append_log(f"Archivage automatique incomplet: {format_archive_result(archive_result)}")
            for failure in archive_result.failures[:5]:
                append_log(f"Echec auto {failure.mail_id}: {failure.reason}")
            notify_user(
                "Archivage automatique incomplet",
                f"{archive_result.failure_count} mail(s) n'ont pas pu etre archives.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        if review_queue.count:
            append_log(f"File a verifier: {review_queue.count} mail(s) en attente.")
            notify_user(
                "Mails a verifier",
                f"{review_queue.count} mail(s) attendent une validation.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        if archive_result.exported_count == 0 and new_pending_count == 0:
            notify_user(
                "Nouveaux mails detectes",
                f"{change.new_count} nouveau(x) mail(s), aucun archivage automatique.",
            )

    def send_review_reminder_if_due() -> None:
        due_key = review_reminder_due_key(
            datetime.now(),
            settings.review_reminder_times,
            review_queue.count,
            sent_review_reminders,
        )
        if due_key is None:
            return
        sent_review_reminders.add(due_key)
        notify_user(
            "Mails a verifier",
            f"{review_queue.count} mail(s) attendent une validation dans MailFlow.",
            QSystemTrayIcon.MessageIcon.Warning,
        )
        append_log(f"Rappel file a verifier: {review_queue.count} mail(s) en attente.")

    def selected_table_row_indexes() -> list[int]:
        selection_model = table.selectionModel()
        if selection_model is None:
            return []
        rows = {index.row() for index in selection_model.selectedRows()}
        if not rows:
            rows = {index.row() for index in table.selectedIndexes()}
        return sorted(row for row in rows if not table.isRowHidden(row))

    def update_preview_from_selection() -> None:
        if refreshing_table:
            return
        selected = selected_table_row_indexes()
        update_mail_preview(selected[0] if selected else table.currentRow())
        update_selection_actions()

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
        if operation_in_progress:
            return
        indexes = selected_table_row_indexes()
        summary = summarize_archive_selection(active_controller.preview_rows, indexes)
        if summary.selected_count == 0:
            append_log("Aucune ligne selectionnee.")
            set_scan_status(
                "Sélectionnez des mails, ou utilisez le menu Archiver tous les mails prêts."
            )
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
        if operation_in_progress:
            return
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
    search_input.textChanged.connect(apply_mail_filters)
    status_filter.currentIndexChanged.connect(apply_mail_filters)
    clear_filters_button.clicked.connect(clear_mail_filters)
    review_button.clicked.connect(
        lambda: open_manual_dialog(selected_table_row_indexes()[0])
        if len(selected_table_row_indexes()) == 1 else None
    )
    reset_button.clicked.connect(on_reset_workspace)
    watch_checkbox.toggled.connect(on_watch_toggled)
    tray_watch_action.toggled.connect(request_watch_from_tray)
    tray_open_action.triggered.connect(show_window_from_tray)
    tray_quit_action.triggered.connect(quit_application)
    background_action.triggered.connect(lambda _checked=False: hide_to_background())
    tray_icon.activated.connect(
        lambda reason: show_window_from_tray()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    watch_timer.timeout.connect(run_watch_scan)
    review_reminder_timer.timeout.connect(send_review_reminder_if_due)
    review_reminder_timer.start()
    export_html_button.clicked.connect(on_export_project_html)
    archive_button.clicked.connect(on_archive_selection)
    archive_selection_action.triggered.connect(lambda _checked=False: on_archive_selection())
    archive_all_action.triggered.connect(lambda _checked=False: on_archive_all_except_review())
    ignore_action.triggered.connect(lambda _checked=False: on_mark_ignored())
    restore_archivable_action.triggered.connect(lambda _checked=False: on_restore_archivable())
    reclassify_action.triggered.connect(lambda _checked=False: on_reclassify())
    open_folder_action.triggered.connect(
        lambda _checked=False: append_log(str(settings.local_projects_root))
    )
    report_action.triggered.connect(lambda _checked=False: on_export_report())
    import_directory_button.clicked.connect(on_import_directory)
    refresh_directory_button.clicked.connect(refresh_directory_table)
    add_directory_button.clicked.connect(add_directory_organization)
    delete_directory_button.clicked.connect(delete_selected_directory_organization)
    rename_directory_button.clicked.connect(rename_selected_directory_organization)
    merge_directory_button.clicked.connect(merge_selected_directory_organization)
    navigation.currentRowChanged.connect(
        lambda index: refresh_directory_table() if index == 2 else None
    )
    save_openai_key_button.clicked.connect(save_openai_key_from_input)
    test_openai_key_button.clicked.connect(test_openai_key_from_input)
    ai_provider_combo.currentIndexChanged.connect(update_ai_provider_fields)
    ollama_model_input.currentTextChanged.connect(reset_ollama_status)
    ollama_base_url_input.textChanged.connect(reset_ollama_status)
    refresh_ollama_models_button.clicked.connect(refresh_ollama_models)
    test_ollama_button.clicked.connect(test_ollama_from_input)
    check_updates_button.clicked.connect(check_updates_from_ui)
    save_settings_button.clicked.connect(save_current_settings)
    rename_folder_button.clicked.connect(rename_selected_folder)
    merge_folder_button.clicked.connect(merge_selected_folder)
    browse_projects_button.clicked.connect(browse_projects_root)
    account_combo.currentIndexChanged.connect(lambda _index: populate_outlook_root_options())
    table.cellDoubleClicked.connect(lambda row, _column: open_manual_dialog(row))
    table.currentCellChanged.connect(lambda row, _col, _old_row, _old_col: update_mail_preview(row))
    table.itemSelectionChanged.connect(update_preview_from_selection)
    shortcut_actions: list[Any] = []
    for sequence, callback in (
        ("Ctrl+F", lambda: (navigation.setCurrentRow(0), search_input.setFocus())),
        ("Ctrl+R", on_scan),
        ("Ctrl+Return", on_archive_selection),
        ("Ctrl+,", lambda: navigation.setCurrentRow(3)),
    ):
        shortcut_action = QAction(window)
        shortcut_action.setShortcut(QKeySequence(sequence))
        shortcut_action.triggered.connect(callback)
        window.addAction(shortcut_action)
        shortcut_actions.append(shortcut_action)
    review_shortcut = QAction(table)
    review_shortcut.setShortcut(QKeySequence("Return"))
    review_shortcut.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
    review_shortcut.triggered.connect(review_button.click)
    table.addAction(review_shortcut)
    shortcut_actions.append(review_shortcut)
    dynamic_window.mailflow_close_handler = handle_window_close
    populate_account_options()
    refresh_directory_table()

    dynamic_window.mailflow_controller = active_controller
    dynamic_window.mailflow_scan_button = scan_button
    dynamic_window.mailflow_scan_status_label = scan_status_label
    dynamic_window.mailflow_reset_button = reset_button
    dynamic_window.mailflow_preview_table = table
    dynamic_window.mailflow_refresh_table = refresh_table
    dynamic_window.mailflow_folder_tree = folder_tree
    dynamic_window.mailflow_rename_folder_button = rename_folder_button
    dynamic_window.mailflow_merge_folder_button = merge_folder_button
    dynamic_window.mailflow_archive_button = archive_button
    dynamic_window.mailflow_archive_menu = archive_menu
    dynamic_window.mailflow_archive_selection_action = archive_selection_action
    dynamic_window.mailflow_archive_all_action = archive_all_action
    dynamic_window.mailflow_more_actions_button = more_actions_button
    dynamic_window.mailflow_more_actions_menu = more_actions_menu
    dynamic_window.mailflow_ignore_action = ignore_action
    dynamic_window.mailflow_restore_archivable_action = restore_archivable_action
    dynamic_window.mailflow_reclassify_action = reclassify_action
    dynamic_window.mailflow_background_action = background_action
    dynamic_window.mailflow_open_folder_action = open_folder_action
    dynamic_window.mailflow_report_action = report_action
    dynamic_window.mailflow_import_directory_button = import_directory_button
    dynamic_window.mailflow_refresh_directory_button = refresh_directory_button
    dynamic_window.mailflow_add_directory_button = add_directory_button
    dynamic_window.mailflow_delete_directory_button = delete_directory_button
    dynamic_window.mailflow_rename_directory_button = rename_directory_button
    dynamic_window.mailflow_merge_directory_button = merge_directory_button
    dynamic_window.mailflow_directory_table = directory_table
    dynamic_window.mailflow_directory_status_label = directory_status_label
    dynamic_window.mailflow_logs = logs
    dynamic_window.mailflow_project_digest_preview = project_digest_preview
    dynamic_window.mailflow_mail_preview = mail_preview
    dynamic_window.mailflow_export_html_button = export_html_button
    dynamic_window.mailflow_watch_checkbox = watch_checkbox
    dynamic_window.mailflow_watch_timer = watch_timer
    dynamic_window.mailflow_review_reminder_timer = review_reminder_timer
    dynamic_window.mailflow_review_reminder_times_input = review_reminder_times_input
    dynamic_window.mailflow_ai_mode_combo = ai_mode_combo
    dynamic_window.mailflow_ai_provider_combo = ai_provider_combo
    dynamic_window.mailflow_ai_provider_hint = ai_provider_hint
    dynamic_window.mailflow_ai_model_input = ai_model_input
    dynamic_window.mailflow_ollama_model_input = ollama_model_input
    dynamic_window.mailflow_ollama_base_url_input = ollama_base_url_input
    dynamic_window.mailflow_ollama_timeout_input = ollama_timeout_input
    dynamic_window.mailflow_ollama_status = ollama_status
    dynamic_window.mailflow_refresh_ollama_models_button = refresh_ollama_models_button
    dynamic_window.mailflow_test_ollama_button = test_ollama_button
    dynamic_window.mailflow_openai_key_input = openai_key_input
    dynamic_window.mailflow_openai_key_status = openai_key_status
    dynamic_window.mailflow_save_openai_key_button = save_openai_key_button
    dynamic_window.mailflow_test_openai_key_button = test_openai_key_button
    dynamic_window.mailflow_check_updates_button = check_updates_button
    dynamic_window.mailflow_update_status = update_status
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
    dynamic_window.mailflow_navigation = navigation
    dynamic_window.mailflow_pages = pages
    dynamic_window.mailflow_content_splitter = content_splitter
    dynamic_window.mailflow_workspace_splitter = workspace_splitter
    dynamic_window.mailflow_settings_scroll_area = settings_scroll_area
    dynamic_window.mailflow_logs_toggle = logs_toggle
    dynamic_window.mailflow_search_input = search_input
    dynamic_window.mailflow_status_filter = status_filter
    dynamic_window.mailflow_clear_filters_button = clear_filters_button
    dynamic_window.mailflow_summary_label = summary_label
    dynamic_window.mailflow_review_button = review_button
    dynamic_window.mailflow_mail_results = mail_results
    dynamic_window.mailflow_preview_tabs = preview_tabs
    dynamic_window.mailflow_inspector = preview
    dynamic_window.mailflow_selection_status = selection_status
    dynamic_window.mailflow_detail_columns_action = detail_columns_action
    dynamic_window.mailflow_inspector_action = inspector_action
    dynamic_window.mailflow_set_operation_busy = set_operation_busy
    dynamic_window.mailflow_selected_table_row_indexes = selected_table_row_indexes
    refresh_table()
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
        AiMode.AMBIGUOUS_ONLY: "activee",
        AiMode.ALL: "activee",
    }
    return labels[mode]


def interlocutor_label(interlocutor: InterlocutorType) -> str:
    return interlocutor.value


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
        "padding: 3px 6px; }"
    )


def set_combo_value_by_data(combo: Any, value: str) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return
    if combo.currentIndex() < 0 and combo.count() > 0:
        combo.setCurrentIndex(0)


def set_combo_value_by_text(combo: Any, value: str) -> None:
    for index in range(combo.count()):
        if combo.itemText(index) == value:
            combo.setCurrentIndex(index)
            return
    if value:
        combo.addItem(value)
        combo.setCurrentText(value)


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


def tray_tooltip_text(watch_enabled: bool, review_count: int = 0) -> str:
    status = UI_TEXT["tray_watch_active"] if watch_enabled else UI_TEXT["tray_watch_inactive"]
    text = f"{UI_TEXT['window_title']} - {status}"
    if review_count > 0:
        text = f"{text} - {review_count} a verifier"
    return text


def parse_reminder_times(value: str) -> list[str]:
    cleaned = value.strip()
    if not cleaned:
        return []
    result: list[str] = []
    for part in re.split(r"[,;\s]+", cleaned):
        if not part:
            continue
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", part)
        if match is None:
            msg = f"Heure de rappel invalide: {part}"
            raise ValueError(msg)
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            msg = f"Heure de rappel invalide: {part}"
            raise ValueError(msg)
        normalized = f"{hour:02d}:{minute:02d}"
        if normalized not in result:
            result.append(normalized)
    return result


def format_reminder_times(times: Sequence[str]) -> str:
    return ", ".join(parse_reminder_times(", ".join(times)))


def review_reminder_due_key(
    now: datetime,
    reminder_times: Sequence[str],
    review_count: int,
    sent_keys: set[str],
) -> str | None:
    if review_count <= 0:
        return None
    current_time = now.strftime("%H:%M")
    normalized_times = set(parse_reminder_times(", ".join(reminder_times)))
    if current_time not in normalized_times:
        return None
    key = now.strftime("%Y-%m-%d %H:%M")
    return None if key in sent_keys else key


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


def confirm_update_install(result: UpdateCheckResult, *, parent: Any | None = None) -> bool:
    from PySide6.QtWidgets import QMessageBox

    asset_name = result.installer_asset.name if result.installer_asset is not None else "-"
    response = QMessageBox.question(
        parent,
        "Mise a jour disponible",
        (
            f"La version {result.latest_version} est disponible.\n\n"
            f"Installateur: {asset_name}\n\n"
            "Telecharger et lancer l'installateur maintenant ?\n"
            "Fermer MailFlow pendant l'installation si l'installateur le demande."
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return response == QMessageBox.StandardButton.Yes


def confirm_open_release_page(release_url: str, *, parent: Any | None = None) -> bool:
    from PySide6.QtWidgets import QMessageBox

    response = QMessageBox.question(
        parent,
        "Mise a jour disponible",
        (
            "Une mise a jour est disponible, mais aucun installateur automatique "
            "n'a ete trouve pour cette plateforme.\n\n"
            f"Ouvrir la page de release ?\n{release_url}"
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


def format_directory_values(values: Sequence[str], *, limit: int) -> str:
    cleaned = [value for value in values if value.strip()]
    if len(cleaned) <= limit:
        return ", ".join(cleaned)
    visible = ", ".join(cleaned[:limit])
    return f"{visible}, +{len(cleaned) - limit}"


def format_directory_entry_label(entry: Any) -> str:
    organization_id = int(entry.organization_id)
    name = str(entry.name)
    domains = tuple(str(item) for item in entry.domains)
    domain_label = format_directory_values(domains, limit=3) or "sans domaine"
    return f"{name} [{domain_label}] #{organization_id}"


def format_directory_import_result(result: object) -> str:
    return (
        "Import annuaire termine: "
        f"{getattr(result, 'scanned_mail_count', 0)} mail(s) scanne(s), "
        f"{getattr(result, 'imported_contact_count', 0)} contact(s) importe(s), "
        f"{getattr(result, 'new_organizations', 0)} entreprise(s) creee(s), "
        f"{getattr(result, 'new_domains', 0)} domaine(s) ajoute(s)."
    )


def format_archive_result(result: ArchiveBatchResult) -> str:
    message = (
        "Archivage termine: "
        f"{result.exported_count} exporte(s), "
        f"{result.skipped_count} ignore(s), "
        f"{result.failure_count} erreur(s)."
    )
    warnings = getattr(result, "warnings", [])
    if warnings:
        message += f" {len(warnings)} avertissement(s) : " + " ; ".join(warnings[:5])
    return message


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
    learning_term: str | None = None,
    misleading_term: str | None = None,
    manual_required: bool = False,
) -> ManualClassificationUpdate:
    mail_type = {
        RoutingCategory.CORRESPONDANCE.value: MailType.CORRESPONDANCE_GENERALE,
        RoutingCategory.DEMANDE_DE_PRIX.value: MailType.DEMANDE_DE_PRIX,
        RoutingCategory.COMMANDE.value: MailType.COMMANDE,
    }.get(mail_type_value)
    if mail_type is None:
        mail_type = MailType.A_VERIFIER if not mail_type_value else MailType(mail_type_value)
    return ManualClassificationUpdate(
        mail_type=mail_type,
        interlocutor=InterlocutorType(
            interlocutor_value.strip().casefold().replace(" ", "_")
        ),
        target_relative_folder=destination_value,
        learning_term=learning_term,
        misleading_term=misleading_term,
        manual_required=manual_required,
    )
