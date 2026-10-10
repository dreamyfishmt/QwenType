"""Render docs/capsule.gif: the real CapsuleWindow over a mock editor, frame by frame.

Run from the repository root (Pillow is in the dev dependency group):

    uv run python scripts/make_capsule_gif.py

Qt runs offscreen, so this works on any OS. The capsule's appear/exit and width
animations are replayed with the same durations and easing curves as capsule.py,
but driven by the frame clock instead of real time, so the output is deterministic.
"""

from __future__ import annotations

import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEasingCurve, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from qwentype import capsule as cap

FPS = 20
W, H = 760, 380
OUT = Path(__file__).resolve().parent.parent / "docs" / "capsule.gif"
FONTS = ["Segoe UI", "Inter", "Microsoft YaHei UI", "WenQuanYi Zen Hei", "Noto Sans CJK SC", "sans-serif"]

EDITOR = QRectF(40, 36, 530, 200)


@dataclass
class Utterance:
    start: float  # key down (s)
    end: float  # key up (s)
    partials: list[tuple[float, str]]  # (seconds after key down, text)
    final: str


UTTERANCES = [
    Utterance(
        0.7,
        3.6,
        [
            (0.8, "Hello"),
            (1.3, "Hello, this is QwenType,"),
            (2.0, "Hello, this is QwenType, voice typing"),
            (2.6, "Hello, this is QwenType, voice typing for Windows"),
        ],
        "Hello, this is QwenType, voice typing for Windows.",
    ),
    Utterance(
        5.2,
        7.9,
        [(0.8, "我用"), (1.4, "我用 Python 写了"), (2.1, "我用 Python 写了一个 JSON 解析器")],
        "我用 Python 写了一个 JSON 解析器。",
    ),
]
DURATION = 10.4
FINAL_DELAY = 0.2  # final result after the key is released (GPU server)
TYPE_SECONDS = 0.35  # injection is near-instant; slowed slightly so it reads as typing


def ease(kind: QEasingCurve.Type, t: float) -> float:
    return QEasingCurve(kind).valueForProgress(max(0.0, min(1.0, t)))


