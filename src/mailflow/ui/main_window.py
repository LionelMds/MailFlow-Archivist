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
from mailflow.core.mailbox_sorting import MailboxSortRequest, missing_project_numbers
from mailflow.core.projectflow_link import (
    ProjectFlowError,
    ProjectFlowInstallation,
    ProjectFlowLink,
    find_projectflow,
    projectflow_report,
)
from mailflow.core.update_installer import download_update_installer, launch_update_installer
from mailflow.core.updates import UpdateCheckResult, check_for_updates
from mailflow.models import (
    REVIEW_CONFIDENCE_THRESHOLD,
    AiMode,
    InterlocutorType,
    MailType,
    ManualClassificationUpdate,
    OutlookAccount,
    PreviewAction,
    PreviewRow,
    RoutingCategory,
)
from mailflow.outlook.mailbox import MailboxSourceKind
from mailflow.ui.directory_review import (
    BUSINESS_ROLES,
    directory_item_view,
    domains_text,
    entry_role,
    has_business_role,
    matches_directory_filter,
    next_without_role,
    projects_text,
    role_tag,
    selected_suggestion,
    split_contact,
    suggestion_headline,
    without_role_count,
)
from mailflow.ui.folder_review import (
    DuplicateHint,
    breadcrumb_text,
    duplicate_note,
    find_duplicate_folders,
    folder_role_tag,
    folder_rows,
    folder_text,
    leaf_name,
    merge_text,
    tree_header_text,
)
from mailflow.ui.mailbox_sort_view import (
    CHECK_COLUMN,
    MAILBOX_SORT_COLUMNS,
    PERIOD_OPTIONS,
    STATUS_FILTERS,
    STATUS_LABELS,
    STATUS_TAG_KINDS,
    build_mailbox_sort_confirmation,
    build_projectflow_confirmation,
    format_mailbox_sort_result,
    format_projectflow_report,
    mailbox_counts_text,
    mailbox_destination_view,
    mailbox_since,
    period_label,
    proposal_found_in,
    proposal_meta_text,
    proposal_to_cells,
    summarize_mailbox_analysis,
)
from mailflow.ui.review_queue import (
    SHORTCUTS_HINT,
    archive_button_text,
    attachments_text,
    build_queue_update,
    bulk_destination_text,
    bulk_selection_view,
    bulk_validation_problem,
    decision_text_html,
    decision_view,
    destination_category,
    destination_for_role,
    engine_label,
    first_queue_index,
    mail_meta_text,
    next_review_index,
    percent_html,
    queue_header_text,
    queue_item_view,
    queue_progress,
    role_choice,
    role_text,
    sender_html,
    validation_problem,
)
from mailflow.ui.theme import COLORS

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
    "test_jev_key": "Tester Jev",
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
    "validate_role_suggestions": "Valider les suggestions sûres",
    "refresh_roles": "Actualiser les rôles des mails",
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
    "analyze_mailbox": "Analyser la boîte mail",
    "sort_mailbox": "Ranger les mails cochés",
}
MAILBOX_PAGE = 3
SETTINGS_PAGE = 4
DIRECTORY_COLUMNS = (
    "Entreprise", "Domaines", "Contacts", "Rôle global", "Rôle suggéré", "Projets",
)
DIRECTORY_ROLE_COLUMN = 3
DIRECTORY_SUGGESTION_COLUMN = 4
DIRECTORY_PROJECTS_COLUMN = 5

WATCH_INTERVAL_MS = 5 * 60 * 1000
REMINDER_CHECK_INTERVAL_MS = 60 * 1000


