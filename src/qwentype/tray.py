"""System tray icon (drawn in code) and menu."""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import __version__
from .settings import LANGUAGES, Settings


def draw_icon(size: int = 64, active: bool = False) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0
    p.scale(s, s)

    grad = QLinearGradient(0, 0, 64, 64)
    if active:
        grad.setColorAt(0, QColor(255, 120, 120))
        grad.setColorAt(1, QColor(220, 60, 110))
    else:
        grad.setColorAt(0, QColor(96, 165, 250))
        grad.setColorAt(1, QColor(139, 92, 246))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 16, 16)

    white = QColor(255, 255, 255)
    p.setBrush(white)
    p.drawRoundedRect(QRectF(24, 10, 16, 28), 8, 8)  # microphone capsule
    pen = QPen(white, 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc = QPainterPath()
    arc.moveTo(QPointF(16, 30))
    arc.cubicTo(QPointF(16, 50), QPointF(48, 50), QPointF(48, 30))
    p.drawPath(arc)
    p.drawLine(QPointF(32, 45), QPointF(32, 53))
    p.drawLine(QPointF(24, 54), QPointF(40, 54))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 256):
        icon.addPixmap(draw_icon(size))
    return icon


class Tray(QObject):
    language_changed = Signal(str)
    asr_server_requested = Signal()
    llm_toggled = Signal(bool)
    llm_settings_requested = Signal()
    autostart_toggled = Signal(bool)
    menu_opened = Signal()
    quit_requested = Signal()

    def __init__(self, settings: Settings, autostart: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._icon_idle = app_icon()
        self._icon_active = QIcon(draw_icon(64, active=True))
        self._status = "…"

        self.menu = QMenu()
        self.menu.setTitle("QwenType")
        self.menu.setToolTipsVisible(True)

        self.status_action = QAction("ASR: …", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        lang_menu = self.menu.addMenu("Language")
        group = QActionGroup(lang_menu)
        group.setExclusive(True)
        self._lang_actions: dict[str, QAction] = {}
        for label, code in LANGUAGES:
            act = QAction(label, lang_menu, checkable=True)
            act.setChecked(code == settings.language)
            act.triggered.connect(lambda _checked=False, c=code: self.language_changed.emit(c))
            group.addAction(act)
            lang_menu.addAction(act)
            self._lang_actions[code] = act

        self.menu.addAction("ASR Server…", self.asr_server_requested.emit)

        llm_menu = self.menu.addMenu("LLM Refinement")
        self.llm_action = QAction("Enable", llm_menu, checkable=True)
        self.llm_action.setChecked(settings.llm_enabled)
        self.llm_action.toggled.connect(self.llm_toggled.emit)
        llm_menu.addAction(self.llm_action)
        llm_menu.addAction("Settings…", self.llm_settings_requested.emit)

        self.autostart_action = QAction("Start with Windows", self.menu, checkable=True)
        self.autostart_action.setChecked(autostart)
        self.autostart_action.toggled.connect(self.autostart_toggled.emit)
        self.menu.addAction(self.autostart_action)

        self.menu.addSeparator()
        self.menu.addAction("Quit", self.quit_requested.emit)
        self.menu.aboutToShow.connect(self.menu_opened.emit)

        self.icon = QSystemTrayIcon(self._icon_idle)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)
        self._update_tooltip()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def set_status(self, status: str) -> None:
        self._status = status
        self.status_action.setText(f"ASR: {status}")
        self._update_tooltip()

    def set_recording(self, recording: bool) -> None:
        self.icon.setIcon(self._icon_active if recording else self._icon_idle)

    def set_llm_checked(self, checked: bool) -> None:
        self.llm_action.blockSignals(True)
        self.llm_action.setChecked(checked)
        self.llm_action.blockSignals(False)

    def set_autostart_checked(self, checked: bool) -> None:
        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(checked)
        self.autostart_action.blockSignals(False)

    def _update_tooltip(self) -> None:
        self.icon.setToolTip(f"QwenType {__version__} — ASR: {self._status}\nHold Right Ctrl to dictate")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.menu_opened.emit()  # left click: refresh the status in the tooltip