class Demo:
    def __init__(self) -> None:
        self.capsule = cap.CapsuleWindow(blur=False)
        font = QFont()
        font.setFamilies(FONTS)
        font.setPixelSize(16)
        self.capsule.setFont(font)
        self.capsule._fm = QFontMetricsF(font)
        self.capsule._center = QPoint(2000, 2000)  # keep the (hidden) geometry positive
        self.ui_font = QFont()
        self.ui_font.setFamilies(FONTS)
        self.rng = random.Random(7)
        self.label_from = self.label_to = float(cap.LABEL_MIN)
        self.label_t0 = 0.0
        self.text = ""

    # -- capsule state at time t ---------------------------------------------------

    def _label_target(self, text: str) -> float:
        adv = math.ceil(self.capsule._fm.horizontalAdvance(text)) + 2
        return float(max(cap.LABEL_MIN, min(cap.LABEL_MAX, adv)))

    def _set_label(self, t: float, text: str, color: QColor) -> None:
        c = self.capsule
        if text != self.text:
            target = self._label_target(text)
            if abs(target - self.label_to) > 0.5:
                self.label_from, self.label_to, self.label_t0 = c._label_w, target, t
            self.text = text
        c._text, c._color = text, color
        c._label_w = self.label_from + (self.label_to - self.label_from) * ease(
            QEasingCurve.Type.OutCubic, (t - self.label_t0) / 0.25
        )

    def capsule_frame(self, t: float) -> tuple[QImage | None, float]:
        """The capsule image and its opacity at time t (None when hidden)."""
        for u in UTTERANCES:
            shown = u.start + 0.15  # SHOW_DELAY_MS
            final_at = u.end + FINAL_DELAY
            if not (shown <= t < final_at + 0.22):
                continue
            c = self.capsule
            if t < final_at:
                text, color = "Listening…", cap.COLOR_PLACEHOLDER
                for at, partial in u.partials:
                    if t >= u.start + at:
                        text, color = partial, cap.COLOR_TEXT
                if t < shown + 1 / FPS:  # first frame: no width animation
                    self.text = text
                    self.label_from = self.label_to = c._label_w = self._label_target(text)
                self._set_label(t, text, color)
                a = t - shown
                c._scale = 0.6 + 0.4 * ease(QEasingCurve.Type.OutBack, a / 0.35)
                opacity = min(1.0, a / 0.2)
            else:  # exit animation, then the text is typed
                a = t - final_at
                c._scale = 1.0 - 0.15 * ease(QEasingCurve.Type.InCubic, a / 0.22)
                opacity = 1.0 - a / 0.22
            speaking = u.start + 0.4 < t < u.end - 0.2
            level = (0.25 + 0.55 * abs(math.sin(t * 7.3)) * self.rng.uniform(0.6, 1.0)) if speaking else 0.02
            c.wave.set_level(level if t < u.end else 0.0)
            for _ in range(3):  # 3 x 16 ms waveform ticks per 50 ms frame
                c.wave._tick()
            c._apply_geometry()
            img = QImage(c.size(), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            c.render(img, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
            return img, max(0.0, opacity)
        return None, 0.0

    # -- scene ---------------------------------------------------------------------------

    def typed_text(self, t: float) -> list[str]:
        lines: list[str] = []
        for u in UTTERANCES:
            at = u.end + FINAL_DELAY + 0.05
            if t >= at:
                n = len(u.final) if t >= at + TYPE_SECONDS else int(len(u.final) * (t - at) / TYPE_SECONDS)
                lines.append(u.final[:n])
        return lines

    def key_down(self, t: float) -> bool:
        return any(u.start <= t < u.end for u in UTTERANCES)

    def draw(self, t: float) -> QImage:
        img = QImage(W, H, QImage.Format.Format_RGB32)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        bg = QLinearGradient(0, 0, W, H)
        bg.setColorAt(0, QColor(30, 58, 110))
        bg.setColorAt(1, QColor(88, 52, 130))
        p.fillRect(0, 0, W, H, bg)

        # Editor window
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRoundedRect(EDITOR.translated(0, 4), 10, 10)
        p.setBrush(QColor(250, 250, 252))
        p.drawRoundedRect(EDITOR, 10, 10)
        p.setBrush(QColor(236, 238, 243))
        p.drawRoundedRect(QRectF(EDITOR.x(), EDITOR.y(), EDITOR.width(), 34), 10, 10)
        p.drawRect(QRectF(EDITOR.x(), EDITOR.y() + 20, EDITOR.width(), 14))
        for i, color in enumerate((QColor(255, 95, 87), QColor(254, 188, 46), QColor(40, 200, 64))):
            p.setBrush(color)
            p.drawEllipse(QRectF(EDITOR.x() + 16 + i * 20, EDITOR.y() + 11, 12, 12))
        title = QFont(self.ui_font)
        title.setPixelSize(13)
        p.setFont(title)
        p.setPen(QColor(90, 95, 105))
        p.drawText(QRectF(EDITOR.x(), EDITOR.y(), EDITOR.width(), 34), Qt.AlignmentFlag.AlignCenter, "Notes")

        body = QFont(self.ui_font)
        body.setPixelSize(17)
        p.setFont(body)
        fm = QFontMetricsF(body)
        lines = self.typed_text(t) or [""]
        if t > UTTERANCES[0].end + 1.0 and len(lines) == 1:
            lines.append("")  # Enter pressed between the two utterances
        x0, y0, lh = EDITOR.x() + 24, EDITOR.y() + 58, 30
        p.setPen(QColor(30, 32, 38))
        for i, line in enumerate(lines):
            p.drawText(QRectF(x0, y0 + i * lh - 20, EDITOR.width() - 48, lh), Qt.AlignmentFlag.AlignLeft, line)
        if int(t * 2) % 2 == 0 or self.key_down(t):  # blinking caret
            cx = x0 + fm.horizontalAdvance(lines[-1]) + 1
            cy = y0 + (len(lines) - 1) * lh - 18
            p.setPen(QPen(QColor(40, 90, 220), 2))
            p.drawLine(QRectF(cx, cy, 0, 22).topLeft(), QRectF(cx, cy, 0, 22).bottomLeft())

        # Keycap
        down = self.key_down(t)
        key = QRectF(EDITOR.right() + 34, EDITOR.y() + 60, 116, 46).translated(0, 3 if down else 0)
        if not down:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRoundedRect(key.translated(0, 4), 9, 9)
        p.setBrush(QColor(255, 120, 130) if down else QColor(245, 246, 250))
        p.setPen(QPen(QColor(255, 255, 255, 120), 1))
        p.drawRoundedRect(key, 9, 9)
        kf = QFont(self.ui_font)
        kf.setPixelSize(15)
        kf.setBold(True)
        p.setFont(kf)
        p.setPen(QColor(255, 255, 255) if down else QColor(40, 44, 52))
        p.drawText(key, Qt.AlignmentFlag.AlignCenter, "Right Ctrl")
        hint = QFont(self.ui_font)
        hint.setPixelSize(12)
        p.setFont(hint)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(
            QRectF(key.x() - 20, key.bottom() + 8, key.width() + 40, 18),
            Qt.AlignmentFlag.AlignCenter,
            "hold to talk" if not down else "recording…",
        )

        # Capsule, bottom center like on screen
        img_c, opacity = self.capsule_frame(t)
        if img_c is not None and opacity > 0:
            p.setOpacity(opacity)
            cx, cy = W // 2, H - cap.BOTTOM_MARGIN - cap.HEIGHT // 2
            p.drawImage(QPoint(cx - img_c.width() // 2, cy - img_c.height() // 2), img_c)
            p.setOpacity(1.0)
        p.end()
        return img


def to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombuffer(
        "RGB", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGB", img.bytesPerLine(), 1
    )


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    demo = Demo()
    frames = [to_pil(demo.draw(i / FPS)) for i in range(int(DURATION * FPS))]
    # One shared palette (taken from a frame with the capsule and both lines) keeps colors stable.
    ref = frames[int((UTTERANCES[1].end - 0.3) * FPS)]
    palette = ref.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    quantized[0].save(
        OUT, save_all=True, append_images=quantized[1:], duration=1000 // FPS, loop=0, optimize=True, disposal=1
    )
    print(f"Wrote {OUT} ({len(frames)} frames, {OUT.stat().st_size / 1024:.0f} KiB)")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
