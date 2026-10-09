"""Client for the Qwen3-ASR server's `WS /transcribe-streaming` endpoint
(https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm).

One WebSocket connection per utterance:
  connect <url>?language=<code>  ->  {"type":"ready"}
  {"type":"start","format":"pcm_s16le","sample_rate_hz":16000}, binary PCM frames ...
  <- {"type":"partial","text":...}  (full transcript so far)
  {"type":"stop"}  ->  {"type":"final","text":...,"language":...}, close 1000
"""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus, InvalidURI

from ..aio import AsyncRunner
from ..settings import Settings
from .base import AsrBackend, AsrSession

log = logging.getLogger(__name__)

_STOP = object()


def build_ws_url(ws_url: str, language: str) -> str:
    """Append the language hint as a query parameter (omitted for auto-detect)."""
    url = ws_url.strip()
    if not language:
        return url
    base, hash_sign, fragment = url.partition("#")
    sep = "&" if "?" in base else "?"
    if base.endswith(("?", "&")):
        sep = ""
    return f"{base}{sep}{urlencode({'language': language})}{hash_sign}{fragment}"


class ServerError(Exception):
    """A failure with a short, user-facing message."""


def describe_close(code: int | None, reason: str, server_message: str | None) -> str:
    if server_message:
        return server_message
    if code == 1011:
        return "ASR server not ready" if "not ready" in (reason or "").lower() else "ASR server internal error"
    if code == 1003:
        return "Unsupported audio format or language"
    if code == 1002:
        return "ASR protocol error"
    if code == 1000:
        return "ASR server closed the connection"
    return "Connection to ASR server lost"


async def fetch_status(http_base: str, timeout: float = 3.0) -> str:
    """GET <http-base>/ready: 200 -> "ready", 503 -> {"status": ...}, refused -> "offline"."""
    try:
        # trust_env=False: the server is local, never route it through a proxy.
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            r = await client.get(http_base.rstrip("/") + "/ready")
    except httpx.TimeoutException:
        return "timeout"
    except (httpx.HTTPError, OSError, ValueError):
        return "offline"
    if r.status_code == 200:
        return "ready"
    try:
        status = r.json().get("status")
    except Exception:
        status = None
    if isinstance(status, str) and status:
        return status
    return "loading" if r.status_code == 503 else f"HTTP {r.status_code}"


