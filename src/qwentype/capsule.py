"""Frameless capsule overlay shown while recording.

Never takes focus (WindowDoesNotAcceptFocus, WA_ShowWithoutActivating,
WS_EX_NOACTIVATE) and never shows up in Alt+Tab (Qt.Tool, WS_EX_TOOLWINDOW).
Qt cannot scale a top-level window, so the "scale" animations resize the
window around a fixed center and paint the contents scaled.
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPen,
    QScreen,
)
from PySide6.QtWidgets import QWidget

from . import win32

HEIGHT = 56
RADIUS = 28
PAD_LEFT = 14
WAVE_W, WAVE_H = 44, 32
GAP = 12
PAD_RIGHT = 24
LABEL_MIN, LABEL_MAX = 160, 560
BOTTOM_MARGIN = 28

COLOR_TEXT = QColor(245, 246, 250)
COLOR_PLACEHOLDER = QColor(245, 246, 250, 150)
COLOR_STATUS = QColor(170, 205, 255)
COLOR_ERROR = QColor(255, 138, 128)


class WaveformWidget(QWidget):
    """Five bars driven by real microphone RMS levels (no fake animation)."""

    WEIGHTS = (0.5, 0.8, 1.0, 0.75, 0.55)
    ATTACK, RELEASE, JITTER = 0.40, 0.15, 0.04
    BAR_W, BAR_GAP, BAR_MIN = 4.0, 5.0, 4.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._target = 0.0
        self._env = 0.0
        self._heights = [self.BAR_MIN] * len(self.WEIGHTS)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)  # ~60 fps
        self._timer.timeout.connect(self._tick)

    def set_level(self, level: float) -> None:
        self._target = max(0.0, min(1.0, level))

    def start(self) -> None:
        self._target = self._env = 0.0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        k = self.ATTACK if self._target > self._env else self.RELEASE
        self._env += (self._target - self._env) * k
        span = WAVE_H - self.BAR_MIN
        self._heights = [
            self.BAR_MIN + span * max(0.0, min(1.0, self._env * w * (1.0 + random.uniform(-self.JITTER, self.JITTER))))
            for w in self.WEIGHTS
        ]
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width() / WAVE_W if WAVE_W else 1.0
        p.scale(s, s)
        total = len(self.WEIGHTS) * self.BAR_W + (len(self.WEIGHTS) - 1) * self.BAR_GAP
        x = (WAVE_W - total) / 2
        grad = QLinearGradient(0, 0, WAVE_W, 0)
        grad.setColorAt(0.0, QColor(125, 211, 252))
        grad.setColorAt(1.0, QColor(196, 160, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        for h in self._heights:
            p.drawRoundedRect(QRectF(x, (WAVE_H - h) / 2, self.BAR_W, h), self.BAR_W / 2, self.BAR_W / 2)
            x += self.BAR_W + self.BAR_GAP


def screen_for_foreground() -> QScreen:
    """The screen containing the focused window (falls back to the cursor's)."""
    center = win32.foreground_window_center()
    if center is not None:
        pt = QPoint(*center)
        for screen in QGuiApplication.screens():
            g = screen.geometry()
            dpr = screen.devicePixelRatio()
            # Qt keeps each screen's origin in native pixels; only its size is scaled.
            native = QRect(g.topLeft(), QSize(round(g.width() * dpr), round(g.height() * dpr)))
            if native.contains(pt):
                return screen
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


class CapsuleWindow(QWidget):
    hidden = Signal()

    def __init__(self, blur: bool = True) -> None:
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

        self._want_blur = blur
        self._blur_active = False
        self._styled = False
        self._scale = 1.0
        self._label_w = float(LABEL_MIN)
        self._center = QPoint(0, 0)
        self._text = ""
        self._color = COLOR_PLACEHOLDER
        self._closing = False

        font = QFont()
        font.setFamilies(
            ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Yu Gothic UI", "Malgun Gothic", "sans-serif"]
        )
        font.setPixelSize(16)
        self.setFont(font)
        self._fm = QFontMetricsF(font)

        self.wave = WaveformWidget(self)

        self._appear = QParallelAnimationGroup(self)
        self._appear_scale = QPropertyAnimation(self, b"animScale", self)
        self._appear_scale.setDuration(350)
        self._appear_scale.setEasingCurve(QEasingCurve.Type.OutBack)
        self._appear_opacity = QPropertyAnimation(self, b"windowOpacity", self)
        self._appear_opacity.setDuration(200)
        self._appear.addAnimation(self._appear_scale)
        self._appear.addAnimation(self._appear_opacity)

        self._exit = QParallelAnimationGroup(self)
        self._exit_scale = QPropertyAnimation(self, b"animScale", self)
        self._exit_scale.setDuration(220)
        self._exit_scale.setEasingCurve(QEasingCurve.Type.InCubic)
        self._exit_opacity = QPropertyAnimation(self, b"windowOpacity", self)
        self._exit_opacity.setDuration(220)
        self._exit.addAnimation(self._exit_scale)
        self._exit.addAnimation(self._exit_opacity)
        self._exit.finished.connect(self._on_exit_finished)

        self._width_anim = QPropertyAnimation(self, b"labelWidth", self)
        self._width_anim.setDuration(250)
        self._width_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.timeout.connect(self.dismiss)
        self._restore = QTimer(self)
        self._restore.setSingleShot(True)
        self._restore.timeout.connect(self._restore_text)
        self._saved: tuple[str, QColor] | None = None

    # -- animated properties ---------------------------------------------------

    def _get_scale(self) -> float:
        return self._scale

    def _set_scale(self, value: float) -> None:
        self._scale = max(0.05, float(value))
        self._apply_geometry()

    animScale = Property(float, _get_scale, _set_scale)

    def _get_label_w(self) -> float:
        return self._label_w

    def _set_label_w(self, value: float) -> None:
        self._label_w = float(value)
        self._apply_geometry()

    labelWidth = Property(float, _get_label_w, _set_label_w)

    # -- public API --------------------------------------------------------------

    def show_listening(self) -> None:
        self._auto_hide.stop()
        self._set_text("Listening…", COLOR_PLACEHOLDER, animate=False)
        self.wave.start()
        self._appear_now()

    def set_text(self, text: str) -> None:
        if self._restore.isActive():  # a notice is showing: update what comes back after it
            self._saved = (text, COLOR_TEXT)
            return
        self._set_text(text, COLOR_TEXT)

    def show_status(self, text: str) -> None:
        self._restore.stop()
        self._set_text(text, COLOR_STATUS)
        self.wave.set_level(0.0)
        if not self.isVisible() or self._closing:
            self._appear_now()

    def show_notice(self, text: str, ms: int = 1500) -> None:
        if self._saved is None:
            self._saved = (self._text, self._color)
        self._set_text(text, COLOR_STATUS)
        self._restore.start(ms)

    def show_error(self, text: str, ms: int = 2000) -> None:
        self._restore.stop()
        self._saved = None
        self.wave.set_level(0.0)
        self._set_text(text, COLOR_ERROR)
        if not self.isVisible() or self._closing:
            self._appear_now()
        self._auto_hide.start(ms)

    def set_level(self, level: float) -> None:
        self.wave.set_level(level)

    def dismiss(self) -> None:
        self._auto_hide.stop()
        self._restore.stop()
        self._saved = None
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._appear.stop()
        self._exit_scale.setStartValue(self._scale)
        self._exit_scale.setEndValue(0.85)
        self._exit_opacity.setStartValue(self.windowOpacity())
        self._exit_opacity.setEndValue(0.0)
        self._exit.start()

    def hide_now(self) -> None:
        self._appear.stop()
        self._exit.stop()
        self._auto_hide.stop()
        self._restore.stop()
        self._saved = None
        self._on_exit_finished()

    # -- internals -------------------------------------------------------------------

    def _appear_now(self) -> None:
        self._exit.stop()
        was_closing = self._closing
        self._closing = False
        if not self.isVisible():
            self._place()
            self._scale = 0.6
            self.setWindowOpacity(0.0)
            self._apply_geometry()
            self.show()
        elif not was_closing:
            return
        self._appear_scale.setStartValue(self._scale)
        self._appear_scale.setEndValue(1.0)
        self._appear_opacity.setStartValue(self.windowOpacity())
        self._appear_opacity.setEndValue(1.0)
        self._appear.start()

    def _on_exit_finished(self) -> None:
        self._closing = False
        self.wave.stop()
        self.hide()
        self.hidden.emit()

    def _restore_text(self) -> None:
        if self._saved is not None:
            text, color = self._saved
            self._saved = None
            self._set_text(text, color)

    def _place(self) -> None:
        avail = screen_for_foreground().availableGeometry()
        self._center = QPoint(avail.center().x(), avail.bottom() - BOTTOM_MARGIN - HEIGHT // 2)

    def _set_text(self, text: str, color: QColor, animate: bool = True) -> None:
        self._text = text
        self._color = color
        target = max(LABEL_MIN, min(LABEL_MAX, math.ceil(self._fm.horizontalAdvance(text)) + 2))
        if not animate or not self.isVisible():
            self._width_anim.stop()
            self._label_w = float(target)
            self._apply_geometry()
            return self.update()
        running = self._width_anim.state() == QPropertyAnimation.State.Running
        goal = float(self._width_anim.endValue()) if running else self._label_w
        if abs(target - goal) > 0.5:
            self._width_anim.stop()
            self._width_anim.setStartValue(self._label_w)
            self._width_anim.setEndValue(float(target))
            self._width_anim.start()
        self.update()

    def _base_width(self) -> float:
        return PAD_LEFT + WAVE_W + GAP + self._label_w + PAD_RIGHT

    def _apply_geometry(self) -> None:
        s = self._scale
        w = max(2, math.ceil(self._base_width() * s))
        h = max(2, math.ceil(HEIGHT * s))
        self.setGeometry(self._center.x() - w // 2, self._center.y() - h // 2, w, h)
        self.wave.setGeometry(
            round(PAD_LEFT * s), round((HEIGHT - WAVE_H) / 2 * s), max(1, round(WAVE_W * s)), max(1, round(WAVE_H * s))
        )
        if self._blur_active:
            dpr = self.devicePixelRatioF()
            win32.set_round_region(int(self.winId()), round(w * dpr), round(h * dpr), round(RADIUS * s * dpr))
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._styled:
            self._styled = True
            hwnd = int(self.winId())
            win32.make_overlay_window(hwnd)
            if self._want_blur:
                self._blur_active = win32.enable_blur_behind(hwnd)
                self._apply_geometry()
        else:
            win32.make_overlay_window(int(self.winId()))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.scale(self._scale, self._scale)
        rect = QRectF(0.5, 0.5, self._base_width() - 1.0, HEIGHT - 1.0)
        # With blur-behind the backdrop does the work; otherwise a dark translucent pill.
        p.setBrush(QColor(22, 24, 30, 120 if self._blur_active else 218))
        p.setPen(QPen(QColor(255, 255, 255, 38), 1.0))
        p.drawRoundedRect(rect, RADIUS - 0.5, RADIUS - 0.5)

        x = PAD_LEFT + WAVE_W + GAP
        text = self._fm.elidedText(self._text, Qt.TextElideMode.ElideLeft, self._label_w)
        p.setPen(self._color)
        p.setFont(self.font())
        p.drawText(
            QRectF(x, 0, self._label_w + 2, HEIGHT),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )
