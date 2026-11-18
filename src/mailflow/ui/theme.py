"""Industry design tokens and the desktop stylesheet; no Qt import is needed to inspect them.

Industry is a wireframe look: steel blue on a light technical ground, Barlow Condensed
headings over Barlow, square hairline frames with "+" registration marks. The tokens
below mirror the Claude Design "Industry" system so the desktop app and the mockups
stay in step.
"""

from typing import Any

from mailflow.resources import ASSETS_DIR

FONTS_DIR = ASSETS_DIR / "fonts"
ICONS_DIR = ASSETS_DIR / "icons"
BODY_FONT = "Barlow"
HEADING_FONT = "Barlow Condensed"

COLORS = {
    "bg": "#f2f2f3",
    "surface": "#e9e9ea",
    "text": "#1d1f20",
    # Text at 16 % (frame lines) and 8 % (row rules) over the ground.
    "divider": "#d0d0d1",
    "rule": "#e1e1e2",
    # The mockups mute text to 55 %; this step keeps 4.5:1 on the ground.
    "muted": "#6b6c6e",
    "accent": "#5980a6",
    "accent_100": "#eef6ff",
    "accent_200": "#d6ebff",
    "accent_300": "#b5d9fd",
    "accent_400": "#94bce3",
    "accent_600": "#4d7194",
    "accent_700": "#416180",
    "accent_800": "#2c455d",
    "accent_900": "#1d2d3d",
    "neutral_100": "#f5f5f8",
    "neutral_300": "#d4d4d7",
    "neutral_500": "#98989b",
    "neutral_700": "#5d5d60",
    "neutral_800": "#424244",
    "mark": "#7d7e7f",
    "disabled": "#a9aaab",
    # Status feedback stays quiet so the steel accent remains the only color.
    "success": "#2f6b4f",
    "danger": "#9b2c2c",
    "warning": "#8a5a00",
    "warning_bg": "#f6efe1",
}

# The stylesheet is designed for a light palette. Windows dark mode would otherwise
# give unstyled parts, such as open drop-down lists, dark backgrounds under dark text.
LIGHT_PALETTE = {
    "Window": COLORS["bg"],
    "WindowText": COLORS["text"],
    "Base": COLORS["bg"],
    "AlternateBase": COLORS["neutral_100"],
    "Text": COLORS["text"],
    "PlaceholderText": COLORS["muted"],
    "Button": COLORS["bg"],
    "ButtonText": COLORS["text"],
    "BrightText": COLORS["bg"],
    "Highlight": COLORS["accent_200"],
    "HighlightedText": COLORS["text"],
    "ToolTipBase": COLORS["accent_900"],
    "ToolTipText": COLORS["bg"],
    "Link": COLORS["accent_700"],
}

