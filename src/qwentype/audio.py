"""Microphone capture (sounddevice / WASAPI) producing 16 kHz s16le mono chunks.

The stream is opened only while recording. Opening/closing runs on a single
worker thread so the UI never blocks on the audio driver and start/stop calls
are always processed in order.
"""

from __future__ import annotations

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

TARGET_RATE = 16000
CHUNK_BYTES = 3200  # 100 ms of 16 kHz s16le mono
BLOCK_SECONDS = 0.02  # callback granularity (drives the waveform)


class Resampler:
    """Streaming resampler: windowed-sinc low-pass (when downsampling) followed
    by linear interpolation. Keeps state between blocks, numpy only."""

    def __init__(self, src_rate: float, dst_rate: float = TARGET_RATE, taps: int = 63) -> None:
        self.ratio = float(src_rate) / float(dst_rate)  # input samples per output sample
        self._fir: np.ndarray | None = None
        if src_rate > dst_rate:
            fc = 0.5 * dst_rate / src_rate * 0.9  # cutoff in cycles/sample, a bit below Nyquist
            n = np.arange(taps) - (taps - 1) / 2
            h = 2 * fc * np.sinc(2 * fc * n) * np.hamming(taps)
            self._fir = (h / h.sum()).astype(np.float32)
            self._hist = np.zeros(taps - 1, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        if self._fir is not None:
            xx = np.concatenate((self._hist, x))
            x = np.convolve(xx, self._fir, mode="valid").astype(np.float32)
            self._hist = xx[-(len(self._fir) - 1):]
        buf = np.concatenate((self._buf, x)) if self._buf.size else x
        n_out = int(math.ceil((buf.size - 1 - self._pos) / self.ratio)) if buf.size - 1 > self._pos else 0
        if n_out <= 0:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)
        idx = self._pos + self.ratio * np.arange(n_out)
        i0 = np.floor(idx).astype(np.int64)
        frac = (idx - i0).astype(np.float32)
        out = buf[i0] * (1.0 - frac) + buf[i0 + 1] * frac
        new_pos = self._pos + self.ratio * n_out
        drop = int(math.floor(new_pos))
        self._buf = buf[drop:]
        self._pos = new_pos - drop
        return out


def rms_level(mono: np.ndarray) -> float:
    """Map block RMS to 0..1 on a dB scale (-55 dBFS -> 0, -10 dBFS -> 1)."""
    if mono.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
    db = 20.0 * math.log10(rms + 1e-9)
    return min(1.0, max(0.0, (db + 55.0) / 45.0))


def to_pcm16(mono: np.ndarray) -> bytes:
    return (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class AudioCapture(QObject):
    """Captures the default input device. `level` is emitted from the audio
    thread (queued to the UI); chunks go to the `on_chunk` callback."""

    level = Signal(float)
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="QwenType-Audio")
        self._lock = threading.Lock()
        self._stream = None
        self._on_chunk: Callable[[bytes], None] | None = None
        self._resampler: Resampler | None = None
        self._channels = 1
        self._pending = bytearray()

    # -- public API (any thread) ---------------------------------------------

    def start(self, on_chunk: Callable[[bytes], None]) -> None:
        self._executor.submit(self._open, on_chunk)

    def stop(self, flush: bool = True, then: Callable[[], None] | None = None) -> None:
        """Close the stream; with flush=True the last partial chunk is delivered
        first. `then` runs on the audio worker afterwards (keeps ordering)."""
        self._executor.submit(self._close, flush, then)

    def shutdown(self) -> None:
        self.stop(flush=False)
        self._executor.shutdown(wait=True, cancel_futures=False)

    # -- worker thread --------------------------------------------------------

    def _open(self, on_chunk: Callable[[bytes], None]) -> None:
        self._close(False, None)
        try:
            import sounddevice as sd
        except OSError as e:
            log.error("sounddevice unavailable: %s", e)
            self.error.emit("Microphone unavailable")
            return
        with self._lock:
            self._on_chunk = on_chunk
            self._pending = bytearray()
        try:
            stream, rate, channels = self._open_stream(sd)
        except Exception as e:
            log.error("Could not open microphone: %s", e)
            with self._lock:
                self._on_chunk = None
            self.error.emit("Cannot open microphone")
            return
        with self._lock:
            self._resampler = None if int(rate) == TARGET_RATE else Resampler(rate)
            self._channels = channels
            self._stream = stream
        try:
            stream.start()
        except Exception as e:
            log.error("Could not start microphone: %s", e)
            self._close(False, None)
            self.error.emit("Cannot start microphone")
            return
        log.info("Microphone open: %s Hz, %s ch%s", rate, channels, " (resampling)" if self._resampler else "")

    def _open_stream(self, sd):
        """Try WASAPI at 16 kHz with auto_convert, then the device's native rate."""
        candidates: list[tuple[int | None, object | None]] = []
        try:
            for api in sd.query_hostapis():
                if "WASAPI" in api["name"] and api["default_input_device"] >= 0:
                    extra = None
                    if hasattr(sd, "WasapiSettings"):
                        try:
                            extra = sd.WasapiSettings(auto_convert=True)
                        except TypeError:
                            extra = None
                    candidates.append((api["default_input_device"], extra))
                    break
        except Exception as e:
            log.debug("Host API query failed: %s", e)
        candidates.append((None, None))  # PortAudio default device

        last_error: Exception | None = None
        for device, extra in candidates:
            info = sd.query_devices(device, "input")
            max_ch = max(1, int(info.get("max_input_channels", 1)))
            default_rate = int(info.get("default_samplerate") or 48000)
            attempts = [(TARGET_RATE, 1, extra)]
            if extra is not None:
                attempts.append((TARGET_RATE, 1, None))
            for ch in dict.fromkeys((1, min(max_ch, 2))):
                attempts.append((default_rate, ch, None))
            for rate, ch, ex in attempts:
                try:
                    stream = sd.InputStream(
                        device=device,
                        samplerate=rate,
                        channels=ch,
                        dtype="float32",
                        blocksize=int(rate * BLOCK_SECONDS),
                        latency="low",
                        extra_settings=ex,
                        callback=self._callback,
                    )
                    return stream, rate, ch
                except Exception as e:
                    last_error = e
                    log.debug("Open failed (device=%s rate=%s ch=%s auto_convert=%s): %s",
                              device, rate, ch, ex is not None, e)
        raise last_error or RuntimeError("no input device")

    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        if status:
            log.debug("Audio status: %s", status)
        mono = indata[:, 0] if self._channels == 1 else indata.mean(axis=1)
        self.level.emit(rms_level(mono))
        with self._lock:
            if self._on_chunk is None:
                return
            if self._resampler is not None:
                mono = self._resampler.process(mono)
            self._pending += to_pcm16(mono)
            while len(self._pending) >= CHUNK_BYTES:
                chunk = bytes(self._pending[:CHUNK_BYTES])
                del self._pending[:CHUNK_BYTES]
                self._on_chunk(chunk)

    def _close(self, flush: bool, then: Callable[[], None] | None) -> None:
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()  # waits for the callback to finish
                stream.close()
            except Exception as e:
                log.debug("Closing stream: %s", e)
        with self._lock:
            if flush and self._on_chunk is not None and self._pending:
                self._on_chunk(bytes(self._pending))
            self._stream = None
            self._on_chunk = None
            self._pending = bytearray()
            self._resampler = None
        if stream is not None:
            self.level.emit(0.0)
        if then is not None:
            try:
                then()
            except Exception:
                log.exception("Audio stop callback failed")