def project_folder_selected_by_default(
    project_number: str,
    project_filter: str,
    *,
    archived: bool = False,
) -> bool:
    cleaned_filter = project_filter.strip()
    if not cleaned_filter:
        # Archived projects stay available but unchecked: their mails were archived
        # already, and classifying them again would only cost AI calls.
        return not archived
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

    from mailflow.ui.theme import apply_light_theme

    app = QApplication([])
    apply_light_theme(app)
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
    from PySide6.QtCore import QItemSelectionModel, QSize, Qt, QTimer
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
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QFrame,
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
        QProgressDialog,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSlider,
        QSplitter,
        QStackedWidget,
        QSystemTrayIcon,
        QTableWidget,
        QTableWidgetItem,
        QTableWidgetSelectionRange,
        QTabWidget,
        QTextBrowser,
        QTextEdit,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    from mailflow import __version__
    from mailflow.classifier.ai_classifier import AiClassifier
    from mailflow.classifier.jev_classifier import JevClassifier
    from mailflow.classifier.ollama_classifier import OllamaClassifier
    from mailflow.config import (
        AI_MODEL_OPTIONS,
        DEFAULT_AI_MODEL,
        DEFAULT_JEV_MODEL,
        DEFAULT_MAILBOX_PENDING_FOLDER,
        DEFAULT_OLLAMA_BASE_URL,
        DEFAULT_OLLAMA_MODEL,
        JEV_MODEL_OPTIONS,
        get_jev_api_key,
        get_openai_api_key,
        save_settings,
        set_jev_api_key,
        set_openai_api_key,
    )
    from mailflow.core.app_controller import (
        PreviewRequest,
        build_ai_classifier,
        build_default_controller,
        build_project_suggester,
    )
    from mailflow.core.manual_review import suggested_manual_destination
    from mailflow.core.project_digest import build_project_digest
    from mailflow.resources import app_icon_path
    from mailflow.ui.background_call import (
        ResponsiveAiClassifier,
        ResponsiveProjectSuggester,
        run_with_event_loop,
    )
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
    from mailflow.ui.theme import APP_STYLESHEET, icon_path, load_brand_fonts
    from mailflow.ui.widgets.industry import (
        BlueprintButton,
        BlueprintFrame,
        ConfidenceBar,
        SegmentedControl,
        SplitBar,
        field_block,
        heading_font,
        kicker_label,
        pane,
        set_kicker_text,
        set_tag,
        tag_label,
        text_label,
    )

    class TableComboBox(QComboBox):
        """A list inside a table: the mouse wheel scrolls the table, never the choice."""

        def wheelEvent(self, event: Any) -> None:
            event.ignore()

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
        pipeline.decision_confidence_threshold = settings.decision_confidence_threshold
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
    preferred_folder_path: str | None = None
    watch_state = WatchState()
    review_queue = ReviewQueue()
    sent_review_reminders: set[str] = set()
    load_brand_fonts()
    central = QWidget()
    central.setObjectName("workspace")
    layout = QHBoxLayout(central)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    window.setStyleSheet(APP_STYLESHEET)
    window.resize(1440, 900)
    window.setMinimumSize(1180, 720)

    def column(
        kind: str,
        *,
        width: int | None = None,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
        spacing: int = 0,
    ) -> tuple[Any, Any]:
        frame = pane(kind, width=width)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(*margins)
        frame_layout.setSpacing(spacing)
        return frame, frame_layout

    def row_layout(*widgets: Any, spacing: int = 8, stretch_at: int | None = None) -> Any:
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(spacing)
        for index, widget in enumerate(widgets):
            if index == stretch_at:
                holder_layout.addStretch(1)
            holder_layout.addWidget(widget)
        if stretch_at is not None and stretch_at >= len(widgets):
            holder_layout.addStretch(1)
        return holder

    # Navigation column: the word mark, the five screens and the version.
    nav_column = QFrame()
    nav_column.setObjectName("navColumn")
    nav_column.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    nav_column.setFixedWidth(176)
    nav_layout = QVBoxLayout(nav_column)
    nav_layout.setContentsMargins(0, 14, 0, 12)
    nav_layout.setSpacing(0)
    app_title = QLabel("MailFlow")
    app_title.setProperty("role", "title")
    app_title.setContentsMargins(18, 0, 18, 14)
    navigation = QListWidget()
    navigation.setObjectName("navigation")
    navigation.setAccessibleName("Navigation principale")
    navigation.setIconSize(QSize(17, 17))
    for label, icon_name in (
        ("Mails", "mail"),
        ("Arborescence", "folder-tree"),
        ("Annuaire", "book-user"),
        ("Boîte mail", "inbox"),
        ("Réglages", "sliders"),
    ):
        navigation.addItem(QListWidgetItem(QIcon(icon_path(icon_name)), label))
    navigation.setCurrentRow(0)
    version_label = text_label(f"v{__version__}", "small")
    version_label.setContentsMargins(18, 0, 18, 0)
    nav_layout.addWidget(app_title)
    nav_layout.addWidget(navigation, 1)
    nav_layout.addWidget(version_label)

    # Scan bar: the Outlook source, the scan and the watch, above every screen.
    top_bar = QFrame()
    top_bar.setProperty("pane", "bar")
    top_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    top_layout = QHBoxLayout(top_bar)
    top_layout.setContentsMargins(20, 8, 20, 8)
    top_layout.setSpacing(24)
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
    scan_button = BlueprintButton(UI_TEXT["scan_button"])
    scan_button.setToolTip("Analyser les dossiers sélectionnés (Ctrl+R)")
    reset_button = QPushButton(UI_TEXT["reset_workspace"])
    watch_checkbox = QCheckBox(UI_TEXT["watch_outlook"])
    watch_checkbox.setToolTip("Contrôler Outlook toutes les 5 minutes, même fenêtre fermée.")
    scan_status_label = QLabel("")
    scan_status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    scan_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    scan_status_label.setStyleSheet(f"QLabel {{ color: {COLORS['muted']}; }}")
    scan_panel = QWidget()
    scan_layout = QGridLayout(scan_panel)
    scan_layout.setContentsMargins(0, 0, 0, 0)
    scan_layout.setHorizontalSpacing(12)
    scan_layout.setVerticalSpacing(4)
    for column_index, (label, field) in enumerate((
        ("Compte Outlook", account_combo),
        ("Dossier source", outlook_root_combo),
        ("Année", year_input),
        ("Projet (facultatif)", project_input),
    )):
        field_label = text_label(label, "field")
        field_label.setBuddy(field)
        scan_layout.addWidget(field_label, 0, column_index)
        scan_layout.addWidget(field, 1, column_index)
    scan_layout.setColumnStretch(0, 2)
    scan_layout.setColumnStretch(1, 2)
    scan_layout.addWidget(scan_button, 0, 4, 2, 1, Qt.AlignmentFlag.AlignBottom)
    scan_layout.addWidget(reset_button, 1, 5)
    workflow_label = text_label("1 SCANNER  →  2 VÉRIFIER  →  3 ARCHIVER", "small")
    workflow_widget = QWidget()
    workflow_layout = QVBoxLayout(workflow_widget)
    workflow_layout.setContentsMargins(0, 0, 0, 0)
    workflow_layout.setSpacing(6)
    workflow_layout.addWidget(workflow_label, 0, Qt.AlignmentFlag.AlignRight)
    workflow_layout.addWidget(watch_checkbox, 0, Qt.AlignmentFlag.AlignRight)
    top_layout.addWidget(scan_panel, 1)
    top_layout.addWidget(workflow_widget)

    content_splitter = QSplitter(Qt.Orientation.Horizontal)
    content_splitter.setChildrenCollapsible(False)
    content_splitter.setHandleWidth(0)
    content_splitter.addWidget(nav_column)

    workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
    workspace_splitter.setChildrenCollapsible(False)
    pages = QStackedWidget()

    # ── Mails: the review queue (1c) by default, the table on request. ──
    mail_page = QWidget()
    mail_page_layout = QVBoxLayout(mail_page)
    mail_page_layout.setContentsMargins(0, 0, 0, 0)
    mail_page_layout.setSpacing(0)
    mail_views = QStackedWidget()
    mail_page_layout.addWidget(mail_views)
    mail_view_toggle = SegmentedControl([("File", "queue"), ("Tableau", "table")])
    mail_view_toggle.setFixedWidth(150)
    mail_view_toggle.setToolTip("File de vérification ou tableau de tous les mails")
    mail_view_toggle.set_value("queue")

    queue_view = QWidget()
    queue_view_layout = QHBoxLayout(queue_view)
    queue_view_layout.setContentsMargins(0, 0, 0, 0)
    queue_view_layout.setSpacing(0)
    queue_pane, queue_pane_layout = column("list", width=340)
    queue_header, queue_header_layout = column("header", margins=(16, 14, 16, 12), spacing=6)
    queue_title = text_label("File de vérification", "heading")
    queue_toggle_host = QHBoxLayout()
    queue_toggle_host.setContentsMargins(0, 0, 0, 0)
    queue_title_row = QHBoxLayout()
    queue_title_row.addWidget(queue_title)
    queue_title_row.addStretch(1)
    queue_title_row.addLayout(queue_toggle_host)
    queue_header_layout.addLayout(queue_title_row)
    queue_header_info = text_label("Aucun mail analysé", "small")
    queue_header_layout.addWidget(queue_header_info)
    queue_progress_bar = SplitBar()
    queue_header_layout.addWidget(queue_progress_bar)
    queue_list = QListWidget()
    queue_list.setProperty("pane", "queue")
    queue_list.setAccessibleName("File de vérification des mails")
    queue_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    queue_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    queue_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    queue_list.setToolTip("Ctrl ou Maj + clic : sélectionner plusieurs mails")
    queue_footer, queue_footer_layout = column("footer", margins=(11, 7, 11, 7))
    queue_archive_button = BlueprintButton("Archiver")
    queue_archive_button.setToolTip("Archiver tous les mails prêts, après confirmation")
    queue_footer_layout.addWidget(queue_archive_button)
    queue_pane_layout.addWidget(queue_header)
    queue_pane_layout.addWidget(queue_list, 1)
    queue_pane_layout.addWidget(queue_footer)

    queue_detail, queue_detail_layout = column("detail")
    queue_detail.setMinimumWidth(300)
    queue_detail_stack = QStackedWidget()
    queue_detail_layout.addWidget(queue_detail_stack)
    queue_single = QWidget()
    queue_single_layout = QVBoxLayout(queue_single)
    queue_single_layout.setContentsMargins(28, 22, 28, 18)
    queue_single_layout.setSpacing(12)
    queue_action_tag = tag_label()
    queue_meta_label = text_label("", "muted")
    queue_single_layout.addWidget(row_layout(queue_action_tag, queue_meta_label, stretch_at=2))
    queue_subject_label = text_label("", "display", wrap=True)
    queue_subject_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    queue_single_layout.addWidget(queue_subject_label)
    queue_sender_label = QLabel()
    queue_sender_label.setTextFormat(Qt.TextFormat.RichText)
    queue_sender_label.setWordWrap(True)
    queue_sender_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    queue_single_layout.addWidget(queue_sender_label)
    queue_clip_icon = QLabel()
    queue_clip_icon.setPixmap(QIcon(icon_path("paperclip")).pixmap(14, 14))
    queue_attachments_label = text_label("", "muted", wrap=True)
    queue_single_layout.addWidget(
        row_layout(queue_clip_icon, queue_attachments_label, spacing=6, stretch_at=2)
    )
    queue_body_frame = BlueprintFrame(padding=14)
    queue_body = QTextBrowser()
    queue_body.setProperty("pane", "plain")
    queue_body.setOpenExternalLinks(False)
    queue_body.setAccessibleName("Contenu du mail")
    queue_body_frame.body.addWidget(queue_body)
    queue_single_layout.addWidget(queue_body_frame, 1)
    queue_previous_button = QPushButton("← Précédent")
    queue_next_button = QPushButton("Suivant →")
    queue_shortcuts_label = text_label(SHORTCUTS_HINT, "small", wrap=True)
    queue_single_layout.addWidget(
        row_layout(queue_previous_button, queue_next_button, queue_shortcuts_label, stretch_at=2)
    )
    queue_detail_stack.addWidget(queue_single)

    queue_bulk = QWidget()
    queue_bulk_layout = QVBoxLayout(queue_bulk)
    queue_bulk_layout.setContentsMargins(28, 22, 28, 18)
    queue_bulk_layout.setSpacing(12)
    queue_bulk_layout.addWidget(
        text_label("Appliquer un classement à la sélection", "display", wrap=True)
    )
    queue_bulk_layout.addWidget(text_label(
        "Chaque mail garde le dossier de sa propre entreprise. Les mails archivés ne sont "
        "jamais modifiés.",
        "muted",
        wrap=True,
    ))
    queue_bulk_frame = BlueprintFrame()
    queue_bulk_values: dict[str, Any] = {}
    for key, caption in (
        ("count", "Mails concernés"),
        ("destination", "Dossier de destination"),
        ("role", "Rôle enregistré pour l'entreprise"),
    ):
        line = QFrame()
        line.setProperty("pane", "header" if key != "role" else "detail")
        line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        line_layout = QHBoxLayout(line)
        line_layout.setContentsMargins(16, 11, 16, 11)
        value_label = QLabel()
        value_label.setStyleSheet("QLabel { font-weight: 700; }")
        value_label.setWordWrap(True)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        line_layout.addWidget(QLabel(caption))
        line_layout.addWidget(value_label, 1)
        queue_bulk_values[key] = value_label
        queue_bulk_frame.body.addWidget(line)
    queue_bulk_frame.body.setSpacing(0)
    queue_bulk_layout.addWidget(queue_bulk_frame)
    queue_bulk_layout.addStretch(1)
    queue_detail_stack.addWidget(queue_bulk)

    queue_empty = QWidget()
    queue_empty_layout = QVBoxLayout(queue_empty)
    queue_empty_layout.setContentsMargins(40, 24, 40, 24)
    queue_empty_layout.addStretch(1)
    queue_empty_title = text_label("Commencez par une analyse Outlook", "display", wrap=True)
    queue_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    queue_empty_body = text_label(
        "Choisissez votre compte et votre dossier source, puis cliquez sur Scanner Outlook. "
        "Les mails à vérifier apparaîtront ici, un à la fois.",
        "muted",
        wrap=True,
    )
    queue_empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    queue_empty_settings_button = QPushButton("Configurer les dossiers et l'IA")
    queue_empty_layout.addWidget(queue_empty_title)
    queue_empty_layout.addSpacing(10)
    queue_empty_layout.addWidget(queue_empty_body)
    queue_empty_layout.addSpacing(16)
    queue_empty_layout.addWidget(queue_empty_settings_button, 0, Qt.AlignmentFlag.AlignHCenter)
    queue_empty_layout.addStretch(1)
    queue_detail_stack.addWidget(queue_empty)

    queue_decision, queue_decision_layout = column("decision", width=340)
    queue_decision_stack = QStackedWidget()
    queue_decision_layout.addWidget(queue_decision_stack)
    decision_single = QWidget()
    decision_layout = QVBoxLayout(decision_single)
    decision_layout.setContentsMargins(22, 22, 22, 12)
    decision_layout.setSpacing(12)
    decision_kicker = kicker_label("Proposition IA")
    decision_figure = QLabel()
    decision_figure.setProperty("role", "figure")
    decision_figure.setTextFormat(Qt.TextFormat.RichText)
    decision_bar = ConfidenceBar()
    decision_threshold_label = text_label("", "small")
    decision_text = QLabel()
    decision_text.setTextFormat(Qt.TextFormat.RichText)
    decision_text.setWordWrap(True)
    decision_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    decision_text.setStyleSheet("QLabel { font-size: 13.5px; }")
    decision_destination_combo = QComboBox()
    decision_destination_combo.addItems(list(DESTINATION_OPTIONS))
    decision_destination_combo.setPlaceholderText("À choisir")
    decision_destination_combo.setAccessibleName("Destination du mail")
    decision_target_label = text_label("", "small", wrap=True)
    decision_role = SegmentedControl(
        [("Client", InterlocutorType.CLIENT), ("Fournisseur", InterlocutorType.FOURNISSEUR)],
        allow_none=True,
    )
    decision_warning = text_label("", "warning", wrap=True)
    decision_validate_button = BlueprintButton("Valider et suivant")
    decision_validate_button.setToolTip(
        "Enregistrer ce classement, mémoriser le rôle de l'entreprise et passer au mail "
        "suivant à vérifier (Entrée)"
    )
    widget: Any
    for widget in (
        decision_kicker, decision_figure, decision_bar, decision_threshold_label,
        decision_text,
    ):
        decision_layout.addWidget(widget)
    decision_layout.addSpacing(4)
    decision_layout.addWidget(field_block("Destination", decision_destination_combo))
    decision_layout.addWidget(decision_target_label)
    decision_layout.addWidget(field_block("Rôle de l'entreprise (global)", decision_role))
    decision_layout.addWidget(decision_warning)
    decision_layout.addStretch(1)
    decision_layout.addWidget(decision_validate_button)
    queue_decision_stack.addWidget(decision_single)

    decision_bulk = QWidget()
    decision_bulk_layout = QVBoxLayout(decision_bulk)
    decision_bulk_layout.setContentsMargins(22, 22, 22, 12)
    decision_bulk_layout.setSpacing(14)
    decision_bulk_layout.addWidget(kicker_label("Classement"))
    bulk_category = SegmentedControl(
        list(zip(MAIL_TYPE_OPTIONS, DESTINATION_OPTIONS, strict=True)),
        vertical=True,
        allow_none=True,
    )
    bulk_role = SegmentedControl(
        [("Client", InterlocutorType.CLIENT), ("Fournisseur", InterlocutorType.FOURNISSEUR)],
        allow_none=True,
    )
    bulk_warning = text_label("", "warning", wrap=True)
    bulk_apply_button = BlueprintButton("Appliquer")
    bulk_cancel_button = QPushButton("Annuler")
    decision_bulk_layout.addWidget(bulk_category)
    decision_bulk_layout.addWidget(field_block("Rôle de l'entreprise", bulk_role))
    decision_bulk_layout.addWidget(bulk_warning)
    decision_bulk_layout.addStretch(1)
    decision_bulk_layout.addWidget(bulk_apply_button)
    decision_bulk_layout.addWidget(bulk_cancel_button)
    queue_decision_stack.addWidget(decision_bulk)

    decision_empty = QWidget()
    decision_empty_layout = QVBoxLayout(decision_empty)
    decision_empty_layout.setContentsMargins(22, 22, 22, 22)
    decision_empty_layout.setSpacing(12)
    decision_empty_layout.addWidget(kicker_label("Proposition IA"))
    decision_empty_layout.addWidget(text_label(
        "Après le scan, la proposition de classement de chaque mail s'affiche ici pour "
        "être validée ou corrigée.",
        "muted",
        wrap=True,
    ))
    decision_empty_layout.addStretch(1)
    queue_decision_stack.addWidget(decision_empty)

    queue_view_layout.addWidget(queue_pane)
    queue_view_layout.addWidget(queue_detail, 1)
    queue_view_layout.addWidget(queue_decision)
    mail_views.addWidget(queue_view)

    table_view = QWidget()
    mail_layout = QVBoxLayout(table_view)
    mail_layout.setContentsMargins(20, 16, 20, 12)
    mail_layout.setSpacing(10)
    mail_heading = QLabel("Votre espace de classement")
    mail_heading.setProperty("role", "heading")
    table_toggle_host = QHBoxLayout()
    table_toggle_host.setContentsMargins(0, 0, 0, 0)
    mail_heading_row = QHBoxLayout()
    mail_heading_row.addWidget(mail_heading)
    mail_heading_row.addStretch(1)
    mail_heading_row.addLayout(table_toggle_host)
    mail_layout.addLayout(mail_heading_row)
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
    refresh_roles_action = QAction("Actualiser les rôles (sans IA)", window)
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
    more_actions_menu.addAction(refresh_roles_action)
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
    review_button.setToolTip(
        "Modifier le classement du mail sélectionné, ou appliquer le même classement "
        "à plusieurs mails sélectionnés (Entrée)"
    )
    actions_layout.addWidget(review_button)
    actions_layout.addWidget(export_html_button)
    actions_layout.addWidget(more_actions_button)
    actions_layout.addStretch(1)
    table = QTableWidget(0, len(PREVIEW_COLUMNS))
    table.setHorizontalHeaderLabels(list(PREVIEW_COLUMNS))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setMinimumHeight(220)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(38)
    table.setAccessibleName("Mails analysés et propositions de classement")
    for aligned_table in (table,):
        aligned_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
    table.horizontalHeader().setSectionsMovable(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    # Keep the information used to make a decision visible first on smaller displays.
    for visual_index, logical_index in enumerate((4, 9, 3, 7)):
        table.horizontalHeader().moveSection(
            table.horizontalHeader().visualIndex(logical_index), visual_index
        )
    for column_index in (0, 1, 2, TYPE_COLUMN, INTERLOCUTOR_COLUMN, 8):
        table.setColumnHidden(column_index, True)
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
    mail_views.addWidget(table_view)
    pages.addWidget(mail_page)

    # ── Arborescence (2a): folders, the selected folder, rename or merge it. ──
    tree_widget = QWidget()
    tree_page_layout = QHBoxLayout(tree_widget)
    tree_page_layout.setContentsMargins(0, 0, 0, 0)
    tree_page_layout.setSpacing(0)
    tree_pane, tree_layout = column("list", width=340)
    tree_header, tree_header_layout = column("header", margins=(16, 14, 16, 12), spacing=2)
    tree_heading = QLabel("Dossiers proposés")
    tree_heading.setProperty("role", "heading")
    tree_hint = text_label("Organisez les destinations avant de lancer l'archivage.", "small")
    tree_hint.setWordWrap(True)
    tree_header_layout.addWidget(tree_heading)
    tree_header_layout.addWidget(tree_hint)
    folder_tree = QTreeWidget()
    folder_tree.setProperty("pane", "plain")
    folder_tree.setColumnCount(3)
    folder_tree.setHeaderLabels(["Dossier propose", "", "Mails"])
    folder_tree.setHeaderHidden(True)
    folder_tree.setIndentation(18)
    folder_tree.setIconSize(QSize(15, 15))
    folder_tree.setAccessibleName("Dossiers proposés pour l'archivage")
    folder_tree.header().setStretchLastSection(False)
    folder_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    folder_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    folder_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    tree_footer, tree_footer_layout = column("footer", margins=(11, 7, 11, 7))
    tree_archive_button = BlueprintButton("Archiver")
    tree_archive_button.setToolTip("Archiver tous les mails prêts, après confirmation")
    tree_footer_layout.addWidget(tree_archive_button)
    tree_layout.addWidget(tree_header)
    tree_layout.addWidget(folder_tree, 1)
    tree_layout.addWidget(tree_footer)

    tree_detail, tree_detail_layout = column("detail", margins=(28, 22, 28, 18), spacing=12)
    tree_detail.setMinimumWidth(300)
    tree_breadcrumb = text_label("", "muted")
    tree_folder_title = text_label("Aucun dossier sélectionné", "display", wrap=True)
    tree_duplicate_tag = tag_label("", "outline")
    tree_mails_frame = BlueprintFrame()
    tree_mails_table = QTableWidget(0, 2)
    tree_mails_table.setProperty("pane", "plain")
    tree_mails_table.horizontalHeader().hide()
    tree_mails_table.verticalHeader().hide()
    tree_mails_table.setShowGrid(False)
    tree_mails_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tree_mails_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    tree_mails_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    tree_mails_table.verticalHeader().setDefaultSectionSize(42)
    tree_mails_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    tree_mails_table.horizontalHeader().setSectionResizeMode(
        1, QHeaderView.ResizeMode.ResizeToContents
    )
    tree_mails_table.setAccessibleName("Mails du dossier sélectionné")
    tree_mails_frame.body.addWidget(tree_mails_table)
    tree_folder_note = text_label(
        "Sélectionnez un dossier à gauche pour voir ses mails.", "muted", wrap=True
    )
    tree_detail_layout.addWidget(tree_breadcrumb)
    tree_detail_layout.addWidget(tree_folder_title)
    tree_detail_layout.addWidget(row_layout(tree_duplicate_tag, stretch_at=1))
    tree_detail_layout.addWidget(tree_mails_frame)
    tree_detail_layout.addWidget(tree_folder_note)
    tree_detail_layout.addStretch(1)

    tree_decision, tree_decision_layout = column(
        "decision", width=340, margins=(22, 22, 22, 12), spacing=12
    )
    tree_kicker = kicker_label("Dossier")
    tree_decision_title = text_label("", "statement", wrap=True)
    tree_decision_text = text_label("", None, wrap=True)
    tree_decision_text.setStyleSheet("QLabel { font-size: 13.5px; }")
    tree_target_input = QLineEdit()
    tree_target_input.setReadOnly(True)
    tree_target_field = field_block("Nom du dossier cible", tree_target_input)
    tree_merge_duplicate_button = BlueprintButton("Fusionner et suivant")
    rename_folder_button = QPushButton("Renommer dossier")
    merge_folder_button = QPushButton("Fusionner vers...")
    tree_ignore_duplicate_button = QPushButton("Ignorer ce doublon")
    tree_ignore_duplicate_button.setProperty("role", "ghost")
    for widget in (tree_kicker, tree_decision_title, tree_decision_text, tree_target_field):
        tree_decision_layout.addWidget(widget)
    tree_decision_layout.addStretch(1)
    for widget in (
        tree_merge_duplicate_button, rename_folder_button, merge_folder_button,
        tree_ignore_duplicate_button,
    ):
        tree_decision_layout.addWidget(widget)
    tree_page_layout.addWidget(tree_pane)
    tree_page_layout.addWidget(tree_detail, 1)
    tree_page_layout.addWidget(tree_decision)
    pages.addWidget(tree_widget)

    # ── Annuaire (2b): companies, the selected company, its global role. ──
    directory_page = QWidget()
    directory_page_layout = QHBoxLayout(directory_page)
    directory_page_layout.setContentsMargins(0, 0, 0, 0)
    directory_page_layout.setSpacing(0)
    directory_views = QStackedWidget()
    directory_view_toggle = SegmentedControl([("Liste", "list"), ("Tableau", "table")])
    directory_view_toggle.setFixedWidth(150)
    directory_view_toggle.set_value("list")
    import_directory_button = QPushButton(UI_TEXT["import_directory"])
    refresh_directory_button = QPushButton(UI_TEXT["refresh_directory"])
    add_directory_button = QPushButton(UI_TEXT["add_directory"])
    delete_directory_button = QPushButton(UI_TEXT["delete_directory"])
    rename_directory_button = QPushButton(UI_TEXT["rename_directory"])
    merge_directory_button = QPushButton(UI_TEXT["merge_directory"])
    directory_global_actions = QWidget()
    directory_global_layout = QGridLayout(directory_global_actions)
    directory_global_layout.setContentsMargins(0, 0, 0, 0)
    directory_global_layout.setSpacing(8)
    directory_global_layout.addWidget(import_directory_button, 0, 0, 1, 2)
    directory_global_layout.addWidget(add_directory_button, 1, 0)
    directory_global_layout.addWidget(refresh_directory_button, 1, 1)
    directory_edit_actions = QWidget()
    directory_edit_layout = QHBoxLayout(directory_edit_actions)
    directory_edit_layout.setContentsMargins(0, 0, 0, 0)
    directory_edit_layout.addWidget(rename_directory_button)
    directory_edit_layout.addWidget(merge_directory_button)
    directory_edit_layout.addWidget(delete_directory_button)
    directory_edit_layout.addStretch(1)

    directory_list_view = QWidget()
    directory_list_view_layout = QHBoxLayout(directory_list_view)
    directory_list_view_layout.setContentsMargins(0, 0, 0, 0)
    directory_list_view_layout.setSpacing(0)
    directory_list_pane, directory_list_layout = column("list", width=340)
    directory_list_header, directory_list_header_layout = column(
        "header", margins=(16, 14, 16, 12), spacing=8
    )
    directory_list_title = text_label("Entreprises", "heading")
    directory_list_count = text_label("", "small")
    directory_toggle_host_list = QHBoxLayout()
    directory_toggle_host_list.setContentsMargins(0, 0, 0, 0)
    directory_title_row = QHBoxLayout()
    directory_title_row.setSpacing(8)
    directory_title_row.addWidget(directory_list_title)
    directory_title_row.addWidget(directory_list_count)
    directory_title_row.addStretch(1)
    directory_title_row.addLayout(directory_toggle_host_list)
    directory_list_header_layout.addLayout(directory_title_row)
    directory_search_input = QLineEdit()
    directory_search_input.setPlaceholderText("Rechercher…")
    directory_search_input.setClearButtonEnabled(True)
    directory_search_input.setAccessibleName("Rechercher une entreprise")
    directory_filter = SegmentedControl([("Toutes", "all"), ("Sans rôle", "without_role")])
    directory_filter.set_value("all")
    directory_list_header_layout.addWidget(directory_search_input)
    directory_list_header_layout.addWidget(directory_filter)
    directory_list = QListWidget()
    directory_list.setProperty("pane", "queue")
    directory_list.setAccessibleName("Entreprises de l'annuaire")
    directory_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    directory_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    directory_list_footer, directory_list_footer_layout = column(
        "footer", margins=(16, 10, 16, 10)
    )
    directory_list_footer_layout.addWidget(directory_global_actions)
    directory_list_layout.addWidget(directory_list_header)
    directory_list_layout.addWidget(directory_list, 1)
    directory_list_layout.addWidget(directory_list_footer)

    directory_detail, directory_detail_layout = column(
        "detail", margins=(28, 22, 28, 18), spacing=12
    )
    directory_detail.setMinimumWidth(300)
    directory_role_tag = tag_label("", "outline")
    directory_name_label = text_label("Aucune entreprise sélectionnée", "display", wrap=True)
    directory_meta_label = text_label("", "muted", wrap=True)
    directory_contacts_frame = BlueprintFrame()
    directory_contacts_table = QTableWidget(0, 2)
    directory_contacts_table.setProperty("pane", "plain")
    directory_contacts_table.setHorizontalHeaderLabels(["Contact", "Adresse"])
    directory_contacts_table.verticalHeader().hide()
    directory_contacts_table.setShowGrid(False)
    directory_contacts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    directory_contacts_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    directory_contacts_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    directory_contacts_table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.Stretch
    )
    directory_contacts_table.setAccessibleName("Contacts de l'entreprise")
    directory_contacts_table.verticalHeader().setDefaultSectionSize(40)
    directory_contacts_table.horizontalHeader().setFixedHeight(34)
    directory_contacts_frame.body.addWidget(directory_contacts_table)
    directory_projects_label = text_label("", "muted", wrap=True)
    directory_detail_layout.addWidget(row_layout(directory_role_tag, stretch_at=1))
    directory_detail_layout.addWidget(directory_name_label)
    directory_detail_layout.addWidget(directory_meta_label)
    directory_detail_layout.addWidget(directory_contacts_frame)
    directory_detail_layout.addWidget(directory_projects_label)
    directory_detail_layout.addStretch(1)
    directory_edit_host_list = QVBoxLayout()
    directory_edit_host_list.setContentsMargins(0, 0, 0, 0)
    directory_detail_layout.addLayout(directory_edit_host_list)
    directory_list_view_layout.addWidget(directory_list_pane)
    directory_list_view_layout.addWidget(directory_detail, 1)
    directory_views.addWidget(directory_list_view)

    directory_table_view = QWidget()
    directory_layout = QVBoxLayout(directory_table_view)
    directory_layout.setContentsMargins(20, 16, 20, 12)
    directory_layout.setSpacing(8)
    directory_heading = QLabel("Votre annuaire d'entreprises")
    directory_heading.setProperty("role", "heading")
    directory_toggle_host_table = QHBoxLayout()
    directory_toggle_host_table.setContentsMargins(0, 0, 0, 0)
    directory_heading_row = QHBoxLayout()
    directory_heading_row.addWidget(directory_heading)
    directory_heading_row.addStretch(1)
    directory_heading_row.addLayout(directory_toggle_host_table)
    directory_hint = QLabel(
        "Les rôles enregistrés fixent le classement des mails. Pour une entreprise sans "
        "rôle, Jev suggère fournisseur ou client : un rôle validé s'applique aussitôt aux "
        "mails, sans nouvel appel à l'IA."
    )
    directory_hint.setWordWrap(True)
    directory_hint.setProperty("role", "muted")
    directory_layout.addLayout(directory_heading_row)
    directory_layout.addWidget(directory_hint)
    directory_table_actions_host = QHBoxLayout()
    directory_table_actions_host.setContentsMargins(0, 0, 0, 0)
    directory_table_actions_host.setSpacing(16)
    directory_layout.addLayout(directory_table_actions_host)
    directory_table = QTableWidget(0, len(DIRECTORY_COLUMNS))
    directory_table.setHorizontalHeaderLabels(list(DIRECTORY_COLUMNS))
    directory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    directory_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    directory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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
        DIRECTORY_SUGGESTION_COLUMN,
        QHeaderView.ResizeMode.Interactive,
    )
    directory_table.horizontalHeader().setSectionResizeMode(
        DIRECTORY_PROJECTS_COLUMN,
        QHeaderView.ResizeMode.ResizeToContents,
    )
    directory_table.setColumnWidth(0, 220)
    for aligned_table in (directory_table, directory_contacts_table):
        aligned_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
    directory_layout.addWidget(directory_table, 1)
    directory_views.addWidget(directory_table_view)

    directory_decision, directory_decision_layout = column(
        "decision", width=340, margins=(22, 22, 22, 12), spacing=12
    )
    directory_kicker = kicker_label("Rôle global")
    directory_figure = QLabel()
    directory_figure.setProperty("role", "figure")
    directory_figure.setTextFormat(Qt.TextFormat.RichText)
    directory_suggestion_label = QLabel()
    directory_suggestion_label.setTextFormat(Qt.TextFormat.RichText)
    directory_suggestion_label.setWordWrap(True)
    directory_role_choice = SegmentedControl(
        [("Client", InterlocutorType.CLIENT), ("Fournisseur", InterlocutorType.FOURNISSEUR)],
        allow_none=True,
    )
    directory_role_note = text_label(
        "S'applique aussitôt aux mails de cette entreprise et à tous les projets, sans "
        "nouvel appel à l'IA.",
        "small",
        wrap=True,
    )
    validate_suggestions_button = QPushButton(UI_TEXT["validate_role_suggestions"])
    validate_suggestions_button.setToolTip(
        "Enregistrer les rôles suggérés avec au moins 80 % de confiance, après confirmation."
    )
    validate_suggestions_button.setEnabled(False)
    refresh_roles_button = QPushButton(UI_TEXT["refresh_roles"])
    refresh_roles_button.setToolTip(
        "Appliquer les rôles de l'annuaire aux mails affichés, sans nouvel appel à l'IA."
    )
    directory_status_label = text_label("Annuaire local", "small", wrap=True)
    directory_validate_button = BlueprintButton("Valider et suivant")
    directory_validate_button.setToolTip(
        "Enregistrer ce rôle dans l'annuaire et passer à l'entreprise suivante sans rôle"
    )
    for widget in (
        directory_kicker, directory_figure, directory_suggestion_label,
        field_block("Rôle global", directory_role_choice), directory_role_note,
    ):
        directory_decision_layout.addWidget(widget)
    directory_decision_layout.addStretch(1)
    for widget in (
        validate_suggestions_button, refresh_roles_button, directory_status_label,
        directory_validate_button,
    ):
        directory_decision_layout.addWidget(widget)
    directory_page_layout.addWidget(directory_views, 1)
    directory_page_layout.addWidget(directory_decision)
    pages.addWidget(directory_page)

    # ── Boîte mail (2d): loose mails, the selected mail, where it will go. ──
    mailbox_page = QWidget()
    mailbox_page_layout = QHBoxLayout(mailbox_page)
    mailbox_page_layout.setContentsMargins(0, 0, 0, 0)
    mailbox_page_layout.setSpacing(0)
    mailbox_list_pane, mailbox_list_layout = column("list", width=340)
    mailbox_list_header, mailbox_list_header_layout = column(
        "header", margins=(16, 14, 16, 12), spacing=8
    )
    mailbox_options_button = QPushButton("Options")
    mailbox_options_button.setProperty("role", "ghost")
    mailbox_options_button.setToolTip("Choisir les dossiers et la période à analyser")
    mailbox_list_header_layout.addWidget(
        row_layout(text_label("À ranger", "heading"), mailbox_options_button, stretch_at=1)
    )
    mailbox_counts_label = text_label("Aucune analyse pour le moment", "small")
    mailbox_list_header_layout.addWidget(mailbox_counts_label)
    mailbox_summary_label = QLabel("Aucune analyse de la boîte mail pour le moment.")
    mailbox_summary_label.setProperty("role", "summary")
    mailbox_summary_label.setWordWrap(True)
    mailbox_analyze_button = QPushButton(UI_TEXT["analyze_mailbox"])
    mailbox_list_header_layout.addWidget(mailbox_analyze_button)
    mailbox_filter_combo = QComboBox()
    mailbox_filter_combo.setAccessibleName("Filtrer les mails de la boîte par état")
    for label, status_value in STATUS_FILTERS:
        mailbox_filter_combo.addItem(label, None if status_value is None else status_value.value)
    mailbox_check_all_button = QPushButton("Tout cocher")
    mailbox_uncheck_all_button = QPushButton("Tout décocher")
    mailbox_list_header_layout.addWidget(mailbox_filter_combo)
    mailbox_list_header_layout.addWidget(
        row_layout(mailbox_check_all_button, mailbox_uncheck_all_button)
    )
    mailbox_table = QTableWidget(0, len(MAILBOX_SORT_COLUMNS))
    mailbox_table.setProperty("pane", "plain")
    mailbox_table.setHorizontalHeaderLabels(list(MAILBOX_SORT_COLUMNS))
    mailbox_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    mailbox_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    mailbox_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    mailbox_table.setShowGrid(False)
    mailbox_table.verticalHeader().hide()
    mailbox_table.verticalHeader().setDefaultSectionSize(40)
    mailbox_table.setAccessibleName("Mails de la boîte et dossier projet proposé")
    mailbox_table.setToolTip("Double-cliquez sur un mail pour l'ouvrir dans Outlook.")
    mailbox_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    mailbox_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    mailbox_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
    # The list keeps the check, the subject and the state; the detail shows the rest.
    for column_index in range(len(MAILBOX_SORT_COLUMNS)):
        mailbox_table.setColumnHidden(column_index, column_index not in {CHECK_COLUMN, 4, 8})
    mailbox_table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    mailbox_table.setColumnWidth(CHECK_COLUMN, 58)
    mailbox_table.setColumnWidth(8, 112)
    mailbox_list_layout.addWidget(mailbox_list_header)
    mailbox_list_layout.addWidget(mailbox_table, 1)

    mailbox_detail, mailbox_detail_layout = column("detail")
    mailbox_detail.setMinimumWidth(300)
    mailbox_detail_stack = QStackedWidget()
    mailbox_detail_layout.addWidget(mailbox_detail_stack)
    mailbox_options_view = QWidget()
    mailbox_layout = QVBoxLayout(mailbox_options_view)
    mailbox_layout.setContentsMargins(28, 22, 28, 18)
    mailbox_layout.setSpacing(10)
    mailbox_heading = QLabel("Ranger la boîte mail Outlook")
    mailbox_heading.setProperty("role", "display")
    mailbox_hint = QLabel(
        "Place les mails de la boîte de réception, du dossier à classer et des éléments "
        "envoyés dans le dossier projet Outlook dont le numéro (20XX-XXXX) figure dans "
        "l'objet, le corps ou une pièce jointe. Un mail qui cite plusieurs projets est copié "
        "dans chacun. Rien ne bouge avant votre confirmation et aucun mail n'est supprimé."
    )
    mailbox_hint.setProperty("role", "muted")
    mailbox_hint.setWordWrap(True)
    mailbox_layout.addWidget(mailbox_heading)
    mailbox_layout.addWidget(mailbox_hint)
    mailbox_layout.addWidget(mailbox_summary_label)
    mailbox_options = QGroupBox("Où chercher")
    mailbox_options_layout = QGridLayout(mailbox_options)
    mailbox_options_layout.setColumnStretch(3, 1)
    mailbox_inbox_checkbox = QCheckBox("Boîte de réception")
    mailbox_inbox_checkbox.setChecked(True)
    mailbox_inbox_checkbox.setToolTip(
        "Mails posés directement dans le dossier source choisi en haut, sans ses sous-dossiers."
    )
    mailbox_pending_checkbox = QCheckBox("Dossier")
    mailbox_pending_checkbox.setChecked(True)
    mailbox_pending_input = QLineEdit(settings.mailbox_pending_folder)
    mailbox_pending_input.setPlaceholderText(DEFAULT_MAILBOX_PENDING_FOLDER)
    mailbox_pending_input.setAccessibleName("Dossier Outlook à classer")
    mailbox_pending_input.setToolTip(
        "Cherché sous la boîte de réception puis à la racine du compte."
    )
    mailbox_pending_input.setFixedWidth(160)
    mailbox_pending_widget = QWidget()
    mailbox_pending_layout = QHBoxLayout(mailbox_pending_widget)
    mailbox_pending_layout.setContentsMargins(0, 0, 0, 0)
    mailbox_pending_layout.addWidget(mailbox_pending_checkbox)
    mailbox_pending_layout.addWidget(mailbox_pending_input)
    mailbox_sent_checkbox = QCheckBox("Éléments envoyés")
    mailbox_sent_checkbox.setChecked(True)
    mailbox_options_layout.addWidget(mailbox_inbox_checkbox, 0, 0)
    mailbox_options_layout.addWidget(mailbox_pending_widget, 0, 1)
    mailbox_options_layout.addWidget(mailbox_sent_checkbox, 0, 2)
    mailbox_period_combo = QComboBox()
    mailbox_period_combo.setAccessibleName("Période analysée")
    for period_days, label in PERIOD_OPTIONS:
        mailbox_period_combo.addItem(label, period_days)
    if mailbox_period_combo.findData(settings.mailbox_sort_days) < 0:
        mailbox_period_combo.addItem(
            period_label(settings.mailbox_sort_days), settings.mailbox_sort_days
        )
    mailbox_period_combo.setCurrentIndex(
        mailbox_period_combo.findData(settings.mailbox_sort_days)
    )
    mailbox_period_label = QLabel("Période")
    mailbox_period_label.setBuddy(mailbox_period_combo)
    mailbox_options_layout.addWidget(mailbox_period_label, 1, 0)
    mailbox_options_layout.addWidget(mailbox_period_combo, 1, 1)
    mailbox_attachments_checkbox = QCheckBox(
        "Lire le contenu des pièces jointes (PDF, Word, Excel, texte)"
    )
    mailbox_attachments_checkbox.setChecked(settings.mailbox_read_attachments)
    mailbox_attachments_checkbox.setToolTip(
        "Seulement quand l'objet, le corps et les noms des pièces jointes ne mènent à aucun "
        "dossier projet. Les fichiers sont lus dans un dossier temporaire aussitôt effacé."
    )
    mailbox_options_layout.addWidget(mailbox_attachments_checkbox, 2, 0, 1, 4)
    mailbox_jev_checkbox = QCheckBox(
        "Proposer un projet avec Jev pour les mails sans numéro (suggestion à cocher)"
    )
    mailbox_jev_checkbox.setChecked(settings.mailbox_suggest_with_jev)
    mailbox_options_layout.addWidget(mailbox_jev_checkbox, 3, 0, 1, 4)
    mailbox_layout.addWidget(mailbox_options)
    mailbox_layout.addStretch(1)
    mailbox_detail_stack.addWidget(mailbox_options_view)

    mailbox_mail_view = QWidget()
    mailbox_mail_layout = QVBoxLayout(mailbox_mail_view)
    mailbox_mail_layout.setContentsMargins(28, 22, 28, 18)
    mailbox_mail_layout.setSpacing(12)
    mailbox_detail_tag = tag_label()
    mailbox_detail_subject = text_label("", "display", wrap=True)
    mailbox_detail_meta = text_label("", "muted", wrap=True)
    mailbox_detail_frame = BlueprintFrame(padding=6)
    mailbox_detail_values: dict[str, Any] = {}
    mailbox_detail_grid = QGridLayout()
    mailbox_detail_grid.setHorizontalSpacing(18)
    mailbox_detail_grid.setVerticalSpacing(10)
    mailbox_detail_grid.setColumnStretch(1, 1)
    for row_index, (key, caption) in enumerate((
        ("correspondent", "Interlocuteur"),
        ("numbers", "Numéros trouvés"),
        ("found_in", "Trouvé dans"),
        ("note", "Remarque"),
    )):
        caption_label = text_label(caption, "field")
        value_label = text_label("", None, wrap=True)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mailbox_detail_grid.addWidget(caption_label, row_index, 0, Qt.AlignmentFlag.AlignTop)
        mailbox_detail_grid.addWidget(value_label, row_index, 1)
        mailbox_detail_values[key] = value_label
    mailbox_detail_frame.body.addLayout(mailbox_detail_grid)
    mailbox_mail_layout.addWidget(row_layout(mailbox_detail_tag, stretch_at=1))
    mailbox_mail_layout.addWidget(mailbox_detail_subject)
    mailbox_mail_layout.addWidget(mailbox_detail_meta)
    mailbox_mail_layout.addWidget(mailbox_detail_frame)
    mailbox_mail_layout.addWidget(text_label(
        "Numéros cherchés dans l'objet, le corps et les pièces jointes. "
        "Double-clic dans la liste : ouvrir le mail dans Outlook.",
        "muted",
        wrap=True,
    ))
    mailbox_mail_layout.addStretch(1)
    mailbox_detail_stack.addWidget(mailbox_mail_view)

    mailbox_decision, mailbox_decision_layout = column(
        "decision", width=340, margins=(22, 22, 22, 12), spacing=12
    )
    mailbox_destination_title = text_label("Aucun mail sélectionné", "statement", wrap=True)
    mailbox_destination_frame = BlueprintFrame(padding=6)
    mailbox_destination_lines = QLabel()
    mailbox_destination_lines.setWordWrap(True)
    mailbox_destination_lines.setStyleSheet("QLabel { font-size: 13.5px; }")
    mailbox_destination_frame.body.addWidget(mailbox_destination_lines)
    mailbox_destination_note = text_label(
        "Sélectionnez un mail analysé pour voir son dossier projet.", "small", wrap=True
    )
    mailbox_status_label = QLabel("")
    mailbox_status_label.setWordWrap(True)
    mailbox_status_label.setProperty("role", "small")
    mailbox_projectflow_button = QPushButton("Créer les dossiers absents avec ProjectFlow")
    mailbox_projectflow_button.setEnabled(False)
    mailbox_sort_button = BlueprintButton(UI_TEXT["sort_mailbox"])
    mailbox_sort_button.setEnabled(False)
    for widget in (
        kicker_label("Destination"), mailbox_destination_title, mailbox_destination_frame,
        mailbox_destination_note,
    ):
        mailbox_decision_layout.addWidget(widget)
    mailbox_decision_layout.addStretch(1)
    for widget in (mailbox_status_label, mailbox_projectflow_button, mailbox_sort_button):
        mailbox_decision_layout.addWidget(widget)
    mailbox_page_layout.addWidget(mailbox_list_pane)
    mailbox_page_layout.addWidget(mailbox_detail, 1)
    mailbox_page_layout.addWidget(mailbox_decision)
    pages.addWidget(mailbox_page)

    # ── Réglages (2c): sections, the selected section, the engine test. ──
    settings_container = QWidget()
    settings_container_layout = QHBoxLayout(settings_container)
    settings_container_layout.setContentsMargins(0, 0, 0, 0)
    settings_container_layout.setSpacing(0)
    settings_sections_pane, settings_sections_layout = column("list", width=340)
    settings_sections_header, settings_sections_header_layout = column(
        "header", margins=(16, 14, 16, 12)
    )
    settings_sections_header_layout.addWidget(text_label("Réglages", "heading"))
    settings_sections_list = QListWidget()
    settings_sections_list.setProperty("pane", "queue")
    settings_sections_list.setAccessibleName("Sections des réglages")
    settings_sections_layout.addWidget(settings_sections_header)
    settings_sections_layout.addWidget(settings_sections_list, 1)

    settings_page = QWidget()
    settings_layout = QVBoxLayout(settings_page)
    settings_layout.setContentsMargins(28, 22, 28, 18)
    settings_layout.setSpacing(12)
    settings_heading = QLabel("Moteur IA")
    settings_heading.setProperty("role", "display")
    settings_layout.addWidget(settings_heading)
    settings_hint = QLabel(
        "Enregistrez pour appliquer les choix IA et confidentialité. "
        "Le dossier local sera utilisé au prochain scan."
    )
    settings_hint.setProperty("role", "muted")
    settings_hint.setWordWrap(True)
    settings_layout.addWidget(settings_hint)
    settings_sections_stack = QStackedWidget()
    settings_layout.addWidget(settings_sections_stack)
    settings_layout.addStretch(1)

    def settings_section() -> tuple[Any, Any]:
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(16)
        settings_sections_stack.addWidget(section)
        return section, section_layout

    _engine_section, engine_layout = settings_section()
    ai_mode_combo = QComboBox()
    for mode in (AiMode.DISABLED, AiMode.ALL):
        ai_mode_combo.addItem(ai_mode_label(mode), mode.value)
    selected_ai_mode = (
        AiMode.ALL if settings.ai_mode == AiMode.AMBIGUOUS_ONLY else settings.ai_mode
    )
    set_combo_value_by_data(ai_mode_combo, selected_ai_mode.value)
    ai_mode_combo.setVisible(False)
    ai_mode_choice = SegmentedControl(
        [("Activée", AiMode.ALL.value), ("Désactivée", AiMode.DISABLED.value)]
    )
    ai_mode_choice.setFixedWidth(240)
    ai_provider_combo = QComboBox()
    ai_provider_combo.addItem("OpenAI — API", "openai")
    ai_provider_combo.addItem("Ollama — IA locale sur ce PC", "ollama")
    ai_provider_combo.addItem("Jev (TypeSafe) — API de classification", "jev")
    set_combo_value_by_data(ai_provider_combo, settings.ai_provider)
    ai_provider_combo.setVisible(False)
    engine_layout.addWidget(ai_mode_combo)
    engine_layout.addWidget(ai_provider_combo)
    engine_layout.addWidget(field_block("Mode", ai_mode_choice))
    provider_cards: dict[str, Any] = {}
    provider_cards_widget = QWidget()
    provider_cards_layout = QHBoxLayout(provider_cards_widget)
    provider_cards_layout.setContentsMargins(0, 0, 0, 0)
    provider_cards_layout.setSpacing(12)
    provider_cards_group = QButtonGroup(provider_cards_widget)
    for provider_key, card_kicker, card_title, card_body in (
        ("openai", "Cloud", "OpenAI", "API OpenAI"),
        ("ollama", "Local", "Ollama", "Mails analysés sur ce PC"),
        ("jev", "API", "Jev (TypeSafe)", "Probabilités sur liste fermée"),
    ):
        card = QPushButton()
        card.setCheckable(True)
        card.setProperty("role", "card")
        card.setFixedHeight(92)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setAccessibleName(f"Moteur {card_title}")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(2)
        card_kicker_label = kicker_label(card_kicker)
        card_title_label = QLabel(card_title)
        card_title_label.setFont(heading_font(17))
        card_body_label = text_label(card_body, "small", wrap=True)
        for card_child in (card_kicker_label, card_title_label, card_body_label):
            card_child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            card_layout.addWidget(card_child)
        card_layout.addStretch(1)
        provider_cards_group.addButton(card)
        provider_cards_layout.addWidget(card)
        provider_cards[provider_key] = (card, card_kicker_label, card_kicker)
    engine_layout.addWidget(field_block("Moteur", provider_cards_widget))
    provider_grid = QGridLayout()
    provider_grid.setVerticalSpacing(12)
    provider_grid.setHorizontalSpacing(14)
    provider_grid.setColumnStretch(1, 1)
    engine_layout.addLayout(provider_grid)
    ai_model_label = QLabel("Modèle OpenAI")
    provider_grid.addWidget(ai_model_label, 0, 0)
    ai_model_input = QComboBox()
    ai_model_input.setEditable(True)
    ai_model_input.addItems(list(AI_MODEL_OPTIONS))
    set_combo_value_by_text(ai_model_input, settings.ai_model)
    provider_grid.addWidget(ai_model_input, 0, 1)
    openai_key_label = QLabel("Clé API OpenAI")
    provider_grid.addWidget(openai_key_label, 1, 0)
    key_fields = QWidget()
    openai_key_layout = QHBoxLayout(key_fields)
    openai_key_layout.setContentsMargins(0, 0, 0, 0)
    openai_key_input = QLineEdit()
    openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    openai_key_input.setPlaceholderText("Coller une nouvelle clé puis enregistrer")
    save_openai_key_button = QPushButton(UI_TEXT["save_openai_key"])
    openai_key_layout.addWidget(openai_key_input)
    openai_key_layout.addWidget(save_openai_key_button)
    provider_grid.addWidget(key_fields, 1, 1)
    ollama_model_label = QLabel("Modèle local installé")
    provider_grid.addWidget(ollama_model_label, 2, 0)
    ollama_models_widget = QWidget()
    ollama_models_layout = QHBoxLayout(ollama_models_widget)
    ollama_models_layout.setContentsMargins(0, 0, 0, 0)
    ollama_model_input = QComboBox()
    ollama_model_input.setEditable(True)
    ollama_model_input.addItem(settings.ollama_model)
    ollama_models_layout.addWidget(ollama_model_input, 1)
    refresh_ollama_models_button = QPushButton("Actualiser les modèles")
    ollama_models_layout.addWidget(refresh_ollama_models_button)
    provider_grid.addWidget(ollama_models_widget, 2, 1)
    ollama_url_label = QLabel("Adresse Ollama sur ce PC")
    provider_grid.addWidget(ollama_url_label, 3, 0)
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
    provider_grid.addWidget(ollama_address_widget, 3, 1)
    jev_model_label = QLabel("Modèle Jev")
    provider_grid.addWidget(jev_model_label, 4, 0)
    jev_model_input = QComboBox()
    jev_model_input.setEditable(True)
    jev_model_input.addItems(list(JEV_MODEL_OPTIONS))
    set_combo_value_by_text(jev_model_input, settings.jev_model)
    jev_model_input.setToolTip(
        "jev-latest suit la dernière version de Jev. Le test affiche le modèle qui a "
        "répondu : saisir ce nom fige la version."
    )
    provider_grid.addWidget(jev_model_input, 4, 1)
    jev_key_label = QLabel("Clé API Jev (TypeSafe)")
    provider_grid.addWidget(jev_key_label, 5, 0)
    jev_key_fields = QWidget()
    jev_key_layout = QHBoxLayout(jev_key_fields)
    jev_key_layout.setContentsMargins(0, 0, 0, 0)
    jev_key_input = QLineEdit()
    jev_key_input.setEchoMode(QLineEdit.EchoMode.Password)
    jev_key_input.setPlaceholderText("Coller la clé de console.typesafe.ai puis enregistrer")
    save_jev_key_button = QPushButton(UI_TEXT["save_openai_key"])
    jev_key_layout.addWidget(jev_key_input)
    jev_key_layout.addWidget(save_jev_key_button)
    provider_grid.addWidget(jev_key_fields, 5, 1)
    ai_provider_hint = QLabel()
    ai_provider_hint.setWordWrap(True)
    ai_provider_hint.setProperty("role", "muted")
    engine_layout.addWidget(ai_provider_hint)

    _privacy_section, privacy_layout = settings_section()
    ai_include_body_checkbox = QCheckBox("Inclure l'extrait nettoyé du corps dans l'analyse IA")
    ai_include_body_checkbox.setChecked(settings.ai_include_body_excerpt)
    privacy_layout.addWidget(ai_include_body_checkbox)
    privacy_layout.addWidget(text_label(
        "Sinon : sujet, métadonnées et noms des pièces jointes seulement.", "small", wrap=True
    ))
    privacy_phone_checkbox = QCheckBox("Masquer les numéros de téléphone avant l'analyse IA")
    privacy_phone_checkbox.setChecked(settings.privacy_mask_phone_numbers)
    privacy_layout.addWidget(privacy_phone_checkbox)
    privacy_layout.addWidget(text_label(
        "Les clés OpenAI et Jev restent dans le coffre du système, jamais dans le fichier "
        "de réglages.",
        "small",
        wrap=True,
    ))

    _threshold_section, threshold_layout = settings_section()
    threshold_slider = QSlider(Qt.Orientation.Horizontal)
    threshold_minimum = round(REVIEW_CONFIDENCE_THRESHOLD * 100)
    threshold_slider.setRange(threshold_minimum, 99)
    threshold_slider.setValue(
        min(max(round(settings.decision_confidence_threshold * 100), threshold_minimum), 99)
    )
    threshold_slider.setAccessibleName("Seuil de vérification")
    threshold_value_label = QLabel()
    threshold_value_label.setStyleSheet("QLabel { font-weight: 700; }")
    threshold_value_label.setFixedWidth(48)
    threshold_layout.addWidget(field_block(
        "Seuil de vérification", row_layout(threshold_slider, threshold_value_label, spacing=12)
    ))
    threshold_layout.addWidget(text_label(
        f"En dessous de ce seuil, un mail reste à vérifier. Le minimum est "
        f"{threshold_minimum} % : l'IA n'archive jamais un mail moins sûr.",
        "small",
        wrap=True,
    ))

    _folders_section, folders_layout = settings_section()
    projects_root_input = QLineEdit(str(settings.local_projects_root))
    projects_root_picker = QWidget()
    projects_root_layout = QHBoxLayout(projects_root_picker)
    projects_root_layout.setContentsMargins(0, 0, 0, 0)
    projects_root_layout.addWidget(projects_root_input)
    browse_projects_button = QPushButton("Parcourir")
    projects_root_layout.addWidget(browse_projects_button)
    folders_layout.addWidget(field_block("Dossier local des projets", projects_root_picker))
    projectflow_widget = QWidget()
    projectflow_layout = QVBoxLayout(projectflow_widget)
    projectflow_layout.setContentsMargins(0, 0, 0, 0)
    projectflow_picker = QWidget()
    projectflow_picker_layout = QHBoxLayout(projectflow_picker)
    projectflow_picker_layout.setContentsMargins(0, 0, 0, 0)
    projectflow_input = QLineEdit(settings.projectflow_executable)
    projectflow_input.setPlaceholderText("Détection automatique du programme installé")
    projectflow_input.setAccessibleName("Programme ProjectFlow Automator")
    browse_projectflow_button = QPushButton("Parcourir")
    projectflow_picker_layout.addWidget(projectflow_input)
    projectflow_picker_layout.addWidget(browse_projectflow_button)
    projectflow_status = QLabel()
    projectflow_status.setWordWrap(True)
    projectflow_status.setProperty("role", "muted")
    projectflow_layout.addWidget(projectflow_picker)
    projectflow_layout.addWidget(projectflow_status)
    folders_layout.addWidget(field_block("ProjectFlow Automator", projectflow_widget))

    _watch_section, watch_layout = settings_section()
    review_reminder_times_input = QLineEdit(format_reminder_times(settings.review_reminder_times))
    review_reminder_times_input.setPlaceholderText("09:00, 14:00, 16:30")
    watch_layout.addWidget(field_block("Horaires des rappels", review_reminder_times_input))
    watch_layout.addWidget(text_label(
        "Quand la surveillance Outlook est active, MailFlow contrôle les dossiers projet "
        "toutes les 5 minutes et rappelle à ces heures les mails qui attendent une "
        "vérification.",
        "small",
        wrap=True,
    ))

    _updates_section, updates_layout = settings_section()
    update_widget = QWidget()
    update_layout = QHBoxLayout(update_widget)
    update_layout.setContentsMargins(0, 0, 0, 0)
    check_updates_button = QPushButton(UI_TEXT["check_updates"])
    update_status = QLabel(f"Version {__version__}")
    update_status.setStyleSheet(f"QLabel {{ color: {COLORS['muted']}; }}")
    update_layout.addWidget(update_status)
    update_layout.addStretch(1)
    update_layout.addWidget(check_updates_button)
    updates_frame = BlueprintFrame(padding=8)
    updates_frame.body.addWidget(kicker_label("Application"))
    updates_frame.body.addWidget(update_widget)
    updates_layout.addWidget(updates_frame)

    settings_scroll_area = QScrollArea()
    settings_scroll_area.setProperty("pane", "plain")
    settings_scroll_area.setWidgetResizable(True)
    settings_scroll_area.setWidget(settings_page)
    settings_scroll_area.setMinimumWidth(300)

    settings_decision, settings_decision_layout = column(
        "decision", width=340, margins=(22, 22, 22, 12), spacing=12
    )
    settings_decision_layout.addWidget(kicker_label("Test du moteur"))
    settings_decision_layout.addWidget(text_label(
        "Classe un mail fictif pour vérifier la clé et la connexion.", None, wrap=True
    ))
    test_openai_key_button = QPushButton(UI_TEXT["test_openai_key"])
    openai_key_status = QLabel()
    openai_key_status.setWordWrap(True)
    openai_test_group = QWidget()
    openai_test_layout = QVBoxLayout(openai_test_group)
    openai_test_layout.setContentsMargins(0, 0, 0, 0)
    openai_test_layout.addWidget(test_openai_key_button)
    openai_test_layout.addWidget(openai_key_status)
    test_jev_key_button = QPushButton(UI_TEXT["test_jev_key"])
    test_jev_key_button.setToolTip("Classer un mail fictif avec Jev pour vérifier la clé.")
    jev_key_status = QLabel()
    jev_key_status.setWordWrap(True)
    jev_test_group = QWidget()
    jev_test_layout = QVBoxLayout(jev_test_group)
    jev_test_layout.setContentsMargins(0, 0, 0, 0)
    jev_test_layout.addWidget(test_jev_key_button)
    jev_test_layout.addWidget(jev_key_status)
    ollama_test_widget = QWidget()
    ollama_test_layout = QVBoxLayout(ollama_test_widget)
    ollama_test_layout.setContentsMargins(0, 0, 0, 0)
    test_ollama_button = QPushButton("Tester IA locale")
    test_ollama_button.setToolTip("Classer un mail fictif pour vérifier Ollama et le modèle.")
    ollama_test_layout.addWidget(test_ollama_button)
    ollama_status = QLabel("Connexion locale à tester.")
    ollama_status.setWordWrap(True)
    ollama_test_layout.addWidget(ollama_status)
    for widget in (openai_test_group, jev_test_group, ollama_test_widget):
        settings_decision_layout.addWidget(widget)
    settings_decision_layout.addStretch(1)
    save_settings_button = BlueprintButton(UI_TEXT["save_settings"])
    settings_decision_layout.addWidget(save_settings_button)
    settings_container_layout.addWidget(settings_sections_pane)
    settings_container_layout.addWidget(settings_scroll_area, 1)
    settings_container_layout.addWidget(settings_decision)
    pages.addWidget(settings_container)

    # ── Inspector of the table view, and the activity log under every screen. ──
    preview = QFrame()
    preview.setProperty("pane", "detail")
    preview.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    preview.setMinimumWidth(320)
    preview_layout = QVBoxLayout(preview)
    preview_layout.setContentsMargins(16, 16, 16, 12)
    preview_layout.setSpacing(8)
    preview_layout.addWidget(kicker_label("Comprendre le classement"))
    preview_tabs = QTabWidget()
    project_digest_preview = QTextEdit()
    project_digest_preview.setReadOnly(True)
    project_digest_preview.setMinimumHeight(160)
    project_digest_preview.setHtml(project_digest_to_html(build_project_digest([])))
    mail_preview = QTextEdit()
    mail_preview.setReadOnly(True)
    mail_preview.setMinimumWidth(280)
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
    logs_panel = QFrame()
    logs_panel.setProperty("pane", "footer")
    logs_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    logs_layout = QVBoxLayout(logs_panel)
    logs_layout.setContentsMargins(12, 2, 20, 4)
    logs_layout.setSpacing(2)
    logs_header = QWidget()
    logs_header_layout = QHBoxLayout(logs_header)
    logs_header_layout.setContentsMargins(0, 0, 0, 0)
    logs_toggle = QToolButton()
    logs_toggle.setAutoRaise(True)
    logs_toggle.setStyleSheet("QToolButton { border: none; padding: 2px; }")
    logs_toggle.setToolTip("Afficher ou masquer le journal d'activité")
    logs_toggle.setArrowType(Qt.ArrowType.RightArrow)
    logs_label = text_label("Journal d'activité", "small")
    logs_header_layout.addWidget(logs_toggle)
    logs_header_layout.addWidget(logs_label)
    logs_header_layout.addStretch(1)
    logs_header_layout.addWidget(scan_status_label, 1)
    logs_layout.addWidget(logs_header)
    logs_layout.addWidget(logs)
    logs_panel.setMaximumHeight(34)

    def toggle_logs() -> None:
        logs.setVisible(not logs.isVisible())
        logs_toggle.setArrowType(
            Qt.ArrowType.DownArrow if logs.isVisible() else Qt.ArrowType.RightArrow
        )
        logs_panel.setMaximumHeight(200 if logs.isVisible() else 34)

    logs_toggle.clicked.connect(toggle_logs)

    workspace_splitter.addWidget(pages)
    workspace_splitter.addWidget(preview)
    workspace_splitter.setStretchFactor(0, 5)
    workspace_splitter.setStretchFactor(1, 2)
    workspace_splitter.setSizes([900, 380])
    right_column = QWidget()
    right_layout = QVBoxLayout(right_column)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(0)
    right_layout.addWidget(top_bar)
    right_layout.addWidget(workspace_splitter, 1)
    right_layout.addWidget(logs_panel)
    content_splitter.addWidget(right_column)
    content_splitter.setStretchFactor(0, 0)
    content_splitter.setStretchFactor(1, 1)
    layout.addWidget(content_splitter, 1)

    def update_inspector_visibility(*_args: Any) -> None:
        preview.setVisible(
            navigation.currentRow() == 0
            and mail_views.currentIndex() == 1
            and inspector_action.isChecked()
        )

    def set_mail_view(mode: object) -> None:
        table_mode = mode == "table"
        (table_toggle_host if table_mode else queue_toggle_host).addWidget(mail_view_toggle)
        mail_view_toggle.set_value("table" if table_mode else "queue")
        mail_views.setCurrentIndex(1 if table_mode else 0)
        update_inspector_visibility()

    navigation.currentRowChanged.connect(pages.setCurrentIndex)
    navigation.currentRowChanged.connect(update_inspector_visibility)
    inspector_action.toggled.connect(update_inspector_visibility)
    mail_view_toggle.changed.connect(set_mail_view)
    set_mail_view("queue")

    def show_detail_columns(checked: bool) -> None:
        for column_index in (0, 1, 2, TYPE_COLUMN, INTERLOCUTOR_COLUMN, 8):
            table.setColumnHidden(column_index, not checked)

    detail_columns_action.toggled.connect(show_detail_columns)
    empty_settings_button.clicked.connect(lambda: navigation.setCurrentRow(SETTINGS_PAGE))
    queue_empty_settings_button.clicked.connect(
        lambda: navigation.setCurrentRow(SETTINGS_PAGE)
    )
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
            color = COLORS["success"]
        elif success is False:
            color = COLORS["danger"]
        else:
            color = COLORS["muted"]
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

    def has_jev_api_key() -> bool:
        return get_jev_api_key() is not None

    def set_jev_key_status(
        *,
        has_key: bool,
        valid: bool | None = None,
        testing: bool = False,
    ) -> None:
        jev_key_status.setText(
            openai_key_status_text(has_key=has_key, valid=valid, testing=testing)
        )
        jev_key_status.setStyleSheet(
            openai_key_status_style(has_key=has_key, valid=valid, testing=testing)
        )

    def update_jev_key_status(*, valid: bool | None = None) -> None:
        set_jev_key_status(has_key=has_jev_api_key(), valid=valid)

    settings_section_infos = (
        (
            "Moteur IA",
            "Enregistrez pour appliquer les choix IA. Une panne d'un moteur ne bascule "
            "jamais vers un autre.",
        ),
        ("Confidentialité", "Ce qui est envoyé au moteur IA pour classer un mail."),
        ("Seuils", "La confiance minimale pour archiver un mail sans vérification."),
        ("Dossiers", "Le dossier local sera utilisé au prochain scan."),
        ("Surveillance", "Les rappels de la file de vérification."),
        ("Mises à jour", "Recherche et installation des nouvelles versions de MailFlow."),
    )
    settings_section_subtitles: list[Any] = []
    for section_title, _section_hint in settings_section_infos:
        section_item = QListWidgetItem()
        section_widget = QWidget()
        section_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        section_widget_layout = QVBoxLayout(section_widget)
        section_widget_layout.setContentsMargins(16, 11, 16, 11)
        section_widget_layout.setSpacing(1)
        section_title_label = QLabel(section_title)
        section_title_label.setStyleSheet("QLabel { font-weight: 600; }")
        section_subtitle_label = text_label("", "small")
        section_widget_layout.addWidget(section_title_label)
        section_widget_layout.addWidget(section_subtitle_label)
        section_item.setSizeHint(QSize(0, section_widget.sizeHint().height()))
        settings_sections_list.addItem(section_item)
        settings_sections_list.setItemWidget(section_item, section_widget)
        settings_section_subtitles.append(section_subtitle_label)

    def settings_section_subtitle(index: int) -> str:
        if index == 0:
            label = engine_label(str(ai_provider_combo.currentData()))
            if ai_mode_combo.currentData() == AiMode.DISABLED.value:
                return f"{label} · IA désactivée"
            return "Jev (TypeSafe)" if label == "Jev" else label
        if index == 1:
            return (
                "Corps du mail inclus"
                if ai_include_body_checkbox.isChecked()
                else "Sujet et métadonnées seulement"
            )
        if index == 2:
            return f"Vérification {threshold_slider.value()} %"
        if index == 3:
            root = projects_root_input.text().strip()
            return root if len(root) <= 38 else f"…{root[-37:]}"
        if index == 4:
            times = review_reminder_times_input.text().strip()
            return f"Rappels {times}" if times else "Aucun rappel"
        return f"Version {__version__}"

    def refresh_settings_subtitles(*_args: Any) -> None:
        for index, subtitle_label in enumerate(settings_section_subtitles):
            subtitle_label.setText(settings_section_subtitle(index))
        threshold_value_label.setText(f"{threshold_slider.value()} %")

    def show_settings_section(index: int) -> None:
        if not 0 <= index < len(settings_section_infos):
            return
        settings_sections_stack.setCurrentIndex(index)
        title, hint = settings_section_infos[index]
        settings_heading.setText(title)
        settings_hint.setText(hint)

    def sync_engine_choices(*_args: Any) -> None:
        provider = str(ai_provider_combo.currentData())
        for provider_key, (card, card_kicker_label, card_kicker) in provider_cards.items():
            selected = provider_key == provider
            card.setChecked(selected)
            set_kicker_text(card_kicker_label, "Sélectionné" if selected else card_kicker)
        ai_mode_choice.set_value(str(ai_mode_combo.currentData()))
        refresh_settings_subtitles()

    for provider_key, (card, _card_kicker_label, _card_kicker) in provider_cards.items():
        card.clicked.connect(
            lambda _checked=False, key=provider_key: set_combo_value_by_data(
                ai_provider_combo, key
            )
        )
    ai_mode_choice.changed.connect(
        lambda value: set_combo_value_by_data(ai_mode_combo, str(value))
    )
    ai_mode_combo.currentIndexChanged.connect(sync_engine_choices)
    threshold_slider.valueChanged.connect(refresh_settings_subtitles)
    ai_include_body_checkbox.toggled.connect(refresh_settings_subtitles)
    projects_root_input.textChanged.connect(refresh_settings_subtitles)
    review_reminder_times_input.textChanged.connect(refresh_settings_subtitles)
    settings_sections_list.currentRowChanged.connect(show_settings_section)
    settings_sections_list.setCurrentRow(0)

    def update_ai_provider_fields() -> None:
        provider = str(ai_provider_combo.currentData())
        widgets_by_provider: dict[str, tuple[Any, ...]] = {
            "openai": (
                ai_model_label, ai_model_input, openai_key_label, key_fields, openai_test_group,
            ),
            "ollama": (
                ollama_model_label, ollama_models_widget, ollama_url_label,
                ollama_address_widget, ollama_test_widget,
            ),
            "jev": (
                jev_model_label, jev_model_input, jev_key_label, jev_key_fields, jev_test_group,
            ),
        }
        for widget_provider, widgets in widgets_by_provider.items():
            for widget in widgets:
                widget.setVisible(widget_provider == provider)
                widget.setEnabled(widget_provider == provider)
        ai_provider_hint.setText(ai_provider_hint_text(provider))
        if provider == "openai":
            update_openai_key_status()
        elif provider == "jev":
            update_jev_key_status()
        sync_engine_choices()

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
        for side_archive_button in (queue_archive_button, tree_archive_button):
            side_archive_button.setText(archive_button_text(ready_count))
            side_archive_button.setEnabled(ready_count > 0 and not operation_in_progress)
        editable_count = sum(
            active_controller.preview_rows[index].action != PreviewAction.ARCHIVED
            for index in selected
        )
        review_button.setEnabled(editable_count > 0 and not operation_in_progress)
        review_button.setText(
            f"Vérifier les {editable_count} mails" if editable_count > 1
            else "Vérifier la sélection"
        )
        ignore_action.setEnabled(bool(selected) and not operation_in_progress)
        has_rows = bool(active_controller.preview_rows) and not operation_in_progress
        reclassify_action.setEnabled(has_rows)
        refresh_roles_action.setEnabled(has_rows)
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
                        item.setBackground(QColor(COLORS["warning_bg"]))
                    if column_index == 9:
                        foreground, background = {
                            PreviewAction.ARCHIVE: (COLORS["accent_800"], COLORS["accent_100"]),
                            PreviewAction.REVIEW: (COLORS["warning"], COLORS["warning_bg"]),
                            PreviewAction.IGNORE: (COLORS["neutral_800"], COLORS["neutral_100"]),
                            PreviewAction.ARCHIVED: (COLORS["accent_700"], COLORS["bg"]),
                        }[row.action]
                        item.setForeground(QColor(foreground))
                        item.setBackground(QColor(background))
                    item.setToolTip(value)
                    table.setItem(row_index, column_index, item)
                    continue
                combo = TableComboBox()
                combo.setEnabled(row.action != PreviewAction.ARCHIVED)
                combo.addItems(list(options))
                if value and value not in options:
                    combo.addItem(value)
                if value in options:
                    combo.setCurrentText(value)
                elif value:
                    combo.setCurrentText(value)
                if should_highlight_cell(row, column_index):
                    combo.setStyleSheet(
                        f"QComboBox {{ background-color: {COLORS['warning_bg']}; }}"
                    )
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
        refresh_review_queue(preferred_entry_id)

    def refresh_project_digest() -> None:
        project_digest_preview.setHtml(
            project_digest_to_html(build_project_digest(active_controller.preview_rows))
        )

    def refresh_folder_tree() -> None:
        nonlocal preferred_folder_path
        selected_path = preferred_folder_path or selected_folder_path()
        preferred_folder_path = None
        duplicates = {hint.source for hint in current_duplicates()}
        folder_tree.blockSignals(True)
        try:
            folder_tree.clear()
            for node in active_controller.folder_tree():
                folder_tree.addTopLevelItem(folder_tree_item(node))
            folder_tree.expandAll()
            for item in tree_items():
                path = str(item.data(0, Qt.ItemDataRole.UserRole))
                tag_text = "doublon" if path in duplicates else folder_role_tag(path)
                if tag_text:
                    tag_holder = QWidget()
                    tag_holder_layout = QHBoxLayout(tag_holder)
                    tag_holder_layout.setContentsMargins(0, 0, 6, 0)
                    tag_holder_layout.addWidget(
                        tag_label(tag_text, "outline" if path in duplicates else "neutral")
                    )
                    folder_tree.setItemWidget(item, 1, tag_holder)
                if path == selected_path:
                    folder_tree.setCurrentItem(item)
        finally:
            folder_tree.blockSignals(False)
        tree_hint.setText(tree_header_text(active_controller.preview_rows))
        update_folder_panels()

    def folder_tree_item(node: Any) -> Any:
        item = QTreeWidgetItem([str(node.name), "", str(node.mail_count)])
        item.setData(0, Qt.ItemDataRole.UserRole, str(node.relative_folder))
        item.setIcon(0, folder_icon)
        item.setForeground(2, QColor(COLORS["muted"]))
        item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if node.children:
            font = item.font(0)
            font.setWeight(QFont.Weight.DemiBold)
            item.setFont(0, font)
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

    def browse_projectflow() -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            window,
            "Sélectionner ProjectFlow Automator",
            projectflow_input.text().strip() or str(Path.home()),
            "Programme (*.exe);;Tous les fichiers (*)",
        )
        if selected:
            projectflow_input.setText(selected)
            refresh_projectflow_status()

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

    def current_role_suggestions() -> dict[int, Any]:
        provider = getattr(active_controller, "directory_role_suggestions", None)
        if not callable(provider):
            return {}
        try:
            return dict(provider())
        except Exception as exc:
            append_log(f"Suggestions de role indisponibles: {exc}")
            return {}

    def restore_directory_scroll(vertical: int, horizontal: int) -> None:
        # The suggestion cells know their final width once the theme font is applied.
        # Cell widgets sit inside the item padding of the theme (7 px each side + grid).
        hint = directory_table.sizeHintForColumn(DIRECTORY_SUGGESTION_COLUMN)
        if hint > 0:
            directory_table.setColumnWidth(DIRECTORY_SUGGESTION_COLUMN, hint + 16)
        directory_table.verticalScrollBar().setValue(vertical)
        directory_table.horizontalScrollBar().setValue(horizontal)

    def refresh_directory_table() -> None:
        nonlocal refreshing_directory_table
        # Rebuilding the rows must not move the list: keep the scroll and the selection.
        vertical_scroll = directory_table.verticalScrollBar().value()
        horizontal_scroll = directory_table.horizontalScrollBar().value()
        selected = selected_directory_organization()
        focus = QApplication.focusWidget()
        had_focus = focus is not None and directory_table.isAncestorOf(focus)
        refreshing_directory_table = True
        try:
            entries = active_controller.directory_entries()
        except Exception as exc:
            refreshing_directory_table = False
            directory_table.setRowCount(0)
            directory_status_label.setText(f"Annuaire indisponible: {exc}")
            return
        suggestions = current_role_suggestions()
        directory_entries_cache[:] = entries
        directory_suggestions_cache.clear()
        directory_suggestions_cache.update(suggestions)
        directory_table.clearContents()
        for row_index in range(directory_table.rowCount()):
            directory_table.removeCellWidget(row_index, DIRECTORY_ROLE_COLUMN)
            directory_table.removeCellWidget(row_index, DIRECTORY_SUGGESTION_COLUMN)
        directory_table.setRowCount(len(entries))
        directory_table.setHorizontalHeaderLabels(list(DIRECTORY_COLUMNS))
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
                DIRECTORY_ROLE_COLUMN,
                global_role_combo(organization_id, role),
            )
            suggestion = suggestions.get(organization_id)
            if suggestion is not None and role not in {
                InterlocutorType.CLIENT, InterlocutorType.FOURNISSEUR,
            }:
                directory_table.setCellWidget(
                    row_index,
                    DIRECTORY_SUGGESTION_COLUMN,
                    role_suggestion_cell(organization_id, name, suggestion),
                )
            else:
                directory_table.setItem(
                    row_index, DIRECTORY_SUGGESTION_COLUMN, QTableWidgetItem("")
                )
            directory_table.setItem(row_index, DIRECTORY_PROJECTS_COLUMN, project_item)
            if selected is not None and selected[0] == organization_id:
                directory_table.selectRow(row_index)
        directory_table.resizeRowsToContents()
        refreshing_directory_table = False
        if had_focus:
            directory_table.setFocus(Qt.FocusReason.OtherFocusReason)
        confident = sum(
            1 for suggestion in suggestions.values() if getattr(suggestion, "is_confident", False)
        )
        validate_suggestions_button.setEnabled(confident > 0)
        validate_suggestions_button.setText(
            f"{UI_TEXT['validate_role_suggestions']} ({confident})"
            if confident else UI_TEXT["validate_role_suggestions"]
        )
        restore_directory_scroll(vertical_scroll, horizontal_scroll)
        # The scroll range can be recomputed after this call returns; restore it again.
        QTimer.singleShot(
            0, lambda: restore_directory_scroll(vertical_scroll, horizontal_scroll)
        )
        directory_status_label.setText(
            f"{len(entries)} entreprise(s) dans l'annuaire global."
        )
        refresh_directory_list()

    def role_suggestion_cell(organization_id: int, name: str, suggestion: Any) -> Any:
        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(6, 2, 4, 2)
        cell_layout.setSpacing(8)
        label = QLabel(str(suggestion.label))
        label.setToolTip(
            "Estimation de Jev sur les mails analysés de cette entreprise. "
            "Rien n'est enregistré sans votre validation."
        )
        cell_layout.addWidget(label, 1)
        role = suggestion.role
        if role is not None:
            button = QPushButton("Valider")
            button.setStyleSheet("QPushButton { padding: 3px 10px; }")
            button.setToolTip(f"Enregistrer {name} comme {role.value} dans l'annuaire.")
            button.clicked.connect(
                lambda _checked=False: validate_role_suggestion(organization_id, name, role)
            )
            cell_layout.addWidget(button)
        return cell

    def mail_states() -> dict[str, tuple[Any, str]]:
        return {
            row.mail.entry_id: (row.action, row.decision.target_relative_folder)
            for row in active_controller.preview_rows
        }

    def report_role_update(before: dict[str, tuple[Any, str]]) -> None:
        rows = active_controller.preview_rows
        changed = sum(
            1 for row in rows
            if before.get(row.mail.entry_id) != (row.action, row.decision.target_relative_folder)
        )
        ready = sum(1 for row in rows if row.action == PreviewAction.ARCHIVE)
        message = (
            f"Rôles appliqués sans nouvel appel IA : {changed} mail(s) mis à jour, "
            f"{ready} prêt(s) à archiver."
        )
        directory_status_label.setText(message)
        set_scan_status(message, success=True)
        append_log(message)

    def validate_role_suggestion(organization_id: int, name: str, role: Any) -> None:
        if operation_in_progress:
            return
        before = mail_states()
        try:
            active_controller.set_directory_organization_role(organization_id, role)
        except Exception as exc:
            append_log(f"Erreur role global: {exc}")
            return
        refresh_table()
        refresh_directory_table()
        update_mail_preview(table.currentRow())
        append_log(f"Role suggere valide: {name} enregistre comme {role.value}.")
        report_role_update(before)

    def refresh_roles_without_ai() -> None:
        if operation_in_progress:
            return
        refresher = getattr(active_controller, "refresh_directory_roles", None)
        if not callable(refresher):
            return
        before = mail_states()
        try:
            refresher()
        except Exception as exc:
            append_log(f"Actualisation des roles impossible: {exc}")
            return
        refresh_table()
        refresh_directory_table()
        update_mail_preview(table.currentRow())
        report_role_update(before)

    def validate_confident_role_suggestions() -> None:
        if operation_in_progress:
            return
        confident = {
            organization_id: suggestion
            for organization_id, suggestion in current_role_suggestions().items()
            if suggestion.is_confident
        }
        if not confident:
            directory_status_label.setText("Aucune suggestion sûre à valider.")
            return
        try:
            names = {
                int(entry.organization_id): str(entry.name)
                for entry in active_controller.directory_entries()
            }
        except Exception as exc:
            append_log(f"Annuaire indisponible: {exc}")
            return
        lines = [
            f"• {names.get(organization_id, f'#{organization_id}')} → {suggestion.label}"
            for organization_id, suggestion in sorted(
                confident.items(),
                key=lambda item: names.get(item[0], "").casefold(),
            )
        ]
        shown = "\n".join(lines[:15])
        if len(lines) > 15:
            shown += f"\n… et {len(lines) - 15} autre(s)"
        answer = QMessageBox.question(
            window,
            "Valider les rôles suggérés",
            f"Enregistrer ces {len(confident)} rôle(s) dans l'annuaire ?\n\n{shown}\n\n"
            "Les mails de ces entreprises seront mis à jour aussitôt, sans nouvel appel "
            "à l'IA.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = mail_states()
        try:
            active_controller.set_directory_organization_roles(
                {
                    organization_id: suggestion.role
                    for organization_id, suggestion in confident.items()
                }
            )
        except Exception as exc:
            append_log(f"Erreur role global: {exc}")
            refresh_directory_table()
            return
        append_log(f"{len(confident)} role(s) suggere(s) valide(s) dans l'annuaire.")
        refresh_table()
        refresh_directory_table()
        update_mail_preview(table.currentRow())
        report_role_update(before)

    def global_role_combo(organization_id: int, role: Any) -> Any:
        combo = TableComboBox()
        for item in InterlocutorType:
            combo.addItem(interlocutor_label(item), item.value)
        current_role = role if isinstance(role, InterlocutorType) else InterlocutorType.INCONNU
        set_combo_value_by_data(combo, current_role.value)

        def role_changed(_value: str = "") -> None:
            if refreshing_directory_table:
                return
            selected_role = InterlocutorType(str(combo.currentData()))
            before = mail_states()
            try:
                active_controller.set_directory_organization_role(
                    organization_id,
                    selected_role,
                )
            except Exception as exc:
                append_log(f"Erreur role global: {exc}")
                refresh_directory_table()
                return
            refresh_table()
            refresh_directory_table()
            update_mail_preview(table.currentRow())
            append_log(
                "Role global applique: "
                f"{selected_role.value} pour l'entreprise #{organization_id}."
            )
            report_role_update(before)

        combo.currentTextChanged.connect(role_changed)
        return combo

    def selected_directory_organization() -> tuple[int, str] | None:
        current_entry = directory_entry(directory_current_id)
        if current_entry is not None:
            return int(current_entry.organization_id), str(current_entry.name)
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
        if update is not None:
            commit_manual_update(row_index, update)
        refresh_table(preferred_row_index=row_index)

    def open_bulk_manual_dialog(row_indexes: Sequence[int]) -> None:
        if refreshing_table or operation_in_progress:
            return
        editable = [
            index for index in row_indexes
            if 0 <= index < len(active_controller.preview_rows)
            and active_controller.preview_rows[index].action != PreviewAction.ARCHIVED
        ]
        if len(editable) <= 1:
            if editable:
                open_manual_dialog(editable[0])
            return
        update = ask_manual_classification(editable[0], selection_count=len(editable))
        if update is not None:
            commit_bulk_update(editable, update)
        refresh_table(preferred_row_index=editable[0])

    def ask_manual_classification(
        row_index: int,
        *,
        selection_count: int = 1,
    ) -> ManualClassificationUpdate | None:
        if row_index < 0 or row_index >= len(active_controller.preview_rows):
            return None
        row = active_controller.preview_rows[row_index]
        dialog = QDialog(window)
        dialog.setWindowTitle(
            "Classement manuel"
            if selection_count == 1
            else f"Classement manuel de {selection_count} mails"
        )
        dialog.setMinimumWidth(560)
        form = QFormLayout(dialog)
        if selection_count > 1:
            bulk_note = QLabel(
                f"Ce classement sera appliqué aux {selection_count} mails sélectionnés. "
                "Chaque mail garde le dossier de sa propre entreprise ; le mail "
                "ci-dessous sert d'exemple."
            )
            bulk_note.setWordWrap(True)
            bulk_note.setStyleSheet("QLabel { font-weight: 600; }")
            form.addRow(bulk_note)

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
            "Un classement complet devient un exemple vérifié pour les prochaines "
            "analyses. Choisir Client ou Fournisseur enregistre aussi ce rôle pour "
            "l'entreprise dans l'Annuaire et l'applique à ses autres mails."
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
            "jev_model": clean_optional_text(jev_model_input.currentText()) or DEFAULT_JEV_MODEL,
            "ai_include_body_excerpt": ai_include_body_checkbox.isChecked(),
            "privacy_mask_phone_numbers": privacy_phone_checkbox.isChecked(),
            "review_reminder_times": reminder_times,
            "projectflow_executable": projectflow_input.text().strip().strip('"'),
            "decision_confidence_threshold": threshold_slider.value() / 100,
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

    def save_jev_key_from_input() -> None:
        if operation_in_progress or ai_provider_combo.currentData() != "jev":
            return
        api_key = clean_optional_text(jev_key_input.text())
        if api_key is None:
            append_log("Aucune nouvelle cle Jev a enregistrer.")
            return
        try:
            set_jev_api_key(api_key)
            apply_current_ai_settings()
            jev_key_input.clear()
            update_jev_key_status(valid=None)
            update_mailbox_jev_option()
            append_log("Cle Jev enregistree dans le coffre du systeme.")
        except Exception as exc:
            append_log(f"Erreur enregistrement cle Jev: {exc}")

    @exclusive_operation
    def test_jev_key_from_input() -> None:
        if ai_provider_combo.currentData() != "jev":
            return
        api_key = clean_optional_text(jev_key_input.text()) or get_jev_api_key()
        if api_key is None:
            set_jev_key_status(has_key=False, valid=False)
            append_log("Aucune cle Jev a tester.")
            return
        model = clean_optional_text(jev_model_input.currentText()) or DEFAULT_JEV_MODEL
        set_jev_key_status(has_key=True, testing=True)
        QApplication.processEvents()
        try:
            result = run_with_event_loop(
                JevClassifier(
                    api_key=api_key, model=model,
                    timeout_seconds=settings.jev_timeout_seconds,
                ).check_connection
            )
        except Exception:
            set_jev_key_status(has_key=True, valid=False)
            append_log("Test Jev impossible.")
            return
        set_jev_key_status(has_key=True, valid=result.ok)
        append_log(f"Test Jev : {result.message}")

    def ollama_classifier_from_input() -> OllamaClassifier:
        return OllamaClassifier(
            base_url=clean_optional_text(ollama_base_url_input.text()) or DEFAULT_OLLAMA_BASE_URL,
            model=clean_optional_text(ollama_model_input.currentText()) or DEFAULT_OLLAMA_MODEL,
            timeout_seconds=ollama_timeout_input.value(),
        )

    def set_ollama_status(message: str, *, success: bool | None = None) -> None:
        color = (
            COLORS["success"] if success is True
            else COLORS["danger"] if success is False
            else COLORS["muted"]
        )
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
            color = COLORS["success"]
        elif success is False:
            color = COLORS["danger"]
        else:
            color = COLORS["muted"]
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
        if settings.ai_mode != AiMode.DISABLED:
            if settings.ai_provider == "openai" and not has_openai_api_key():
                append_log("Mode IA actif sans cle OpenAI: les lignes resteront a verifier.")
            elif settings.ai_provider == "jev" and not has_jev_api_key():
                append_log("Mode IA actif sans cle Jev: les lignes resteront a verifier.")
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
            label = str(option.folder_name)
            if getattr(option, "archived", False):
                label += " — archives"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(option.project_number))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = project_folder_selected_by_default(
                str(option.project_number),
                preferred,
                archived=bool(getattr(option, "archived", False)),
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

    mailbox_row_entry_ids: list[str] = []
    projectflow_installation = ProjectFlowInstallation(None)
    mailbox_analysis_days = settings.mailbox_sort_days

    def refresh_projectflow_status() -> ProjectFlowInstallation:
        nonlocal projectflow_installation
        try:
            projectflow_installation = find_projectflow(projectflow_input.text())
        except Exception as exc:
            append_log(f"Detection ProjectFlow impossible: {exc}")
            projectflow_installation = ProjectFlowInstallation(None)
        projectflow_status.setText(projectflow_installation.status_text)
        update_mailbox_actions()
        return projectflow_installation

    def update_mailbox_jev_option() -> None:
        available = has_jev_api_key()
        mailbox_jev_checkbox.setEnabled(available)
        mailbox_jev_checkbox.setToolTip(
            "Jev choisit parmi les projets où ces interlocuteurs ont déjà échangé. "
            "Une suggestion n'est jamais cochée d'office."
            if available
            else "Enregistrez une clé Jev (TypeSafe) dans Réglages pour activer cette option."
        )

    def mailbox_checked_choices() -> dict[str, tuple[str, ...]]:
        analysis = getattr(active_controller, "mailbox_analysis", None)
        if analysis is None:
            return {}
        proposals = {proposal.entry_id: proposal for proposal in analysis.proposals}
        choices: dict[str, tuple[str, ...]] = {}
        for row, entry_id in enumerate(mailbox_row_entry_ids):
            item = mailbox_table.item(row, CHECK_COLUMN)
            proposal = proposals.get(entry_id)
            if (
                item is None
                or proposal is None
                or mailbox_table.isRowHidden(row)
                or item.checkState() != Qt.CheckState.Checked
            ):
                continue
            if proposal.selectable_targets:
                choices[entry_id] = proposal.selectable_targets
        return choices

    def update_mailbox_actions() -> None:
        count = len(mailbox_checked_choices())
        # While busy the whole page is disabled, and the handler is guarded.
        mailbox_sort_button.setEnabled(count > 0)
        mailbox_sort_button.setText(
            f"Ranger les {count} mails cochés" if count > 1 else UI_TEXT["sort_mailbox"]
        )
        analysis = getattr(active_controller, "mailbox_analysis", None)
        missing = missing_project_numbers(analysis) if analysis is not None else []
        mailbox_projectflow_button.setEnabled(
            bool(missing) and projectflow_installation.supported
        )
        mailbox_projectflow_button.setText(
            f"Créer les {len(missing)} dossiers absents avec ProjectFlow"
            if len(missing) > 1
            else "Créer les dossiers absents avec ProjectFlow"
        )
        mailbox_projectflow_button.setToolTip(
            "ProjectFlow crée le dossier Outlook des projets de son répertoire chantier, "
            "avec son propre nommage."
            if projectflow_installation.supported
            else projectflow_installation.status_text
        )

    def apply_mailbox_filter() -> None:
        wanted = mailbox_filter_combo.currentData()
        analysis = getattr(active_controller, "mailbox_analysis", None)
        statuses = {
            proposal.entry_id: proposal.status.value
            for proposal in (analysis.proposals if analysis is not None else [])
        }
        for row, entry_id in enumerate(mailbox_row_entry_ids):
            mailbox_table.setRowHidden(
                row, wanted is not None and statuses.get(entry_id) != wanted
            )
        update_mailbox_actions()
        update_mailbox_panels()

    def refresh_mailbox_table() -> None:
        analysis = getattr(active_controller, "mailbox_analysis", None)
        proposals = analysis.proposals if analysis is not None else []
        folders = analysis.project_folders if analysis is not None else {}
        mailbox_table.blockSignals(True)
        try:
            mailbox_table.clearContents()
            mailbox_table.setRowCount(len(proposals))
            mailbox_row_entry_ids.clear()
            for row, proposal in enumerate(proposals):
                mailbox_row_entry_ids.append(proposal.entry_id)
                selectable = bool(proposal.selectable_targets)
                for column, text in enumerate(proposal_to_cells(proposal, folders)):
                    item = QTableWidgetItem(text)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    if text:
                        item.setToolTip(text)
                    if not selectable:
                        item.setForeground(QColor(COLORS["disabled"]))
                    mailbox_table.setItem(row, column, item)
                check_item = mailbox_table.item(row, CHECK_COLUMN)
                if selectable and check_item is not None:
                    check_item.setFlags(check_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    check_item.setCheckState(
                        Qt.CheckState.Checked
                        if proposal.selected_by_default
                        else Qt.CheckState.Unchecked
                    )
        finally:
            mailbox_table.blockSignals(False)
        mailbox_counts_label.setText(mailbox_counts_text(analysis, days=mailbox_analysis_days))
        apply_mailbox_filter()

    def set_visible_mailbox_checks(checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        mailbox_table.blockSignals(True)
        try:
            for row in range(mailbox_table.rowCount()):
                item = mailbox_table.item(row, CHECK_COLUMN)
                if (
                    item is not None
                    and not mailbox_table.isRowHidden(row)
                    and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                ):
                    item.setCheckState(state)
        finally:
            mailbox_table.blockSignals(False)
        update_mailbox_actions()

    def save_mailbox_options(pending_folder: str, days: int) -> None:
        settings.mailbox_pending_folder = pending_folder
        settings.mailbox_sort_days = days
        settings.mailbox_read_attachments = mailbox_attachments_checkbox.isChecked()
        settings.mailbox_suggest_with_jev = mailbox_jev_checkbox.isChecked()
        try:
            save_settings(settings)
        except Exception as exc:
            append_log(f"Options de rangement non enregistrees: {exc}")

    @exclusive_operation
    def on_analyze_mailbox() -> None:
        kinds = frozenset(
            kind
            for kind, checkbox in (
                (MailboxSourceKind.INBOX, mailbox_inbox_checkbox),
                (MailboxSourceKind.PENDING, mailbox_pending_checkbox),
                (MailboxSourceKind.SENT, mailbox_sent_checkbox),
            )
            if checkbox.isChecked()
        )
        if not kinds:
            mailbox_status_label.setText("Cochez au moins un dossier à analyser.")
            return
        pending_folder = (
            clean_optional_text(mailbox_pending_input.text()) or DEFAULT_MAILBOX_PENDING_FOLDER
        )
        nonlocal mailbox_analysis_days
        days = int(mailbox_period_combo.currentData())
        mailbox_analysis_days = days
        save_mailbox_options(pending_folder, days)
        suggester = None
        # The page is disabled while busy: rely on the saved key, not on the widget state.
        if mailbox_jev_checkbox.isChecked() and has_jev_api_key():
            matcher = build_project_suggester(settings)
            if matcher is None:
                append_log("Aucune cle Jev: suggestions de projet desactivees.")
            else:
                suggester = ResponsiveProjectSuggester(matcher)
        progress_dialog = QProgressDialog(
            "Lecture des dossiers Outlook...", "Annuler", 0, 0, window
        )
        progress_dialog.setWindowTitle("Analyse de la boîte mail")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setAutoClose(False)
        progress_dialog.setAutoReset(False)
        progress_dialog.show()
        QApplication.processEvents()

        def progress(current: int, total: int, message: str) -> bool:
            progress_dialog.setMaximum(max(total, 1))
            progress_dialog.setValue(min(current, max(total, 1)))
            progress_dialog.setLabelText(message)
            QApplication.processEvents()
            return not progress_dialog.wasCanceled()

        mailbox_status_label.setText("")
        try:
            analysis = active_controller.analyze_mailbox(
                MailboxSortRequest(
                    account_identifier=selected_account_identifier(),
                    outlook_root_folder=current_outlook_root_folder(),
                    pending_folder_name=pending_folder,
                    sources=kinds,
                    since=mailbox_since(days),
                    read_attachment_contents=mailbox_attachments_checkbox.isChecked(),
                ),
                progress=progress,
                suggester=suggester,
            )
        except Exception as exc:
            refresh_mailbox_table()
            mailbox_summary_label.setText("Analyse de la boîte mail impossible.")
            append_log(f"Erreur analyse boite mail: {exc}")
            QMessageBox.warning(window, "Analyse de la boîte mail", str(exc))
            return
        finally:
            progress_dialog.close()
            progress_dialog.deleteLater()
        refresh_mailbox_table()
        summary = summarize_mailbox_analysis(analysis, days=days)
        mailbox_summary_label.setText(summary)
        mailbox_status_label.setText(" ".join(analysis.warnings))
        append_log(f"Boite mail analysee: {summary}")
        for warning in analysis.warnings:
            append_log(warning)

    @exclusive_operation
    def on_sort_mailbox() -> None:
        choices = mailbox_checked_choices()
        if not choices:
            mailbox_status_label.setText("Cochez les mails à ranger.")
            return
        answer = QMessageBox.question(
            window,
            "Ranger la boîte mail",
            build_mailbox_sort_confirmation(choices),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            append_log("Rangement Outlook annule.")
            return
        try:
            result = active_controller.sort_mailbox(choices)
        except Exception as exc:
            append_log(f"Erreur rangement Outlook: {exc}")
            QMessageBox.warning(window, "Ranger la boîte mail", str(exc))
            return
        refresh_mailbox_table()
        message = format_mailbox_sort_result(result)
        mailbox_status_label.setText(message)
        append_log(message)
        for failure in result.failures[:10]:
            append_log(f"Echec rangement Outlook: {failure}")
        if result.moved_count:
            append_log("Les mails ranges seront repris au prochain scan des dossiers projet.")

    @exclusive_operation
    def on_create_missing_folders() -> None:
        analysis = getattr(active_controller, "mailbox_analysis", None)
        numbers = missing_project_numbers(analysis) if analysis is not None else []
        if not numbers:
            mailbox_status_label.setText("Aucun dossier projet absent dans la liste.")
            return
        installation = refresh_projectflow_status()
        if installation.executable is None or not installation.supported:
            mailbox_status_label.setText(installation.status_text)
            return
        answer = QMessageBox.question(
            window,
            "Créer les dossiers avec ProjectFlow",
            build_projectflow_confirmation(numbers),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            append_log("Creation des dossiers par ProjectFlow annulee.")
            return
        progress_dialog = QProgressDialog(
            "ProjectFlow prépare les dossiers Outlook...", "", 0, 0, window
        )
        progress_dialog.setWindowTitle("ProjectFlow")
        progress_dialog.setCancelButton(None)
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.show()
        QApplication.processEvents()
        link = ProjectFlowLink(installation.executable)
        try:
            # Only the wait for ProjectFlow leaves this thread; Outlook is read here after.
            result = run_with_event_loop(lambda: link.ensure_outlook_folders(numbers))
            refreshed = active_controller.refresh_mailbox_project_folders()
        except ProjectFlowError as exc:
            mailbox_status_label.setText(str(exc))
            append_log(f"ProjectFlow: {exc}")
            QMessageBox.warning(window, "ProjectFlow", str(exc))
            return
        except Exception as exc:
            mailbox_status_label.setText("Les dossiers n'ont pas pu être préparés.")
            append_log(f"Erreur ProjectFlow: {exc}")
            QMessageBox.warning(window, "ProjectFlow", str(exc))
            return
        finally:
            progress_dialog.close()
            progress_dialog.deleteLater()
        report = projectflow_report(result, refreshed.project_folders)
        refresh_mailbox_table()
        mailbox_summary_label.setText(
            summarize_mailbox_analysis(refreshed, days=mailbox_analysis_days)
        )
        message = format_projectflow_report(report)
        mailbox_status_label.setText(message)
        append_log(message)
        for number, reason in (*report.unknown, *report.failed):
            append_log(f"ProjectFlow {number}: {reason}")

    def open_mailbox_mail(row: int, _column: int) -> None:
        if operation_in_progress or not 0 <= row < len(mailbox_row_entry_ids):
            return
        try:
            active_controller.open_mailbox_mail(mailbox_row_entry_ids[row])
        except Exception as exc:
            append_log(f"Impossible d'ouvrir le mail dans Outlook: {exc}")

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

    # ── Review queue (1c) and group validation (2e) ──
    refreshing_queue = False
    bulk_selection_key: tuple[str, ...] = ()

    def elided(text: str, width: int, label: Any) -> str:
        return str(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))

    def build_queue_item_widget(row: PreviewRow) -> Any:
        view = queue_item_view(row)
        widget = QWidget()
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        item_layout = QVBoxLayout(widget)
        item_layout.setContentsMargins(14, 10, 14, 10)
        item_layout.setSpacing(3)
        correspondent = text_label("", "small")
        correspondent.setText(elided(view.correspondent, 200, correspondent))
        date_label = text_label(view.date_label, "small")
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        meta_row.addWidget(correspondent)
        meta_row.addStretch(1)
        meta_row.addWidget(date_label)
        item_layout.addLayout(meta_row)
        subject = QLabel()
        subject.setStyleSheet("QLabel { font-weight: 500; }")
        subject.setText(elided(view.subject, 290, subject))
        subject.setToolTip(view.subject)
        item_layout.addWidget(subject)
        tag_row = QHBoxLayout()
        tag_row.addWidget(tag_label(view.tag_text, view.tag_kind))
        tag_row.addStretch(1)
        item_layout.addLayout(tag_row)
        return widget

    def fit_table_height(target: Any, row_count: int, *, max_rows: int = 10) -> None:
        """Size a framed table to its rows, so the frame hugs them like in the mockups."""
        header_height = 0 if target.horizontalHeader().isHidden() else 34
        rows_height = target.verticalHeader().defaultSectionSize() * min(row_count, max_rows)
        target.setFixedHeight(header_height + rows_height + 4)

    def selected_queue_indexes() -> list[int]:
        return sorted(queue_list.row(item) for item in queue_list.selectedItems())

    def current_queue_index() -> int:
        selected = selected_queue_indexes()
        if len(selected) == 1:
            return selected[0]
        return -1 if selected else int(queue_list.currentRow())

    def refresh_review_queue(preferred_entry_id: str | None = None) -> None:
        nonlocal refreshing_queue
        rows = active_controller.preview_rows
        selected_ids = {
            str(item.data(Qt.ItemDataRole.UserRole)) for item in queue_list.selectedItems()
        }
        current_item = queue_list.currentItem()
        current_id = preferred_entry_id or (
            str(current_item.data(Qt.ItemDataRole.UserRole)) if current_item else None
        )
        if preferred_entry_id is not None:
            selected_ids = {preferred_entry_id}
        scroll = queue_list.verticalScrollBar().value()
        refreshing_queue = True
        queue_list.blockSignals(True)
        try:
            queue_list.clear()
            for row in rows:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, row.mail.entry_id)
                widget = build_queue_item_widget(row)
                item.setSizeHint(QSize(0, widget.sizeHint().height()))
                queue_list.addItem(item)
                queue_list.setItemWidget(item, widget)
            index_by_id = {row.mail.entry_id: index for index, row in enumerate(rows)}
            restored = [
                index_by_id[entry_id] for entry_id in selected_ids if entry_id in index_by_id
            ]
            target = index_by_id.get(current_id) if current_id is not None else None
            if target is None:
                target = restored[0] if restored else first_queue_index(rows)
            if target is not None:
                queue_list.setCurrentRow(target, QItemSelectionModel.SelectionFlag.NoUpdate)
            for index in restored or ([] if target is None else [target]):
                queue_list.item(index).setSelected(True)
            queue_list.verticalScrollBar().setValue(scroll)
            if preferred_entry_id is not None and target is not None:
                queue_list.scrollToItem(queue_list.item(target))
        finally:
            queue_list.blockSignals(False)
            refreshing_queue = False
        progress = queue_progress(rows)
        queue_progress_bar.set_segments([
            (progress.treated_count, COLORS["accent"]),
            (progress.review_count, COLORS["accent_900"]),
        ])
        update_queue_panels()

    def current_destination() -> str | None:
        index = decision_destination_combo.currentIndex()
        return DESTINATION_OPTIONS[index] if 0 <= index < len(DESTINATION_OPTIONS) else None

    def set_decision_destination(destination: str | None) -> None:
        decision_destination_combo.blockSignals(True)
        decision_destination_combo.setCurrentIndex(
            DESTINATION_OPTIONS.index(destination) if destination in DESTINATION_OPTIONS else -1
        )
        decision_destination_combo.blockSignals(False)

    def update_queue_panels() -> None:
        nonlocal bulk_selection_key
        if refreshing_queue:
            return
        rows = active_controller.preview_rows
        queue_header_info.setText(queue_header_text(rows))
        selected = selected_queue_indexes()
        if len(selected) > 1:
            show_queue_bulk(selected)
            return
        bulk_selection_key = ()
        index = current_queue_index()
        if not 0 <= index < len(rows):
            if rows:
                queue_empty_title.setText("Sélectionnez un mail dans la file")
                queue_empty_body.setText(
                    "Ctrl ou Maj + clic pour sélectionner plusieurs mails et leur appliquer "
                    "le même classement."
                )
            else:
                queue_empty_title.setText("Commencez par une analyse Outlook")
                queue_empty_body.setText(
                    "Choisissez votre compte et votre dossier source, puis cliquez sur "
                    "Scanner Outlook. Les mails à vérifier apparaîtront ici, un à la fois."
                )
            queue_empty_settings_button.setVisible(not rows)
            queue_detail_stack.setCurrentWidget(queue_empty)
            queue_decision_stack.setCurrentWidget(decision_empty)
            return
        show_queue_mail(index)

    def show_queue_mail(index: int) -> None:
        from mailflow.ui.mail_preview import (
            classification_highlight_terms,
            highlight_terms_as_html,
        )

        rows = active_controller.preview_rows
        row = rows[index]
        view = queue_item_view(row)
        queue_detail_stack.setCurrentWidget(queue_single)
        queue_decision_stack.setCurrentWidget(decision_single)
        set_tag(queue_action_tag, view.tag_text, view.tag_kind)
        queue_meta_label.setText(mail_meta_text(row))
        queue_subject_label.setText(view.subject)
        queue_sender_label.setText(sender_html(row))
        queue_attachments_label.setText(attachments_text(row))
        body = highlight_terms_as_html(
            row.mail.body_excerpt or "Aucun extrait disponible.",
            classification_highlight_terms(row),
        )
        queue_body.setHtml(f"<div style='line-height:170%;'>{body}</div>")
        queue_previous_button.setEnabled(index > 0)
        queue_next_button.setEnabled(index < len(rows) - 1)
        decision = decision_view(row, settings.ai_provider)
        threshold = settings.decision_confidence_threshold
        set_kicker_text(decision_kicker, decision.kicker)
        decision_figure.setText(percent_html(decision.confidence))
        decision_bar.set_values(decision.confidence, threshold)
        decision_threshold_label.setText(f"Seuil de vérification : {threshold:.0%}")
        decision_text.setText(decision_text_html(decision))
        set_decision_destination(destination_category(row))
        decision_role.set_value(role_choice(row))
        decision_target_label.setText(
            f"Dossier actuel : {row.decision.target_relative_folder}"
        )
        editable = row.action != PreviewAction.ARCHIVED
        decision_destination_combo.setEnabled(editable)
        decision_role.setEnabled(editable)
        update_decision_validity()

    def update_decision_validity() -> None:
        rows = active_controller.preview_rows
        index = current_queue_index()
        if not 0 <= index < len(rows):
            return
        if rows[index].action == PreviewAction.ARCHIVED:
            problem: str | None = "Ce mail est déjà archivé. Son classement est conservé."
        else:
            problem = validation_problem(current_destination(), cast(Any, decision_role.value()))
        decision_warning.setText(problem or "")
        decision_warning.setVisible(problem is not None)
        decision_validate_button.setEnabled(problem is None and not operation_in_progress)

    def on_decision_role_changed(role: object) -> None:
        set_decision_destination(destination_for_role(current_destination(), cast(Any, role)))
        update_decision_validity()

    def choose_queue_role(role: InterlocutorType) -> None:
        if len(selected_queue_indexes()) > 1:
            if bulk_role.isEnabled():
                bulk_role.set_value(role)
                on_bulk_role_changed(role)
            return
        if decision_role.isEnabled():
            decision_role.set_value(role)
            on_decision_role_changed(role)

    def commit_manual_update(row_index: int, update: ManualClassificationUpdate) -> bool:
        try:
            updated = active_controller.apply_manual_update(row_index, update)
        except Exception as exc:
            append_log(f"Erreur classement manuel: {exc}")
            return False
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
        return True

    def commit_bulk_update(row_indexes: Sequence[int], update: ManualClassificationUpdate) -> None:
        try:
            result = active_controller.apply_manual_updates(row_indexes, update)
        except Exception as exc:
            append_log(f"Erreur classement groupe: {exc}")
            return
        message = f"Classement manuel applique a {result.updated_count} mail(s)."
        if result.skipped_archived_count:
            message += f" {result.skipped_archived_count} mail(s) archive(s) inchange(s)."
        append_log(message)
        for change in result.role_changes:
            append_log(
                f"Annuaire : {change.organization_name} enregistre comme {change.role.value}."
            )
        for error in result.errors:
            append_log(f"Erreur classement manuel: {error}")
        if result.role_changes:
            refresh_directory_table()
        set_scan_status(message, success=not result.errors)

    def validate_queue_mail() -> None:
        if operation_in_progress or refreshing_table:
            return
        rows = active_controller.preview_rows
        index = current_queue_index()
        if not 0 <= index < len(rows) or rows[index].action == PreviewAction.ARCHIVED:
            return
        destination = current_destination()
        role = cast(Any, decision_role.value())
        if destination is None or validation_problem(destination, role) is not None:
            return
        row = rows[index]
        update = build_queue_update(destination, role, row.decision.interlocutor)
        if not commit_manual_update(index, update):
            refresh_table(preferred_row_index=index)
            return
        rows = active_controller.preview_rows
        new_index = next(
            (
                position for position, candidate in enumerate(rows)
                if candidate.mail.entry_id == row.mail.entry_id
            ),
            index,
        )
        next_index = next_review_index(rows, new_index)
        refresh_table(preferred_row_index=new_index if next_index is None else next_index)
        if next_index is None:
            set_scan_status("File de vérification terminée : aucun autre mail à vérifier.",
                            success=True)

    def show_queue_bulk(indexes: Sequence[int]) -> None:
        nonlocal bulk_selection_key
        rows = active_controller.preview_rows
        selection = [rows[index] for index in indexes if 0 <= index < len(rows)]
        view = bulk_selection_view(selection)
        queue_detail_stack.setCurrentWidget(queue_bulk)
        queue_decision_stack.setCurrentWidget(decision_bulk)
        queue_header_info.setText(f"{len(selection)} mails sélectionnés · {view.company_line}")
        key = tuple(row.mail.entry_id for row in selection)
        if key != bulk_selection_key:
            bulk_selection_key = key
            editable = [row for row in selection if row.action != PreviewAction.ARCHIVED]
            categories = {destination_category(row) for row in editable}
            roles = {role_choice(row) for row in editable}
            bulk_category.set_value(categories.pop() if len(categories) == 1 else None)
            bulk_role.set_value(roles.pop() if len(roles) == 1 else None)
        update_bulk_summary()

    def update_bulk_summary() -> None:
        rows = active_controller.preview_rows
        selection = [rows[index] for index in selected_queue_indexes() if index < len(rows)]
        view = bulk_selection_view(selection)
        destination = cast(Any, bulk_category.value())
        role = cast(Any, bulk_role.value())
        count_text = str(view.editable_count)
        if view.archived_count:
            count_text += f" ({view.archived_count} archivé(s) inchangé(s))"
        queue_bulk_values["count"].setText(count_text)
        queue_bulk_values["destination"].setText(
            bulk_destination_text(destination, view.companies)
        )
        queue_bulk_values["role"].setText(role_text(role))
        problem = (
            "Aucun mail modifiable dans la sélection."
            if view.editable_count == 0
            else bulk_validation_problem(destination, role)
        )
        bulk_warning.setText(problem or "")
        bulk_warning.setVisible(problem is not None)
        bulk_apply_button.setText(
            "Appliquer à 1 mail" if view.editable_count == 1
            else f"Appliquer aux {view.editable_count} mails"
        )
        bulk_apply_button.setEnabled(problem is None and not operation_in_progress)

    def on_bulk_role_changed(role: object) -> None:
        bulk_category.set_value(
            destination_for_role(cast(Any, bulk_category.value()), cast(Any, role))
        )
        update_bulk_summary()

    def apply_bulk_classification() -> None:
        if operation_in_progress or refreshing_table:
            return
        rows = active_controller.preview_rows
        editable = [
            index for index in selected_queue_indexes()
            if 0 <= index < len(rows) and rows[index].action != PreviewAction.ARCHIVED
        ]
        destination = cast(Any, bulk_category.value())
        role = cast(Any, bulk_role.value())
        if not editable or bulk_validation_problem(destination, role) is not None:
            return
        commit_bulk_update(editable, build_queue_update(destination, role, role))
        refresh_table(preferred_row_index=editable[0])

    def cancel_bulk_selection() -> None:
        index = queue_list.currentRow()
        queue_list.clearSelection()
        if index >= 0:
            queue_list.setCurrentRow(index)

    def move_in_queue(step: int) -> None:
        index = current_queue_index()
        target = index + step
        if 0 <= target < queue_list.count():
            queue_list.setCurrentRow(target)

    def queue_validate_shortcut() -> None:
        if len(selected_queue_indexes()) > 1:
            if bulk_apply_button.isEnabled():
                bulk_apply_button.click()
        elif decision_validate_button.isEnabled():
            decision_validate_button.click()

    # ── Arborescence (2a) ──
    ignored_duplicates: set[str] = set()
    folder_icon = QIcon(icon_path("folder-tree"))

    def current_duplicates() -> list[DuplicateHint]:
        return find_duplicate_folders(active_controller.preview_rows, ignored=ignored_duplicates)

    def duplicate_for(path: str | None) -> DuplicateHint | None:
        return next((hint for hint in current_duplicates() if hint.source == path), None)

    def tree_items() -> list[Any]:
        items: list[Any] = []
        pending: list[Any] = [
            folder_tree.topLevelItem(index) for index in range(folder_tree.topLevelItemCount())
        ]
        while pending:
            item = pending.pop(0)
            items.append(item)
            pending.extend(item.child(index) for index in range(item.childCount()))
        return items

    def update_folder_panels() -> None:
        path = selected_folder_path()
        rows = active_controller.preview_rows
        hint = duplicate_for(path)
        has_folder = path is not None
        for button in (rename_folder_button, merge_folder_button):
            button.setEnabled(has_folder)
        tree_merge_duplicate_button.setVisible(hint is not None)
        tree_ignore_duplicate_button.setVisible(hint is not None)
        tree_target_field.setVisible(hint is not None)
        if path is None:
            duplicates = current_duplicates()
            tree_breadcrumb.setText("")
            tree_folder_title.setText("Aucun dossier sélectionné")
            set_tag(tree_duplicate_tag, "", "outline")
            tree_mails_table.setRowCount(0)
            tree_mails_frame.setVisible(False)
            tree_folder_note.setText("Sélectionnez un dossier à gauche pour voir ses mails.")
            set_kicker_text(tree_kicker, "Dossiers")
            tree_decision_title.setText(
                f"{len(duplicates)} doublon(s) probable(s)" if duplicates else "Aucun doublon"
            )
            tree_decision_text.setText(
                "Les dossiers marqués « doublon » portent presque le même nom qu'un autre "
                "dossier de la même branche. Fusionnez-les avant l'archivage."
                if duplicates
                else "Renommez ou fusionnez un dossier avant l'archivage : rien n'est "
                "créé sur le disque avant."
            )
            return
        selection = folder_rows(rows, path)
        tree_breadcrumb.setText(breadcrumb_text(path))
        tree_folder_title.setText(leaf_name(path))
        set_tag(
            tree_duplicate_tag,
            f"doublon probable de « {hint.target_name} »" if hint else "",
            "outline",
        )
        tree_mails_frame.setVisible(bool(selection))
        tree_mails_table.setRowCount(len(selection))
        for row_index, row in enumerate(selection):
            subject_item = QTableWidgetItem(row.mail.subject or "(Sans objet)")
            subject_item.setToolTip(row.mail.subject)
            date_item = QTableWidgetItem(f"{row.mail.sent_at:%d.%m.%Y}")
            date_item.setForeground(QColor(COLORS["muted"]))
            date_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            tree_mails_table.setItem(row_index, 0, subject_item)
            tree_mails_table.setItem(row_index, 1, date_item)
        fit_table_height(tree_mails_table, len(selection))
        tree_folder_note.setText(duplicate_note(hint) if hint else folder_text(selection))
        if hint is not None:
            set_kicker_text(tree_kicker, "Fusion proposée")
            tree_decision_title.setText(f"{hint.source_name} → {hint.target_name}")
            tree_decision_text.setText(merge_text(hint))
            tree_target_input.setText(hint.target_name)
        else:
            set_kicker_text(tree_kicker, "Dossier")
            tree_decision_title.setText(leaf_name(path))
            tree_decision_text.setText(folder_text(selection))

    def merge_duplicate_and_next() -> None:
        nonlocal preferred_folder_path
        if operation_in_progress:
            return
        hint = duplicate_for(selected_folder_path())
        if hint is None:
            return
        try:
            active_controller.merge_preview_folder(hint.source, hint.target)
        except Exception as exc:
            append_log(f"Erreur fusion dossier: {exc}")
            return
        append_log(f"Dossier fusionne: {hint.source} -> {hint.target}.")
        remaining = current_duplicates()
        preferred_folder_path = remaining[0].source if remaining else hint.target
        refresh_table()

    def ignore_selected_duplicate() -> None:
        path = selected_folder_path()
        if path is None:
            return
        ignored_duplicates.add(path)
        refresh_folder_tree()

    # ── Annuaire (2b) ──
    directory_entries_cache: list[Any] = []
    directory_suggestions_cache: dict[int, Any] = {}
    directory_current_id: int | None = None
    refreshing_directory_list = False

    def directory_entry(organization_id: int | None) -> Any | None:
        return next(
            (
                entry for entry in directory_entries_cache
                if int(entry.organization_id) == organization_id
            ),
            None,
        )

    def visible_directory_entries() -> list[Any]:
        without_role = directory_filter.value() == "without_role"
        query = directory_search_input.text()
        return [
            entry for entry in directory_entries_cache
            if matches_directory_filter(entry, query, without_role_only=without_role)
        ]

    def build_directory_item_widget(view: Any) -> Any:
        widget = QWidget()
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        item_layout = QVBoxLayout(widget)
        item_layout.setContentsMargins(16, 10, 16, 10)
        item_layout.setSpacing(2)
        name = QLabel()
        name.setStyleSheet("QLabel { font-weight: 500; }")
        name.setText(elided(view.name, 220, name))
        top_row = QHBoxLayout()
        top_row.addWidget(name)
        top_row.addStretch(1)
        top_row.addWidget(text_label(view.project_text, "small"))
        item_layout.addLayout(top_row)
        item_layout.addWidget(text_label(view.domain_text, "small"))
        if view.suggestion_text:
            suggestion = QLabel(view.suggestion_text)
            suggestion.setStyleSheet(
                f"QLabel {{ color: {COLORS['accent_700']}; font-size: 12px; }}"
            )
            item_layout.addWidget(suggestion)
        return widget

    def refresh_directory_list() -> None:
        nonlocal refreshing_directory_list
        visible = visible_directory_entries()
        scroll = directory_list.verticalScrollBar().value()
        refreshing_directory_list = True
        directory_list.blockSignals(True)
        try:
            directory_list.clear()
            for entry in visible:
                view = directory_item_view(
                    entry, directory_suggestions_cache.get(int(entry.organization_id))
                )
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, view.organization_id)
                widget = build_directory_item_widget(view)
                item.setSizeHint(QSize(0, widget.sizeHint().height()))
                directory_list.addItem(item)
                directory_list.setItemWidget(item, widget)
                if view.organization_id == directory_current_id:
                    directory_list.setCurrentItem(item)
            directory_list.verticalScrollBar().setValue(scroll)
        finally:
            directory_list.blockSignals(False)
            refreshing_directory_list = False
        directory_list_count.setText(str(len(directory_entries_cache)))
        directory_filter.set_label(
            "without_role", f"Sans rôle {without_role_count(directory_entries_cache)}"
        )
        update_directory_panels()

    def select_directory_organization(organization_id: int | None) -> None:
        nonlocal directory_current_id
        directory_current_id = organization_id
        update_directory_panels()

    def on_directory_list_row_changed(row: int) -> None:
        if refreshing_directory_list:
            return
        item = directory_list.item(row) if row >= 0 else None
        if item is not None:
            select_directory_organization(int(item.data(Qt.ItemDataRole.UserRole)))

    def on_directory_table_selection_changed() -> None:
        if refreshing_directory_table:
            return
        selection_model = directory_table.selectionModel()
        rows = selection_model.selectedRows() if selection_model is not None else []
        item = directory_table.item(rows[0].row(), 0) if rows else None
        if item is not None:
            select_directory_organization(int(item.data(Qt.ItemDataRole.UserRole)))

    def update_directory_panels() -> None:
        entry = directory_entry(directory_current_id)
        if entry is None:
            set_tag(directory_role_tag, "", "outline")
            directory_name_label.setText("Aucune entreprise sélectionnée")
            directory_meta_label.setText(
                "Sélectionnez une entreprise pour voir ses contacts et son rôle."
            )
            directory_contacts_frame.setVisible(False)
            directory_projects_label.setText("")
            set_kicker_text(directory_kicker, "Rôle global")
            directory_figure.setVisible(False)
            directory_suggestion_label.setText(
                "Le rôle d'une entreprise fixe le classement de tous ses mails."
            )
            directory_role_choice.set_value(None)
            directory_role_choice.setEnabled(False)
            directory_validate_button.setEnabled(False)
            return
        role = entry_role(entry)
        set_tag(directory_role_tag, *role_tag(role))
        contacts = tuple(str(contact) for contact in entry.contacts)
        directory_name_label.setText(str(entry.name))
        directory_meta_label.setText(
            f"{domains_text(tuple(str(domain) for domain in entry.domains))} · "
            f"{len(contacts)} contact(s)"
        )
        directory_contacts_frame.setVisible(bool(contacts))
        directory_contacts_table.setRowCount(len(contacts))
        for row_index, contact in enumerate(contacts):
            name, address = split_contact(contact)
            directory_contacts_table.setItem(row_index, 0, QTableWidgetItem(name or "—"))
            address_item = QTableWidgetItem(address)
            address_item.setForeground(QColor(COLORS["muted"]))
            directory_contacts_table.setItem(row_index, 1, address_item)
        fit_table_height(directory_contacts_table, len(contacts))
        directory_projects_label.setText(
            f"Présente dans {projects_text(int(getattr(entry, 'project_count', 0)))}."
        )
        suggestion = selected_suggestion(directory_suggestions_cache, entry)
        if suggestion is not None:
            set_kicker_text(directory_kicker, "Suggestion Jev")
            directory_figure.setText(percent_html(float(suggestion.probability)))
            directory_figure.setVisible(True)
            directory_suggestion_label.setText(suggestion_headline(suggestion))
        else:
            set_kicker_text(directory_kicker, "Rôle global")
            directory_figure.setVisible(False)
            directory_suggestion_label.setText(
                f"Rôle enregistré : <b>{role_tag(role)[0].casefold()}</b>."
                if has_business_role(entry)
                else "Aucune suggestion pour cette entreprise : choisissez son rôle."
            )
        if has_business_role(entry):
            proposed = role
        elif suggestion is not None and suggestion.role in BUSINESS_ROLES:
            proposed = suggestion.role
        else:
            proposed = None
        directory_role_choice.set_value(proposed)
        directory_role_choice.setEnabled(True)
        directory_validate_button.setEnabled(proposed is not None and not operation_in_progress)

    def validate_directory_role_and_next() -> None:
        nonlocal directory_current_id
        if operation_in_progress:
            return
        entry = directory_entry(directory_current_id)
        role = cast(Any, directory_role_choice.value())
        if entry is None or role is None:
            return
        organization_id = int(entry.organization_id)
        if role != entry_role(entry):
            before = mail_states()
            try:
                active_controller.set_directory_organization_role(organization_id, role)
            except Exception as exc:
                append_log(f"Erreur role global: {exc}")
                return
            refresh_table()
            append_log(f"Role global applique: {role.value} pour {entry.name}.")
            report_role_update(before)
        refresh_directory_table()
        next_id = next_without_role(visible_directory_entries(), organization_id)
        directory_current_id = organization_id if next_id is None else next_id
        refresh_directory_list()

    def set_directory_view(mode: object) -> None:
        table_mode = mode == "table"
        (
            directory_toggle_host_table if table_mode else directory_toggle_host_list
        ).addWidget(directory_view_toggle)
        if table_mode:
            directory_table_actions_host.addWidget(directory_global_actions)
            directory_table_actions_host.addWidget(directory_edit_actions, 1)
        else:
            directory_list_footer_layout.addWidget(directory_global_actions)
            directory_edit_host_list.addWidget(directory_edit_actions)
        directory_view_toggle.set_value("table" if table_mode else "list")
        directory_views.setCurrentIndex(1 if table_mode else 0)

    # ── Boîte mail (2d) ──
    def selected_mailbox_proposal() -> Any | None:
        analysis = getattr(active_controller, "mailbox_analysis", None)
        if analysis is None:
            return None
        selection_model = mailbox_table.selectionModel()
        rows = selection_model.selectedRows() if selection_model is not None else []
        row = rows[0].row() if rows else -1
        if not 0 <= row < len(mailbox_row_entry_ids) or mailbox_table.isRowHidden(row):
            return None
        entry_id = mailbox_row_entry_ids[row]
        return next(
            (proposal for proposal in analysis.proposals if proposal.entry_id == entry_id),
            None,
        )

    def update_mailbox_panels() -> None:
        proposal = selected_mailbox_proposal()
        if proposal is None:
            mailbox_detail_stack.setCurrentWidget(mailbox_options_view)
            mailbox_destination_title.setText("Aucun mail sélectionné")
            mailbox_destination_frame.setVisible(False)
            mailbox_destination_note.setText(
                "Sélectionnez un mail analysé pour voir son dossier projet."
            )
            return
        analysis = getattr(active_controller, "mailbox_analysis", None)
        folders = analysis.project_folders if analysis is not None else {}
        mailbox_detail_stack.setCurrentWidget(mailbox_mail_view)
        set_tag(
            mailbox_detail_tag, STATUS_LABELS[proposal.status], STATUS_TAG_KINDS[proposal.status]
        )
        mailbox_detail_subject.setText(proposal.subject or "(sans objet)")
        mailbox_detail_meta.setText(proposal_meta_text(proposal))
        mailbox_detail_values["correspondent"].setText(proposal.correspondent or "—")
        mailbox_detail_values["numbers"].setText(
            ", ".join(reference.number for reference in proposal.references) or "—"
        )
        mailbox_detail_values["found_in"].setText(proposal_found_in(proposal))
        mailbox_detail_values["note"].setText(proposal.note or "—")
        destination = mailbox_destination_view(proposal, folders)
        mailbox_destination_title.setText(destination.title)
        mailbox_destination_lines.setText("\n".join(destination.lines))
        mailbox_destination_frame.setVisible(bool(destination.lines))
        mailbox_destination_note.setText(destination.note)

    def show_mailbox_options() -> None:
        mailbox_table.clearSelection()
        mailbox_detail_stack.setCurrentWidget(mailbox_options_view)

    scan_button.clicked.connect(on_scan)
    search_input.textChanged.connect(apply_mail_filters)
    status_filter.currentIndexChanged.connect(apply_mail_filters)
    clear_filters_button.clicked.connect(clear_mail_filters)
    review_button.clicked.connect(
        lambda: open_bulk_manual_dialog(selected_table_row_indexes())
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
    validate_suggestions_button.clicked.connect(
        lambda _checked=False: validate_confident_role_suggestions()
    )
    refresh_roles_button.clicked.connect(lambda _checked=False: refresh_roles_without_ai())
    refresh_roles_action.triggered.connect(lambda _checked=False: refresh_roles_without_ai())
    refresh_directory_button.clicked.connect(refresh_directory_table)
    add_directory_button.clicked.connect(add_directory_organization)
    delete_directory_button.clicked.connect(delete_selected_directory_organization)
    rename_directory_button.clicked.connect(rename_selected_directory_organization)
    merge_directory_button.clicked.connect(merge_selected_directory_organization)
    navigation.currentRowChanged.connect(
        lambda index: refresh_directory_table() if index == 2 else None
    )
    navigation.currentRowChanged.connect(
        lambda index: update_mailbox_jev_option() if index == MAILBOX_PAGE else None
    )
    navigation.currentRowChanged.connect(
        lambda index: refresh_projectflow_status()
        if index in {MAILBOX_PAGE, SETTINGS_PAGE}
        else None
    )
    mailbox_projectflow_button.clicked.connect(
        lambda _checked=False: on_create_missing_folders()
    )
    browse_projectflow_button.clicked.connect(lambda _checked=False: browse_projectflow())
    projectflow_input.editingFinished.connect(refresh_projectflow_status)
    mailbox_analyze_button.clicked.connect(lambda _checked=False: on_analyze_mailbox())
    mailbox_sort_button.clicked.connect(lambda _checked=False: on_sort_mailbox())
    mailbox_check_all_button.clicked.connect(
        lambda _checked=False: set_visible_mailbox_checks(True)
    )
    mailbox_uncheck_all_button.clicked.connect(
        lambda _checked=False: set_visible_mailbox_checks(False)
    )
    mailbox_filter_combo.currentIndexChanged.connect(lambda _index: apply_mailbox_filter())
    mailbox_table.itemChanged.connect(
        lambda item: update_mailbox_actions() if item.column() == CHECK_COLUMN else None
    )
    mailbox_table.cellDoubleClicked.connect(open_mailbox_mail)
    save_openai_key_button.clicked.connect(save_openai_key_from_input)
    test_openai_key_button.clicked.connect(test_openai_key_from_input)
    save_jev_key_button.clicked.connect(save_jev_key_from_input)
    test_jev_key_button.clicked.connect(test_jev_key_from_input)
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
    queue_list.itemSelectionChanged.connect(update_queue_panels)
    queue_list.currentRowChanged.connect(lambda _row: update_queue_panels())
    queue_list.itemDoubleClicked.connect(lambda item: open_manual_dialog(queue_list.row(item)))
    queue_archive_button.clicked.connect(lambda _checked=False: on_archive_all_except_review())
    queue_previous_button.clicked.connect(lambda _checked=False: move_in_queue(-1))
    queue_next_button.clicked.connect(lambda _checked=False: move_in_queue(1))
    decision_role.changed.connect(on_decision_role_changed)
    decision_destination_combo.currentIndexChanged.connect(
        lambda _index: update_decision_validity()
    )
    decision_validate_button.clicked.connect(lambda _checked=False: validate_queue_mail())
    bulk_category.changed.connect(lambda _value: update_bulk_summary())
    bulk_role.changed.connect(on_bulk_role_changed)
    bulk_apply_button.clicked.connect(lambda _checked=False: apply_bulk_classification())
    bulk_cancel_button.clicked.connect(lambda _checked=False: cancel_bulk_selection())
    folder_tree.currentItemChanged.connect(lambda _current, _previous: update_folder_panels())
    tree_archive_button.clicked.connect(lambda _checked=False: on_archive_all_except_review())
    tree_merge_duplicate_button.clicked.connect(
        lambda _checked=False: merge_duplicate_and_next()
    )
    tree_ignore_duplicate_button.clicked.connect(
        lambda _checked=False: ignore_selected_duplicate()
    )
    directory_list.currentRowChanged.connect(on_directory_list_row_changed)
    directory_table.itemSelectionChanged.connect(on_directory_table_selection_changed)
    directory_search_input.textChanged.connect(lambda _text: refresh_directory_list())
    directory_filter.changed.connect(lambda _value: refresh_directory_list())
    directory_view_toggle.changed.connect(set_directory_view)
    directory_role_choice.changed.connect(
        lambda value: directory_validate_button.setEnabled(
            value is not None and not operation_in_progress
        )
    )
    directory_validate_button.clicked.connect(
        lambda _checked=False: validate_directory_role_and_next()
    )
    mailbox_table.itemSelectionChanged.connect(update_mailbox_panels)
    mailbox_options_button.clicked.connect(lambda _checked=False: show_mailbox_options())
    table.currentCellChanged.connect(lambda row, _col, _old_row, _old_col: update_mail_preview(row))
    table.itemSelectionChanged.connect(update_preview_from_selection)
    def focus_mail_search() -> None:
        # The search belongs to the table view; the queue has no filter of its own.
        navigation.setCurrentRow(0)
        set_mail_view("table")
        search_input.setFocus()

    shortcut_actions: list[Any] = []
    for sequence, callback in (
        ("Ctrl+F", focus_mail_search),
        ("Ctrl+R", on_scan),
        ("Ctrl+Return", on_archive_selection),
        ("Ctrl+,", lambda: navigation.setCurrentRow(SETTINGS_PAGE)),
    ):
        shortcut_action = QAction(window)
        shortcut_action.setShortcut(QKeySequence(sequence))
        shortcut_action.triggered.connect(callback)
        window.addAction(shortcut_action)
        shortcut_actions.append(shortcut_action)
    for sequence, callback in (
        ("C", lambda: choose_queue_role(InterlocutorType.CLIENT)),
        ("F", lambda: choose_queue_role(InterlocutorType.FOURNISSEUR)),
        ("Return", queue_validate_shortcut),
        ("Enter", queue_validate_shortcut),
    ):
        queue_shortcut = QAction(queue_view)
        queue_shortcut.setShortcut(QKeySequence(sequence))
        queue_shortcut.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        queue_shortcut.triggered.connect(callback)
        queue_view.addAction(queue_shortcut)
        shortcut_actions.append(queue_shortcut)
    review_shortcut = QAction(table)
    review_shortcut.setShortcut(QKeySequence("Return"))
    review_shortcut.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
    review_shortcut.triggered.connect(review_button.click)
    table.addAction(review_shortcut)
    shortcut_actions.append(review_shortcut)
    dynamic_window.mailflow_close_handler = handle_window_close
    set_directory_view("list")
    populate_account_options()
    refresh_directory_table()
    update_mailbox_jev_option()
    refresh_projectflow_status()

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
    dynamic_window.mailflow_validate_suggestions_button = validate_suggestions_button
    dynamic_window.mailflow_refresh_roles_button = refresh_roles_button
    dynamic_window.mailflow_refresh_roles_action = refresh_roles_action
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
    dynamic_window.mailflow_jev_model_input = jev_model_input
    dynamic_window.mailflow_jev_key_input = jev_key_input
    dynamic_window.mailflow_jev_key_status = jev_key_status
    dynamic_window.mailflow_save_jev_key_button = save_jev_key_button
    dynamic_window.mailflow_test_jev_key_button = test_jev_key_button
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
    dynamic_window.mailflow_mailbox_page = mailbox_page
    dynamic_window.mailflow_mailbox_inbox_checkbox = mailbox_inbox_checkbox
    dynamic_window.mailflow_mailbox_pending_checkbox = mailbox_pending_checkbox
    dynamic_window.mailflow_mailbox_pending_input = mailbox_pending_input
    dynamic_window.mailflow_mailbox_sent_checkbox = mailbox_sent_checkbox
    dynamic_window.mailflow_mailbox_period_combo = mailbox_period_combo
    dynamic_window.mailflow_mailbox_attachments_checkbox = mailbox_attachments_checkbox
    dynamic_window.mailflow_mailbox_jev_checkbox = mailbox_jev_checkbox
    dynamic_window.mailflow_mailbox_analyze_button = mailbox_analyze_button
    dynamic_window.mailflow_mailbox_summary_label = mailbox_summary_label
    dynamic_window.mailflow_mailbox_filter_combo = mailbox_filter_combo
    dynamic_window.mailflow_mailbox_check_all_button = mailbox_check_all_button
    dynamic_window.mailflow_mailbox_uncheck_all_button = mailbox_uncheck_all_button
    dynamic_window.mailflow_mailbox_sort_button = mailbox_sort_button
    dynamic_window.mailflow_mailbox_table = mailbox_table
    dynamic_window.mailflow_mailbox_status_label = mailbox_status_label
    dynamic_window.mailflow_mailbox_projectflow_button = mailbox_projectflow_button
    dynamic_window.mailflow_projectflow_input = projectflow_input
    dynamic_window.mailflow_projectflow_status = projectflow_status
    dynamic_window.mailflow_mail_views = mail_views
    dynamic_window.mailflow_mail_view_toggle = mail_view_toggle
    dynamic_window.mailflow_set_mail_view = set_mail_view
    dynamic_window.mailflow_queue_list = queue_list
    dynamic_window.mailflow_queue_header_info = queue_header_info
    dynamic_window.mailflow_queue_archive_button = queue_archive_button
    dynamic_window.mailflow_queue_detail_stack = queue_detail_stack
    dynamic_window.mailflow_queue_subject_label = queue_subject_label
    dynamic_window.mailflow_decision_kicker = decision_kicker
    dynamic_window.mailflow_decision_figure = decision_figure
    dynamic_window.mailflow_decision_destination_combo = decision_destination_combo
    dynamic_window.mailflow_decision_role = decision_role
    dynamic_window.mailflow_decision_warning = decision_warning
    dynamic_window.mailflow_decision_validate_button = decision_validate_button
    dynamic_window.mailflow_bulk_category = bulk_category
    dynamic_window.mailflow_bulk_role = bulk_role
    dynamic_window.mailflow_bulk_apply_button = bulk_apply_button
    dynamic_window.mailflow_bulk_values = queue_bulk_values
    dynamic_window.mailflow_tree_folder_title = tree_folder_title
    dynamic_window.mailflow_tree_decision_title = tree_decision_title
    dynamic_window.mailflow_tree_merge_duplicate_button = tree_merge_duplicate_button
    dynamic_window.mailflow_tree_ignore_duplicate_button = tree_ignore_duplicate_button
    dynamic_window.mailflow_directory_list = directory_list
    dynamic_window.mailflow_directory_views = directory_views
    dynamic_window.mailflow_directory_view_toggle = directory_view_toggle
    dynamic_window.mailflow_directory_filter = directory_filter
    dynamic_window.mailflow_directory_search_input = directory_search_input
    dynamic_window.mailflow_directory_name_label = directory_name_label
    dynamic_window.mailflow_directory_role_choice = directory_role_choice
    dynamic_window.mailflow_directory_validate_button = directory_validate_button
    dynamic_window.mailflow_directory_figure = directory_figure
    dynamic_window.mailflow_mailbox_detail_stack = mailbox_detail_stack
    dynamic_window.mailflow_mailbox_destination_title = mailbox_destination_title
    dynamic_window.mailflow_settings_sections_list = settings_sections_list
    dynamic_window.mailflow_settings_heading = settings_heading
    dynamic_window.mailflow_provider_cards = {
        key: card for key, (card, _kicker, _text) in provider_cards.items()
    }
    dynamic_window.mailflow_ai_mode_choice = ai_mode_choice
    dynamic_window.mailflow_threshold_slider = threshold_slider
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


def ai_provider_hint_text(provider: str) -> str:
    if provider == "ollama":
        return (
            "Les mails sont analysés sur ce PC. Aucune clé API requise ; aucun envoi à OpenAI. "
            "Ollama doit être démarré et le modèle installé."
        )
    if provider == "jev":
        return (
            "Les informations utilisées pour le classement sont envoyées à l'API Jev de "
            "TypeSafe. Jev choisit la phase commerciale parmi des options fixes et donne "
            "ses probabilités ; il ne rédige pas de texte. Aucun envoi à OpenAI."
        )
    return "Les informations utilisées pour le classement sont envoyées à l'API OpenAI."


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
        color, background = COLORS["warning"], COLORS["warning_bg"]
    elif not has_key or valid is False:
        color, background = COLORS["danger"], "#f6e8e8"
    elif valid is True:
        color, background = COLORS["success"], "#e7f0eb"
    else:
        color, background = COLORS["text"], COLORS["neutral_100"]
    return (
        f"QLabel {{ color: {color}; background: {background}; "
        f"border: 1px solid {COLORS['rule']}; border-radius: 0; padding: 4px 8px; }}"
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