_STYLESHEET_TEMPLATE = """
QMainWindow, QWidget#workspace { background: @bg; color: @text; }
QWidget { font-family: "Barlow"; font-size: 14px; }
QDialog, QMessageBox, QProgressDialog { background: @bg; }
QLabel { color: @text; background: transparent; }
QLabel[role="title"] { font-family: "Barlow Condensed"; font-size: 28px; font-weight: 600; }
QLabel[role="display"] { font-family: "Barlow Condensed"; font-size: 32px; font-weight: 600; }
QLabel[role="heading"] { font-family: "Barlow Condensed"; font-size: 20px; font-weight: 600; }
QLabel[role="statement"] { font-family: "Barlow Condensed"; font-size: 28px; }
QLabel[role="figure"] { font-family: "Barlow Condensed"; font-size: 68px; font-weight: 600; }
QLabel[role="kicker"] { color: @accent_700; font-size: 10px; }
QLabel[role="field"] { color: #4f5052; font-size: 12px; }
QLabel[role="muted"] { color: @muted; }
QLabel[role="small"] { color: @muted; font-size: 12px; }
QLabel[role="summary"] { color: @muted; font-size: 13px; }
QLabel[role="warning"] { color: @warning; font-weight: 600; }
QLabel[tag="accent"] { background: @accent_100; color: @accent_800; font-size: 11px;
    padding: 3px 10px; }
QLabel[tag="outline"] { border: 1px solid @accent; color: @accent_700; font-size: 11px;
    padding: 2px 9px; }
QLabel[tag="neutral"] { background: @neutral_100; color: @neutral_800; font-size: 11px;
    padding: 2px 9px; border: 1px solid @rule; }
QLabel[tag="solid"] { background: @accent; color: @bg; font-size: 11px; padding: 3px 10px; }
QFrame[pane="list"] { background: @bg; border: none; border-right: 1px solid @divider; }
QFrame[pane="detail"] { background: @bg; border: none; }
QFrame[pane="decision"] { background: @accent_100; border: none;
    border-left: 1px solid @divider; }
QFrame[pane="header"] { background: transparent; border: none;
    border-bottom: 1px solid @divider; }
QFrame[pane="footer"] { background: transparent; border: none;
    border-top: 1px solid @divider; }
QFrame[pane="bar"] { background: @bg; border: none; border-bottom: 1px solid @divider; }
QFrame#navColumn { background: @bg; border: none; border-right: 1px solid @divider; }
QFrame[pane="decision"] QLineEdit, QFrame[pane="decision"] QComboBox { background: @bg; }
QGroupBox { border: 1px solid @divider; border-radius: 0; margin-top: 18px;
    padding: 14px 10px 10px; font-weight: 600; background: transparent; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: @text;
    font-family: "Barlow Condensed"; font-size: 16px; }
QLineEdit, QComboBox, QDoubleSpinBox { background: @surface; color: @text;
    border: 1px solid @divider; border-radius: 0; padding: 6px 10px; min-height: 22px;
    selection-background-color: @accent_200; selection-color: @text; }
QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover { border-color: #8c8d8e; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border: 1px solid @accent; }
QComboBox::drop-down { width: 24px; border: none; }
QComboBox::down-arrow { image: url("__CHEVRON_DOWN__"); width: 14px; height: 14px; }
QComboBox:disabled, QLineEdit:disabled { background: @bg; color: @disabled;
    border-color: @rule; }
QComboBox QAbstractItemView { background: @bg; color: @text; border: 1px solid @divider;
    selection-background-color: @accent_100; selection-color: @text; outline: none; }
QPushButton, QToolButton { background: transparent; color: @text;
    border: 1px solid @divider; border-radius: 0; padding: 7px 12px; min-height: 18px;
    font-family: "Barlow Condensed"; font-size: 15px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: #e0e0e1; }
QPushButton:pressed, QToolButton:pressed { background: @divider; }
QPushButton:focus, QToolButton:focus { border: 1px solid @accent; }
QPushButton:checked { background: @accent; color: @bg; border-color: @accent; }
QPushButton[role="primary"], QToolButton[role="primary"] { background: @accent;
    border: 1px solid @accent; color: @bg; }
QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover {
    background: @accent_600; border-color: @accent_600; }
QPushButton[role="primary"]:pressed, QToolButton[role="primary"]:pressed {
    background: @accent_700; }
QPushButton[blueprint="true"] { margin: 5px; }
QPushButton[role="ghost"] { border-color: transparent; color: @accent_700;
    text-align: left; padding: 7px 4px; }
QPushButton[role="ghost"]:hover { background: #e3e8ee; }
QPushButton[role="segment"] { background: transparent; border: 1px solid @divider;
    font-family: "Barlow"; font-size: 13px; font-weight: 400; padding: 6px 12px; }
QPushButton[role="segment"]:hover { background: #e0e0e1; }
QPushButton[role="segment"]:checked { background: @accent; color: @bg;
    border-color: @accent; }
QPushButton[role="segment"]:disabled { color: @disabled; }
QPushButton[role="card"] { text-align: left; padding: 10px 12px; border: 1px solid @divider;
    font-family: "Barlow"; font-size: 13px; font-weight: 400; background: transparent; }
QPushButton[role="card"]:hover { background: #e3e8ee; }
QPushButton[role="card"]:checked { background: @accent_100; color: @text;
    border: 1px solid @accent; }
QPushButton:disabled, QToolButton:disabled { background: transparent; color: @disabled;
    border-color: @rule; }
QPushButton[role="primary"]:disabled, QToolButton[role="primary"]:disabled {
    background: #a9bdd1; border-color: #a9bdd1; color: @bg; }
QCheckBox { spacing: 8px; color: @text; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #8c8d8e;
    background: @bg; }
QCheckBox::indicator:hover { border-color: @accent; }
QCheckBox::indicator:checked { background: @accent; border-color: @accent;
    image: url("__CHECK__"); }
QCheckBox::indicator:disabled { border-color: @rule; background: @surface; }
QListWidget#navigation { background: transparent; color: @text; border: none;
    padding: 0; outline: none; }
QListWidget#navigation::item { padding: 11px 16px; border-left: 2px solid transparent; }
QListWidget#navigation::item:selected { background: @accent_200; color: @text;
    border-left: 2px solid @accent; font-weight: 600; }
QListWidget#navigation::item:hover:!selected { background: #e3e8ee; }
QListWidget[pane="queue"] { background: transparent; border: none; outline: none; }
QListWidget[pane="queue"]::item { border-bottom: 1px solid @rule;
    border-left: 2px solid transparent; padding: 0; }
QListWidget[pane="queue"]::item:selected { background: @accent_100; color: @text;
    border-left: 2px solid @accent; }
QListWidget[pane="queue"]::item:hover:!selected { background: #e7ebf0; }
QTableWidget, QTreeWidget, QTextEdit, QTextBrowser, QListWidget, QScrollArea {
    background: @bg; color: @text; border: 1px solid @divider; border-radius: 0;
    alternate-background-color: @neutral_100;
    selection-background-color: @accent_100; selection-color: @text; }
QScrollArea[pane="plain"], QTextBrowser[pane="plain"] { border: none;
    background: transparent; }
QTreeWidget[pane="plain"], QTableWidget[pane="plain"] { border: none; }
QTableWidget { gridline-color: @rule; }
QTableWidget::item { padding: 5px 7px; border-bottom: 1px solid @rule; }
QTableWidget::item:selected, QTreeWidget::item:selected { background: @accent_100;
    color: @text; }
QTreeWidget::item { padding: 6px 2px; border-bottom: 1px solid @rule; }
QTreeWidget::item:hover:!selected { background: #e7ebf0; }
QTableWidget QComboBox { border: none; border-radius: 0; padding: 3px 7px;
    background: transparent; }
QHeaderView::section { background: transparent; color: @muted; border: none;
    border-bottom: 1px solid @divider; padding: 8px 8px; font-size: 11px;
    font-weight: 600; }
QTabWidget::pane { border: 1px solid @divider; background: @bg; border-radius: 0; }
QTabBar::tab { background: transparent; color: @muted; padding: 9px 15px;
    border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: @accent_700; border-bottom: 2px solid @accent;
    font-weight: 600; }
QSplitter::handle { background: @divider; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle { background: @neutral_300; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: @neutral_500; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSlider::groove:horizontal { height: 4px; background: @neutral_300; }
QSlider::sub-page:horizontal { background: @accent; }
QSlider::handle:horizontal { background: @bg; border: 1px solid @accent; width: 12px;
    margin: -6px 0; }
QMenu { background: @bg; color: @text; border: 1px solid @divider; padding: 4px; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: @accent_100; }
QMenu::item:disabled { color: @disabled; }
QMenu::separator { height: 1px; background: @rule; margin: 4px 8px; }
QToolTip { background: @accent_900; color: @bg; border: none; padding: 6px; }
"""


