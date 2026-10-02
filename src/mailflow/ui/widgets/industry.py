"""Industry building blocks for the desktop window.

The design system draws cards, figures and the primary button as blueprint objects:
square, hairline-bordered, with "+" registration marks at the corners. Qt cannot draw
outside a widget, so these widgets reserve a few pixels around their box for the marks.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mailflow.ui.theme import COLORS, HEADING_FONT

MARK_OFFSET = 6
BUTTON_MARGIN = 5
MARK_ARM = 5
ALL_CORNERS = ("tl", "tr", "bl", "br")


def repolish(widget: QWidget) -> None:
    """Apply a changed dynamic property to the stylesheet."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def draw_registration_marks(
    painter: QPainter,
    box: QRect,
    corners: Sequence[str] = ALL_CORNERS,
) -> None:
    painter.setPen(QPen(QColor(COLORS["mark"]), 1))
    points = {
        "tl": (box.left(), box.top()),
        "tr": (box.right(), box.top()),
        "bl": (box.left(), box.bottom()),
        "br": (box.right(), box.bottom()),
    }
    for corner in corners:
        x, y = points[corner]
        painter.drawLine(x - MARK_ARM, y, x + MARK_ARM, y)
        painter.drawLine(x, y - MARK_ARM, x, y + MARK_ARM)


class BlueprintFrame(QFrame):
    """A transparent hairline frame with the four corner registration marks."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        padding: int = 0,
        fill: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._fill = fill
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContentsMargins(
            MARK_OFFSET + 1, MARK_OFFSET + 1, MARK_OFFSET + 1, MARK_OFFSET + 1
        )
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(6)

    def box(self) -> QRect:
        return self.rect().adjusted(MARK_OFFSET, MARK_OFFSET, -MARK_OFFSET - 1, -MARK_OFFSET - 1)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        box = self.box()
        if self._fill:
            painter.fillRect(box, QColor(self._fill))
        painter.setPen(QPen(QColor(COLORS["divider"]), 1))
        painter.drawRect(box)
        draw_registration_marks(painter, box)
        painter.end()


class BlueprintButton(QPushButton):
    """The primary action: a solid accent box carrying two registration marks."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "primary")
        self.setProperty("blueprint", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        box = self.rect().adjusted(
            BUTTON_MARGIN, BUTTON_MARGIN, -BUTTON_MARGIN - 1, -BUTTON_MARGIN - 1
        )
        draw_registration_marks(painter, box, ("tl", "br"))
        painter.end()


class SegmentedControl(QWidget):
    """Mutually exclusive choices drawn as one segmented strip, like `.seg` in Industry."""

    changed = Signal(object)

    def __init__(
        self,
        options: Sequence[tuple[str, object]],
        parent: QWidget | None = None,
        *,
        vertical: bool = False,
        allow_none: bool = False,
    ) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(not allow_none)
        self._values: list[object] = []
        self._buttons: list[QPushButton] = []
        layout: QVBoxLayout | QHBoxLayout = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for index, (label, value) in enumerate(options):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("role", "segment")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if index:
                # Neighbouring segments share one hairline instead of drawing two.
                button.setStyleSheet(
                    "QPushButton { border-top: none; }"
                    if vertical
                    else "QPushButton { border-left: none; }"
                )
            if not vertical:
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._group.addButton(button, index)
            self._values.append(value)
            self._buttons.append(button)
            layout.addWidget(button)
        self._group.idClicked.connect(self._emit_changed)

    def _emit_changed(self, index: int) -> None:
        self.changed.emit(self._values[index])

    def buttons(self) -> list[QPushButton]:
        return list(self._buttons)

    def value(self) -> object | None:
        index = self._group.checkedId()
        return None if index < 0 else self._values[index]

    def set_value(self, value: object | None) -> None:
        """Select an option without emitting `changed`; None clears the choice."""
        exclusive = self._group.exclusive()
        self._group.setExclusive(False)
        for button, option in zip(self._buttons, self._values, strict=True):
            button.setChecked(value is not None and option == value)
        self._group.setExclusive(exclusive)

    def set_label(self, value: object, label: str) -> None:
        for button, option in zip(self._buttons, self._values, strict=True):
            if option == value:
                button.setText(label)


class ConfidenceBar(QWidget):
    """A thin confidence gauge with the review threshold drawn as a dashed tick."""

    def __init__(self, parent: QWidget | None = None, *, height: int = 6) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._threshold: float | None = None
        self._bar_height = height
        self.setFixedHeight(height + 10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, value: float, threshold: float | None = None) -> None:
        self._value = min(max(value, 0.0), 1.0)
        self._threshold = threshold
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        top = (self.height() - self._bar_height) // 2
        track = QRectF(0, top, self.width(), self._bar_height)
        painter.fillRect(track, QColor(COLORS["neutral_300"]))
        fill = QRectF(0, top, self.width() * self._value, self._bar_height)
        painter.fillRect(fill, QColor(COLORS["accent"]))
        if self._threshold is not None:
            x = round(self.width() * min(max(self._threshold, 0.0), 1.0))
            pen = QPen(QColor(COLORS["text"]), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(x, 0, x, self.height())
        painter.end()


class SplitBar(QWidget):
    """A 4 px progress strip split into weighted segments with 2 px gaps."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[tuple[int, str]] = []
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_segments(self, segments: Sequence[tuple[int, str]]) -> None:
        self._segments = [(weight, color) for weight, color in segments if weight > 0]
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        if not self._segments:
            painter.fillRect(self.rect(), QColor(COLORS["neutral_300"]))
            painter.end()
            return
        gap = 2
        total = sum(weight for weight, _color in self._segments)
        available = self.width() - gap * (len(self._segments) - 1)
        x = 0.0
        for weight, color in self._segments:
            width = available * weight / total
            painter.fillRect(QRectF(x, 0, width, self.height()), QColor(color))
            x += width + gap
        painter.end()


def text_label(text: str = "", role: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role is not None:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    return label


def kicker_label(text: str = "") -> QLabel:
    """The small uppercase, letter-spaced caption that opens a decision panel."""
    label = QLabel(text.upper())
    label.setProperty("role", "kicker")
    font = label.font()
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
    label.setFont(font)
    return label


def set_kicker_text(label: QLabel, text: str) -> None:
    label.setText(text.upper())


def tag_label(text: str = "", kind: str = "neutral") -> QLabel:
    label = QLabel(text)
    label.setProperty("tag", kind)
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    label.setVisible(bool(text))
    return label


def set_tag(label: QLabel, text: str, kind: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))
    if label.property("tag") != kind:
        label.setProperty("tag", kind)
        repolish(label)


def pane(kind: str, *, width: int | None = None) -> QFrame:
    """A layout column: "list" (left), "detail" (centre) or "decision" (right)."""
    frame = QFrame()
    frame.setProperty("pane", kind)
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    if width is not None:
        frame.setFixedWidth(width)
    return frame


def field_block(label_text: str, widget: QWidget) -> QWidget:
    """A form field: a small caption above its control, like `.field > label`."""
    block = QWidget()
    layout = QVBoxLayout(block)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    caption = text_label(label_text, "field")
    caption.setBuddy(widget)
    layout.addWidget(caption)
    layout.addWidget(widget)
    return block


def heading_font(pixel_size: int, *, semibold: bool = True) -> QFont:
    font = QFont(HEADING_FONT)
    font.setPixelSize(pixel_size)
    if semibold:
        font.setWeight(QFont.Weight.DemiBold)
    return font
