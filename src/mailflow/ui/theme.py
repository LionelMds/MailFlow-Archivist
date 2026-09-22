"""Shared desktop presentation tokens; no Qt import is needed to inspect them."""

from mailflow.resources import ASSETS_DIR

APP_STYLESHEET = """
QMainWindow, QWidget#workspace { background: #f3f6fa; color: #243247; }
QWidget { font-family: "Segoe UI"; font-size: 10pt; }
QLabel { color: #243247; background: transparent; }
QLabel[role="title"] { font-size: 21pt; font-weight: 700; color: #173653; }
QLabel[role="heading"] { font-size: 15pt; font-weight: 600; color: #173653; }
QLabel[role="muted"] { color: #56677a; }
QLabel[role="summary"] { background: #e8eff7; color: #173653; padding: 9px 12px;
    border-radius: 7px; font-weight: 600; }
QWidget#scanPanel, QWidget#emptyState { background: white; border-radius: 9px; }
QGroupBox { border: 1px solid #d7e0ea; border-radius: 8px; margin-top: 18px;
    padding: 14px 10px 10px; font-weight: 600; background: white; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #173653; }
QLineEdit, QComboBox { background: white; color: #243247; border: 1px solid #bccbda;
    border-radius: 5px; padding: 6px 9px; min-height: 20px; selection-background-color: #dbeafe;
    selection-color: #173653; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #246b9e; padding: 5px 8px; }
QComboBox::drop-down { width: 22px; border: none; }
QComboBox::down-arrow { image: url("__CHEVRON_DOWN__"); width: 14px; height: 14px; }
QComboBox:disabled { background: #edf1f5; color: #728094; }
QPushButton, QToolButton { background: white; color: #243247; border: 1px solid #bccbda;
    border-radius: 5px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover, QToolButton:hover { background: #eaf1f8; border-color: #718ba4; }
QPushButton:pressed, QToolButton:pressed { background: #dbe7f3; }
QPushButton:focus, QToolButton:focus { border: 2px solid #246b9e; padding: 6px 11px; }
QPushButton[role="primary"], QToolButton[role="primary"] { background: #17618f;
    border-color: #17618f; color: white; font-weight: 600; }
QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover { background: #124c70; }
QPushButton:disabled, QToolButton:disabled { background: #edf1f5; color: #728094;
    border-color: #d7e0ea; }
QCheckBox { spacing: 7px; color: #34465d; }
QListWidget#navigation { background: #173653; color: #e5edf5; border: none;
    border-radius: 8px; padding: 8px 4px; outline: none; }
QListWidget#navigation::item { padding: 13px 12px; margin: 3px 4px; border-radius: 5px; }
QListWidget#navigation::item:selected { background: #d9eafa; color: #123b5b; font-weight: 600; }
QListWidget#navigation::item:hover:!selected { background: #244865; }
QTableWidget, QTreeWidget, QTextEdit, QScrollArea { background: white; color: #243247;
    border: 1px solid #d7e0ea; border-radius: 6px; alternate-background-color: #f7f9fc;
    selection-background-color: #dbeafe; selection-color: #173653; }
QTableWidget { gridline-color: #edf1f5; }
QTableWidget::item { padding: 5px 7px; }
QTableWidget QComboBox { border: none; border-radius: 0; padding: 3px 7px; }
QHeaderView::section { background: #edf2f7; color: #42546b; border: none;
    border-bottom: 1px solid #d7e0ea; border-right: 1px solid #e1e8f0;
    padding: 9px 8px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #d7e0ea; background: white; border-radius: 5px; }
QTabBar::tab { background: #e8eff7; color: #42546b; padding: 9px 15px;
    border: 1px solid #d7e0ea; border-bottom: none; }
QTabBar::tab:selected { background: white; color: #173653; font-weight: 600; }
QSplitter::handle { background: #f3f6fa; }
QMenu { background: white; color: #243247; border: 1px solid #bccbda; padding: 5px; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: #dbeafe; }
QMenu::item:disabled { color: #728094; }
QToolTip { background: #173653; color: white; border: none; padding: 6px; }
""".replace("__CHEVRON_DOWN__", (ASSETS_DIR / "chevron-down.svg").as_posix())