def _expand_tokens(stylesheet: str) -> str:
    # Longest names first so "@accent_100" is never read as "@accent" followed by "_100".
    for name in sorted(COLORS, key=len, reverse=True):
        stylesheet = stylesheet.replace(f"@{name}", COLORS[name])
    return stylesheet.replace(
        "__CHEVRON_DOWN__", (ASSETS_DIR / "chevron-down.svg").as_posix()
    ).replace("__CHECK__", (ASSETS_DIR / "check.svg").as_posix())


APP_STYLESHEET = _expand_tokens(_STYLESHEET_TEMPLATE)


def icon_path(name: str) -> str:
    return (ICONS_DIR / f"{name}.svg").as_posix()


_loaded_font_families: list[str] = []


def load_brand_fonts() -> list[str]:
    """Register the bundled Barlow fonts once; return the families Qt now knows."""
    from PySide6.QtGui import QFontDatabase

    if _loaded_font_families:
        return list(_loaded_font_families)
    families: set[str] = set()
    for font_file in sorted(FONTS_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(font_file))
        if font_id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(font_id))
    _loaded_font_families.extend(sorted(families))
    return list(_loaded_font_families)


def apply_light_theme(app: Any) -> None:
    """Keep MailFlow light even when the operating system uses a dark theme."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPalette

    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8 and later
        hints.setColorScheme(Qt.ColorScheme.Light)
    if BODY_FONT in load_brand_fonts():
        font = QFont(BODY_FONT)
        font.setPixelSize(14)
        app.setFont(font)
    palette = QPalette()
    for role_name, color in LIGHT_PALETTE.items():
        role = getattr(QPalette.ColorRole, role_name)
        palette.setColor(QPalette.ColorGroup.All, role, QColor(color))
    for role_name in ("WindowText", "Text", "ButtonText"):
        role = getattr(QPalette.ColorRole, role_name)
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS["disabled"]))
    app.setPalette(palette)
