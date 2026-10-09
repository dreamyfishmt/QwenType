"""Abstract ASR client interface, so other server protocols can be added later.

A backend creates one session per utterance. Sessions report back through the
Qt signals on `session.events`, which are safe to emit from any thread (they
are delivered to the UI thread as queued signals).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtCore import QObject, Signal


class AsrEvents(QObject):
    ready = Signal()  # server accepted the stream (pre-roll is being flushed)
    partial = Signal(str)  # full transcript so far (replace, don't append)
    final = Signal(str, str)  # final text, language reported by the server
    error = Signal(str)  # short, user-facing message
    audio_limit = Signal(float)  # server stopped accepting audio after this many seconds


class AsrSession(ABC):
    """One utterance. All methods are non-blocking and callable from any thread."""

    def __init__(self) -> None:
        self.events = AsrEvents()

    @abstractmethod
    def start(self) -> None:
        """Connect and start the stream. Audio sent before the server is ready is buffered."""

    @abstractmethod
    def send_audio(self, pcm: bytes) -> None:
        """Queue 16 kHz s16le mono PCM."""

    @abstractmethod
    def stop(self) -> None:
        """End of speech: flush queued audio and request the final result."""

    @abstractmethod
    def cancel(self) -> None:
        """Abort without a result. No further signals are emitted."""


class AsrBackend(ABC):
    @abstractmethod
    def create_session(self, language: str) -> AsrSession:
        """language: code such as "zh-CN", or "" for auto-detect."""

    @abstractmethod
    async def status(self) -> str:
        """Short server status, e.g. "ready", "loading_models", "offline"."""