class Qwen3StreamingSession(AsrSession):
    def __init__(self, runner: AsyncRunner, ws_url: str, language: str,
                 ready_timeout: float, final_timeout: float) -> None:
        super().__init__()
        self._runner = runner
        self.url = build_ws_url(ws_url, language)
        self._ready_timeout = ready_timeout
        self._final_timeout = final_timeout
        self._queue: asyncio.Queue = asyncio.Queue()
        self._future = None
        self._cancelled = False
        self._finished = False
        self._stop_sent = False
        self._last_partial = ""
        self._language = ""
        self._server_error: str | None = None

    # -- public API (any thread) ---------------------------------------------

    def start(self) -> None:
        self._future = self._runner.submit(self._run())

    def send_audio(self, pcm: bytes) -> None:
        if not self._cancelled and not self._finished:
            self._runner.call_soon(self._queue.put_nowait, pcm)

    def stop(self) -> None:
        if not self._cancelled:
            self._runner.call_soon(self._queue.put_nowait, _STOP)

    def cancel(self) -> None:
        self._cancelled = True
        if self._future is not None:
            self._future.cancel()  # closes the socket without sending "stop"

    # -- signal helpers (loop thread) -----------------------------------------

    def _emit_error(self, message: str) -> None:
        if not self._cancelled and not self._finished:
            self._finished = True
            log.warning("ASR error: %s", message)
            self.events.error.emit(message)

    def _emit_final(self, text: str, language: str) -> None:
        if not self._cancelled and not self._finished:
            self._finished = True
            self.events.final.emit(text, language)

    # -- protocol (loop thread) -----------------------------------------------

    async def _run(self) -> None:
        if self._cancelled:
            return
        ws: ClientConnection | None = None
        try:
            try:
                async with asyncio.timeout(self._ready_timeout):
                    ws = await connect(
                        self.url,
                        proxy=None,
                        open_timeout=None,  # covered by the ready timeout
                        close_timeout=2,
                        compression=None,
                        max_size=2**22,
                    )
                    await self._wait_ready(ws)
            except TimeoutError:
                self._emit_error("ASR server not ready")
                return
            except InvalidURI:
                self._emit_error("Invalid ASR server URL")
                return
            except InvalidStatus as e:
                self._emit_error(f"ASR server rejected connection (HTTP {e.response.status_code})")
                return
            except (OSError, InvalidHandshake) as e:
                log.info("Connect failed: %r", e)
                self._emit_error("ASR server offline")
                return
            except ServerError as e:
                self._emit_error(str(e))
                return
            if not self._cancelled:
                self.events.ready.emit()
            await self._stream(ws)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("ASR session failed")
            self._emit_error(f"ASR error: {type(e).__name__}")
        finally:
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _wait_ready(self, ws: ClientConnection) -> None:
        # While models load, the server accepts the connection but sends nothing.
        while True:
            try:
                raw = await ws.recv()
            except ConnectionClosed as e:
                raise ServerError(self._describe(e)) from None
            msg = self._parse(raw)
            if msg is None:
                continue
            if msg.get("type") == "ready":
                return
            if msg.get("type") == "error":
                self._server_error = str(msg.get("message") or "ASR server error")

    async def _stream(self, ws: ClientConnection) -> None:
        await ws.send(json.dumps({"type": "start", "format": "pcm_s16le", "sample_rate_hz": 16000}))
        send_task = asyncio.create_task(self._sender(ws))
        recv_task = asyncio.create_task(self._receiver(ws))
        try:
            await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            if not recv_task.done():
                # "stop" has been sent (or sending failed): wait for the final result.
                try:
                    await asyncio.wait_for(asyncio.shield(recv_task), self._final_timeout)
                except TimeoutError:
                    log.info("No final result within %.1f s", self._final_timeout)
                    self._fallback_or_error("No result from ASR server")
                    return
                except (ServerError, ConnectionClosed):
                    pass  # handled below via recv_task.exception()
            try:
                text, language = recv_task.result()
            except ServerError as e:
                if self._stop_sent:
                    self._fallback_or_error(str(e))
                else:
                    self._emit_error(str(e))
                return
            self._emit_final(text, language)
        finally:
            for t in (send_task, recv_task):
                t.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)

    def _fallback_or_error(self, message: str) -> None:
        if self._last_partial:
            log.info("Falling back to the last partial result")
            self._emit_final(self._last_partial, self._language)
        else:
            self._emit_error(message)

    async def _sender(self, ws: ClientConnection) -> None:
        # Pre-roll audio queued before "ready" goes out first, in order.
        while True:
            item = await self._queue.get()
            if item is _STOP:
                await ws.send(json.dumps({"type": "stop"}))
                self._stop_sent = True
                return
            await ws.send(item)

    async def _receiver(self, ws: ClientConnection) -> tuple[str, str]:
        while True:
            try:
                raw = await ws.recv()
            except ConnectionClosed as e:
                raise ServerError(self._describe(e)) from None
            msg = self._parse(raw)
            if msg is None:
                continue
            kind = msg.get("type")
            if kind == "partial":
                text = str(msg.get("text") or "")
                self._language = str(msg.get("language") or self._language)
                if text.strip():  # early partials are often empty: keep the label
                    self._last_partial = text
                    if not self._cancelled:
                        self.events.partial.emit(text)
            elif kind == "final":
                return str(msg.get("text") or "").strip(), str(msg.get("language") or self._language)
            elif kind == "error":
                self._server_error = str(msg.get("message") or "ASR server error")
            # "info" (language acknowledgement) and unknown types are ignored

    def _describe(self, e: ConnectionClosed) -> str:
        rcvd = e.rcvd
        code = rcvd.code if rcvd is not None else None
        reason = rcvd.reason if rcvd is not None else ""
        return describe_close(code, reason, self._server_error)

    @staticmethod
    def _parse(raw) -> dict | None:
        if not isinstance(raw, str):
            return None
        try:
            msg = json.loads(raw)
        except ValueError:
            return None
        return msg if isinstance(msg, dict) else None


class Qwen3StreamingBackend(AsrBackend):
    def __init__(self, runner: AsyncRunner, settings: Settings) -> None:
        self._runner = runner
        self._settings = settings

    def create_session(self, language: str) -> Qwen3StreamingSession:
        s = self._settings
        return Qwen3StreamingSession(self._runner, s.ws_url, language,
                                     s.ready_timeout_seconds, s.final_timeout_seconds)

    async def status(self) -> str:
        return await fetch_status(self._settings.http_base)
