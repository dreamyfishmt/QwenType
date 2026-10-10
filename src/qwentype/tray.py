"""System tray icon (drawn in code) and menu."""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import __version__
from .hotkey import HOTKEYS, get_hotkey
from .settings import LANGUAGES, Settings

RECENT_LABEL_CHARS = 48


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
    hotkey_changed = Signal(str)
    hotkey_mode_changed = Signal(str)
    recent_selected = Signal(str)
    recent_cleared = Signal()
    settings_requested = Signal(str)  # page to open: "asr", "llm" or "advanced"
    llm_toggled = Signal(bool)
    autostart_toggled = Signal(bool)
    menu_opened = Signal()
    quit_requested = Signal()

    def __init__(self, settings: Settings, autostart: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._icon_idle = app_icon()
        self._icon_active = QIcon(draw_icon(64, active=True))
        self._status = "…"
        self._hint = ""

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

        hotkey_menu = self.menu.addMenu("Hotkey")
        hotkey_menu.setToolTipsVisible(True)
        group = QActionGroup(hotkey_menu)
        group.setExclusive(True)
        self._hotkey_actions: dict[str, QAction] = {}
        for hk in HOTKEYS.values():
            act = QAction(hk.label, hotkey_menu, checkable=True)
            act.triggered.connect(lambda _checked=False, i=hk.id: self.hotkey_changed.emit(i))
            group.addAction(act)
            hotkey_menu.addAction(act)
            self._hotkey_actions[hk.id] = act
        hotkey_menu.addSeparator()
        self.toggle_action = QAction("Tap to start, tap to stop", hotkey_menu, checkable=True)
        self.toggle_action.setToolTip("Toggle mode: no need to hold the key. Esc cancels a recording.")
        self.toggle_action.toggled.connect(lambda on: self.hotkey_mode_changed.emit("toggle" if on else "hold"))
        hotkey_menu.addAction(self.toggle_action)

        self.recent_menu = self.menu.addMenu("Recent")
        self.recent_menu.setToolTipsVisible(True)
        self.set_recent([])

        self.menu.addSeparator()
        self.menu.addAction("Settings…", lambda: self.settings_requested.emit("asr"))

        llm_menu = self.menu.addMenu("LLM Refinement")
        self.llm_action = QAction("Enable", llm_menu, checkable=True)
        self.llm_action.setChecked(settings.llm_enabled)
        self.llm_action.toggled.connect(self.llm_toggled.emit)
        llm_menu.addAction(self.llm_action)
        llm_menu.addAction("Settings…", lambda: self.settings_requested.emit("llm"))

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
        self.set_hotkey(settings.hotkey, settings.hotkey_mode)  # also sets the tooltip

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

    def set_hotkey(self, hotkey_id: str, mode: str) -> None:
        act = self._hotkey_actions.get(hotkey_id)
        if act is not None:
            act.setChecked(True)
        self.toggle_action.blockSignals(True)
        self.toggle_action.setChecked(mode == "toggle")
        self.toggle_action.blockSignals(False)
        key = get_hotkey(hotkey_id).short_name
        self._hint = f"Tap {key} to start and stop dictation" if mode == "toggle" else f"Hold {key} to dictate"
        self._update_tooltip()

    def set_recent(self, texts: list[str], visible: bool = True) -> None:
        """Newest first. Clicking an entry copies it to the clipboard."""
        m = self.recent_menu
        m.menuAction().setVisible(visible)
        m.clear()
        if not texts:
            m.addAction("(empty)").setEnabled(False)
            return
        for text in texts:
            one_line = " ".join(text.split())
            label = one_line if len(one_line) <= RECENT_LABEL_CHARS else one_line[: RECENT_LABEL_CHARS - 1] + "…"
            act = m.addAction(label.replace("&", "&&"))
            act.setToolTip(f"Copy to clipboard:\n{text[:500]}")
            act.triggered.connect(lambda _checked=False, t=text: self.recent_selected.emit(t))
        m.addSeparator()
        m.addAction("Clear", self.recent_cleared.emit)

    def set_llm_checked(self, checked: bool) -> None:
        self.llm_action.blockSignals(True)
        self.llm_action.setChecked(checked)
        self.llm_action.blockSignals(False)

    def set_autostart_checked(self, checked: bool) -> None:
        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(checked)
        self.autostart_action.blockSignals(False)

    def _update_tooltip(self) -> None:
        self.icon.setToolTip(f"QwenType {__version__} — ASR: {self._status}\n{self._hint}")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.menu_opened.emit()  # left click: refresh the status in the tooltip
