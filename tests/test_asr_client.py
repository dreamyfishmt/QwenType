"""Runs the real client against a fake server that follows the
fast-qwen-asr-inference-vllm WS /transcribe-streaming protocol."""

import asyncio
import json
import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QObject, Slot
from websockets.asyncio.server import serve

from qwentype.aio import AsyncRunner
from qwentype.asr.qwen3 import Qwen3StreamingSession, fetch_status


class FakeServer:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.received = bytearray()
        self.query = None
        self.order_ok = True
        self.loop = asyncio.new_event_loop()
        self.started = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self.started.wait(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        async with serve(self._handler, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self.started.set()
            await asyncio.Future()

    async def _handler(self, ws):
        self.query = ws.request.path
        if self.mode == "hang":
            await asyncio.sleep(30)
            return
        if self.mode == "badlang":
            await ws.send(json.dumps({"type": "error", "message": "Unsupported language: xx"}))
            await ws.close(1003)
            return
        await asyncio.sleep(0.3)  # models "loading": the client buffers pre-roll audio
        await ws.send(json.dumps({"type": "ready"}))
        started = False
        async for msg in ws:
            if isinstance(msg, bytes):
                if not started:
                    self.order_ok = False
                self.received += msg
                if len(self.received) >= 32000 and len(self.received) - len(msg) < 32000:
                    await ws.send(json.dumps({"type": "partial", "text": "", "language": "Chinese"}))
                    await ws.send(json.dumps({"type": "partial", "text": "你好", "language": "Chinese"}))
                continue
            data = json.loads(msg)
            if data["type"] == "start":
                started = data == {"type": "start", "format": "pcm_s16le", "sample_rate_hz": 16000}
                await ws.send(json.dumps({"type": "info", "message": "language=Chinese"}))
            elif data["type"] == "stop":
                if self.mode == "nofinal":
                    await asyncio.sleep(30)
                    return
                await ws.send(json.dumps({"type": "final", "text": "你好世界", "language": "Chinese"}))
                await ws.close(1000)
                return


class Collector(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    @Slot()
    def ready(self):
        self.events.append(("ready",))

    @Slot(str)
    def partial(self, t):
        self.events.append(("partial", t))

    @Slot(str, str)
    def final(self, t, lang):
        self.events.append(("final", t, lang))

    @Slot(str)
    def error(self, m):
        self.events.append(("error", m))


class AsrClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.runner = AsyncRunner()

    @classmethod
    def tearDownClass(cls):
        cls.runner.stop()

    def _session(self, port, language="zh-CN", ready_timeout=5.0, final_timeout=10.0):
        s = Qwen3StreamingSession(self.runner, f"ws://127.0.0.1:{port}/transcribe-streaming", language,
                                  ready_timeout, final_timeout)
        c = Collector()
        s.events.ready.connect(c.ready)
        s.events.partial.connect(c.partial)
        s.events.final.connect(c.final)
        s.events.error.connect(c.error)
        return s, c

    def _wait(self, c, kinds=("final", "error"), timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if any(e[0] in kinds for e in c.events):
                return
            time.sleep(0.01)
        self.fail(f"timeout, events={c.events}")

    def test_full_utterance_with_preroll(self):
        srv = FakeServer()
        s, c = self._session(srv.port)
        s.start()
        for _ in range(12):  # 1.2 s, the first 0.3 s before the server is ready
            s.send_audio(b"\x01\x00" * 1600)
            time.sleep(0.1)
        self._wait(c, ("partial",))
        s.stop()
        self._wait(c)
        self.assertEqual(c.events[-1], ("final", "你好世界", "Chinese"))
        self.assertIn(("partial", "你好"), c.events)
        self.assertNotIn(("partial", ""), c.events)
        self.assertEqual(len(srv.received), 12 * 3200)
        self.assertTrue(srv.order_ok)
        self.assertEqual(srv.query, "/transcribe-streaming?language=zh-CN")

    def test_auto_language_omits_param(self):
        srv = FakeServer()
        s, c = self._session(srv.port, language="")
        s.start()
        s.send_audio(b"\x00\x00" * 1600)
        s.stop()
        self._wait(c)
        self.assertEqual(srv.query, "/transcribe-streaming")

    def test_ready_timeout(self):
        srv = FakeServer("hang")
        s, c = self._session(srv.port, ready_timeout=0.5)
        s.start()
        self._wait(c)
        self.assertEqual(c.events[-1], ("error", "ASR server not ready"))

    def test_server_error_message(self):
        srv = FakeServer("badlang")
        s, c = self._session(srv.port, language="xx")
        s.start()
        self._wait(c)
        self.assertEqual(c.events[-1], ("error", "Unsupported language: xx"))

    def test_offline(self):
        # Nothing listens on port 1. Windows retries refused connects for ~2 s.
        s, c = self._session(1, ready_timeout=15.0)
        s.start()
        self._wait(c, timeout=20.0)
        self.assertEqual(c.events[-1], ("error", "ASR server offline"))

    def test_final_timeout_falls_back_to_partial(self):
        srv = FakeServer("nofinal")
        s, c = self._session(srv.port, final_timeout=0.5)
        s.start()
        for _ in range(11):
            s.send_audio(b"\x01\x00" * 1600)
        self._wait(c, ("partial",))
        s.stop()
        self._wait(c)
        self.assertEqual(c.events[-1], ("final", "你好", "Chinese"))

    def test_cancel_sends_no_stop_and_no_signals(self):
        srv = FakeServer("nofinal")
        s, c = self._session(srv.port)
        s.start()
        s.send_audio(b"\x00\x00" * 1600)
        time.sleep(0.5)
        s.cancel()
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse([e for e in c.events if e[0] in ("final", "error")])

    def test_status_offline(self):
        fut = self.runner.submit(fetch_status("http://127.0.0.1:1", timeout=15.0))
        self.assertEqual(fut.result(20), "offline")


if __name__ == "__main__":
    unittest.main()
