"""Review box: shows the dictated text before it is typed, so it can be edited by voice.

Like the capsule it never takes focus (WindowDoesNotAcceptFocus, WA_ShowWithoutActivating,
WS_EX_NOACTIVATE): the target app keeps the keyboard focus, so the text is typed into it on Enter
without a focus round trip. Enter, Esc and Ctrl+Z reach QwenType through the keyboard hook.

While the box is open it also stands in for the capsule's status API (show_listening, set_text,
show_status, show_notice, show_error, dismiss, hide_now, set_level), shown in its status row,
so the recording state machine can drive either window.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import win32
from .capsule import (
    BOTTOM_MARGIN,
    COLOR_ERROR,
    COLOR_PLACEHOLDER,
    COLOR_STATUS,
    COLOR_TEXT,
    WaveformWidget,
    screen_for_foreground,
)

WIDTH = 640
RADIUS = 16
COLOR_HINT = QColor(245, 246, 250, 120)


def _label(font: QFont, pixel_size: int, wrap: bool) -> QLabel:
    label = QLabel()
    f = QFont(font)
    f.setPixelSize(pixel_size)
    label.setFont(f)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return label


def _set_color(label: QLabel, color: QColor) -> None:
    label.setStyleSheet(f"color: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})")


class ReviewBox(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("QwenType")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        font = QFont()
        font.setFamilies(
            ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Yu Gothic UI", "Malgun Gothic", "sans-serif"]
        )
        self.title = _label(font, 12, wrap=False)
        self.title.setText("Review")
        _set_color(self.title, COLOR_STATUS)
        self.hint = _label(font, 12, wrap=False)
        _set_color(self.hint, COLOR_HINT)
        self.body = _label(font, 16, wrap=True)
        _set_color(self.body, COLOR_TEXT)
        self.wave = WaveformWidget(self)
        self.wave.setFixedSize(33, 24)
        self.wave.hide()
        self.status = _label(font, 14, wrap=True)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.hint)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        status_row.addWidget(self.wave, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self.status, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self.body)
        layout.addLayout(status_row)

        self._status_text = ""
        self._status_color = COLOR_PLACEHOLDER
        self._saved: tuple[str, QColor] | None = None
        self._bottom = QPoint(0, 0)
        self._width = WIDTH
        self._restore = QTimer(self)
        self._restore.setSingleShot(True)
        self._restore.timeout.connect(self._restore_status)
        self._set_status("", COLOR_PLACEHOLDER)

    # -- box ----------------------------------------------------------------------

    def set_hint(self, hotkey_name: str) -> None:
        self.hint.setText(f"{hotkey_name}: edit by voice · Enter: type · Esc: discard · Ctrl+Z: undo")

    def open(self, text: str) -> None:
        avail = screen_for_foreground().availableGeometry()
        self._width = min(WIDTH, avail.width() - 32)
        self._bottom = QPoint(avail.center().x(), avail.bottom() - BOTTOM_MARGIN)
        # Very long text is cut off at the bottom of the box (it is still typed in full).
        self.body.setMaximumHeight(max(80, int(avail.height() * 0.45)))
        self.hide_now()
        self.set_body(text)
        self.show()

    def set_body(self, text: str) -> None:
        self.body.setText(text)
        self._relayout()

    def close_box(self) -> None:
        self.hide_now()
        self.hide()

    @property
    def text(self) -> str:
        return self.body.text()

    # -- status API shared with the capsule ------------------------------------------

    def show_listening(self) -> None:
        self._restore.stop()
        self._saved = None
        self.wave.show()
        self.wave.start()
        self._set_status("Speak an edit instruction…", COLOR_PLACEHOLDER)

    def set_text(self, text: str) -> None:
        """Live transcript of the instruction being spoken."""
        if self._restore.isActive():
            self._saved = (text, COLOR_TEXT)
            return
        self._set_status(text, COLOR_TEXT)

    def show_status(self, text: str) -> None:
        self._restore.stop()
        self._saved = None
        self._stop_wave()
        self._set_status(text, COLOR_STATUS)

    def show_notice(self, text: str, ms: int = 1500) -> None:
        if self._saved is None:
            self._saved = (self._status_text, self._status_color)
        self._set_status(text, COLOR_STATUS)
        self._restore.start(ms)

    def show_error(self, text: str, ms: int = 2500) -> None:
        self._stop_wave()
        self._saved = ("", COLOR_PLACEHOLDER)
        self._set_status(text, COLOR_ERROR)
        self._restore.start(ms)

    def dismiss(self) -> None:
        """Clear the status row (the box itself stays until Enter or Esc)."""
        self.hide_now()

    def hide_now(self) -> None:
        self._restore.stop()
        self._saved = None
        self._stop_wave()
        self._set_status("", COLOR_PLACEHOLDER)

    def set_level(self, level: float) -> None:
        self.wave.set_level(level)

    # -- internals --------------------------------------------------------------------

    def _stop_wave(self) -> None:
        self.wave.stop()
        self.wave.hide()

    def _restore_status(self) -> None:
        if self._saved is not None:
            text, color = self._saved
            self._saved = None
            self._set_status(text, color)

    def _set_status(self, text: str, color: QColor) -> None:
        self._status_text = text
        self._status_color = color
        self.status.setText(text)
        _set_color(self.status, color)
        self.status.setVisible(bool(text) or self.wave.isVisible())
        self._relayout()

    def _relayout(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        layout.activate()
        h = layout.totalHeightForWidth(self._width) if layout.hasHeightForWidth() else layout.sizeHint().height()
        h = max(h, layout.totalMinimumSize().height())
        self.setGeometry(self._bottom.x() - self._width // 2, self._bottom.y() - h, self._width, h)
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        win32.make_overlay_window(int(self.winId()))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(22, 24, 30, 235))
        p.setPen(QPen(QColor(255, 255, 255, 38), 1.0))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
