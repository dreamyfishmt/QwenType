import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from qwentype import llm
from qwentype.asr.qwen3 import build_ws_url, describe_close
from qwentype.audio import CHUNK_BYTES, Resampler, rms_level, to_pcm16
from qwentype.injector import utf16_units
from qwentype.settings import Settings, http_base_from_ws, is_valid_ws_url


class SettingsTest(unittest.TestCase):
    def test_http_base(self):
        self.assertEqual(http_base_from_ws("ws://127.0.0.1:8907/transcribe-streaming"), "http://127.0.0.1:8907")
        self.assertEqual(http_base_from_ws("wss://asr.example.com/x?y=1"), "https://asr.example.com")
        self.assertTrue(is_valid_ws_url("ws://localhost:1/a"))
        self.assertFalse(is_valid_ws_url("http://localhost:1/a"))

    def test_defaults(self):
        s = Settings()
        self.assertEqual(s.language, "zh-CN")
        self.assertEqual(s.ws_url, "ws://127.0.0.1:8907/transcribe-streaming")
        self.assertEqual(s.max_record_seconds, 60.0)

    def test_roundtrip_and_key_clearing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            s = Settings(language="ja", llm_api_key="sk-secret", llm_model="m", max_record_seconds=30)
            s.save(p)
            raw = json.loads(p.read_text(encoding="utf-8"))
            self.assertNotIn("llm_api_key", raw)
            self.assertNotIn("sk-secret", p.read_text(encoding="utf-8"))
            t = Settings.load(p)
            self.assertEqual((t.language, t.llm_api_key, t.llm_model, t.max_record_seconds),
                             ("ja", "sk-secret", "m", 30.0))
            t.llm_api_key = ""
            t.save(p)
            self.assertNotIn("llm_api_key_dpapi", json.loads(p.read_text(encoding="utf-8")))
            self.assertEqual(Settings.load(p).llm_api_key, "")

    def test_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(Settings.load(p).language, "zh-CN")
            p.write_text('{"language": "xx", "max_record_seconds": "a", "llm_enabled": true}', encoding="utf-8")
            s = Settings.load(p)
            self.assertEqual((s.language, s.max_record_seconds, s.llm_enabled), ("zh-CN", 60.0, True))


class AsrUrlTest(unittest.TestCase):
    def test_build_url(self):
        u = "ws://127.0.0.1:8907/transcribe-streaming"
        self.assertEqual(build_ws_url(u, ""), u)
        self.assertEqual(build_ws_url(u, "zh-CN"), u + "?language=zh-CN")
        self.assertEqual(build_ws_url(u + "?a=1", "en"), u + "?a=1&language=en")

    def test_close_codes(self):
        self.assertEqual(describe_close(1011, "Server not ready: loading_models", None), "ASR server not ready")
        self.assertEqual(describe_close(1003, "", "Unsupported language: xx"), "Unsupported language: xx")
        self.assertEqual(describe_close(1006, "", None), "Connection to ASR server lost")


class AudioTest(unittest.TestCase):
    def test_resample_48k_blocks(self):
        rate = 48000
        t = np.arange(rate) / rate
        x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        r = Resampler(rate)
        out = np.concatenate([r.process(x[i:i + 960]) for i in range(0, x.size, 960)])
        self.assertLessEqual(abs(out.size - 16000), 2)
        spec = np.abs(np.fft.rfft(out[1000:15000]))
        freq = np.argmax(spec) * 16000 / 14000
        self.assertAlmostEqual(freq, 440, delta=3)
        # 10 kHz is above the 8 kHz Nyquist: must be strongly attenuated, not aliased
        hi = (0.5 * np.sin(2 * np.pi * 10000 * t)).astype(np.float32)
        out_hi = Resampler(rate).process(hi)
        self.assertLess(np.sqrt(np.mean(out_hi[200:] ** 2)), 0.02)

    def test_resample_44100(self):
        r = Resampler(44100)
        out = np.concatenate([r.process(np.zeros(441, np.float32)) for _ in range(100)])
        self.assertLessEqual(abs(out.size - 16000), 2)

    def test_pcm_and_level(self):
        self.assertEqual(len(to_pcm16(np.zeros(1600, np.float32))), CHUNK_BYTES)
        self.assertEqual(rms_level(np.zeros(100, np.float32)), 0.0)
        self.assertGreater(rms_level(np.full(100, 0.3, np.float32)), 0.8)


class LlmTest(unittest.TestCase):
    def test_chat_url(self):
        self.assertEqual(llm.chat_url("https://api.openai.com/v1/"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(llm.chat_url("http://x/v1/chat/completions"), "http://x/v1/chat/completions")

    def test_guard(self):
        self.assertTrue(llm.accept_output("我用配森写杰森解析", "我用Python写JSON解析"))
        self.assertTrue(llm.accept_output("配森", "Python"))
        self.assertFalse(llm.accept_output("abc", ""))
        long = "这是一个比较长的句子，用来测试长度保护是否生效。" * 2
        self.assertFalse(llm.accept_output(long, long[:len(long) // 2]))

    def test_clean(self):
        self.assertEqual(llm.clean_output('"你好"', "你好"), "你好")
        self.assertEqual(llm.clean_output("「你好」", "你好"), "你好")


class InjectorTest(unittest.TestCase):
    def test_surrogates(self):
        self.assertEqual(utf16_units("a中"), [0x61, 0x4E2D])
        self.assertEqual(utf16_units("😀"), [0xD83D, 0xDE00])


if __name__ == "__main__":
    unittest.main()
