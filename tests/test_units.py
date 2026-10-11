import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from qwentype import llm
from qwentype.asr.qwen3 import auth_headers, build_ws_url, describe_close, is_local_url, parse_max_duration
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
        self.assertEqual(s.language, "")  # auto-detect: no language parameter
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
            self.assertEqual(
                (t.language, t.llm_api_key, t.llm_model, t.max_record_seconds), ("ja", "sk-secret", "m", 30.0)
            )
            t.llm_api_key = ""
            t.save(p)
            self.assertNotIn("llm_api_key_dpapi", json.loads(p.read_text(encoding="utf-8")))
            self.assertEqual(Settings.load(p).llm_api_key, "")

    def test_asr_token_and_context(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            Settings(asr_token="tok-123", asr_context="Vocabulary: QwenType").save(p)
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("tok-123", text)
            self.assertIn("asr_token_dpapi", text)
            t = Settings.load(p)
            self.assertEqual((t.asr_token, t.asr_context), ("tok-123", "Vocabulary: QwenType"))
            t.asr_token = ""
            t.save(p)
            self.assertEqual(Settings.load(p).asr_token, "")

    def test_migrates_old_final_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text('{"final_timeout_seconds": 10.0}', encoding="utf-8")  # v1 file
            self.assertEqual(Settings.load(p).final_timeout_seconds, 30.0)
            p.write_text('{"final_timeout_seconds": 15.0}', encoding="utf-8")  # user value kept
            self.assertEqual(Settings.load(p).final_timeout_seconds, 15.0)
            p.write_text('{"settings_version": 2, "final_timeout_seconds": 10.0}', encoding="utf-8")
            self.assertEqual(Settings.load(p).final_timeout_seconds, 10.0)

    def test_language_defaults_to_auto_detect(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            # Older files saved the old zh-CN default whether or not the user chose it.
            for version in ("", '"settings_version": 2, '):
                p.write_text("{" + version + '"language": "zh-CN"}', encoding="utf-8")
                self.assertEqual(Settings.load(p).language, "")
            p.write_text('{"settings_version": 2, "language": "en"}', encoding="utf-8")
            self.assertEqual(Settings.load(p).language, "en")
            # From v3 on, a saved language is an explicit choice and is kept.
            Settings(language="zh-CN").save(p)
            self.assertEqual(Settings.load(p).language, "zh-CN")
            Settings().save(p)
            self.assertEqual(Settings.load(p).language, "")

    def test_capsule_blur_off_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            self.assertFalse(Settings().capsule_blur)
            # v1/v2 files always stored the old default (true); there was no UI for it.
            p.write_text('{"settings_version": 2, "capsule_blur": true}', encoding="utf-8")
            self.assertFalse(Settings.load(p).capsule_blur)
            Settings(capsule_blur=True).save(p)  # from v3 on, true is a deliberate choice
            self.assertTrue(Settings.load(p).capsule_blur)

    def test_hotkey_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            s = Settings.load(p)
            self.assertEqual((s.hotkey, s.hotkey_mode, s.history_size), ("right_ctrl", "hold", 10))
            Settings(hotkey="mouse_back", hotkey_mode="toggle").save(p)
            s = Settings.load(p)
            self.assertEqual((s.hotkey, s.hotkey_mode), ("mouse_back", "toggle"))
            p.write_text('{"hotkey": "f99", "hotkey_mode": "sticky", "history_size": -3}', encoding="utf-8")
            s = Settings.load(p)
            self.assertEqual((s.hotkey, s.hotkey_mode, s.history_size), ("right_ctrl", "hold", 0))

    def test_reads_utf8_bom(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_bytes(b"\xef\xbb\xbf" + b'{"capsule_blur": true, "language": "en"}')
            s = Settings.load(p)
            self.assertEqual(s.language, "en")

    def test_bad_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(Settings.load(p).language, "")
            p.write_text('{"language": "xx", "max_record_seconds": "a", "llm_enabled": true}', encoding="utf-8")
            s = Settings.load(p)
            self.assertEqual((s.language, s.max_record_seconds, s.llm_enabled), ("", 60.0, True))


class AsrUrlTest(unittest.TestCase):
    def test_build_url(self):
        u = "ws://127.0.0.1:8907/transcribe-streaming"
        self.assertEqual(build_ws_url(u, ""), u)
        self.assertEqual(build_ws_url(u, "zh-CN"), u + "?language=zh-CN")
        self.assertEqual(build_ws_url(u + "?a=1", "en"), u + "?a=1&language=en")

    def test_auth_and_helpers(self):
        self.assertEqual(auth_headers(""), {})
        self.assertEqual(auth_headers(" abc "), {"Authorization": "Bearer abc"})
        self.assertEqual(parse_max_duration("max_duration_reached=60s"), 60.0)
        self.assertEqual(parse_max_duration("max_duration_reached=7.5s"), 7.5)
        self.assertIsNone(parse_max_duration("language=Chinese"))
        self.assertTrue(is_local_url("ws://127.0.0.1:8907/x"))
        self.assertTrue(is_local_url("ws://localhost:8907/x"))
        self.assertFalse(is_local_url("wss://asr.example.com/transcribe-streaming"))

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
        out = np.concatenate([r.process(x[i : i + 960]) for i in range(0, x.size, 960)])
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
        filler = "嗯，那个，我觉得这个方案，呃，就是，还可以吧。"
        self.assertTrue(llm.accept_output(filler, "我觉得这个方案还可以吧。"))  # filler removal shrinks a lot
        long = "这是一个比较长的句子，用来测试长度保护是否生效。" * 2
        self.assertFalse(llm.accept_output(long, long[: len(long) // 3]))  # summarized
        self.assertFalse(llm.accept_output(long, long + long[:20]))  # grew: answered or expanded

    def test_vocabulary_in_user_message(self):
        self.assertIn("QwenType", llm.build_user_message("t", "zh-CN", "Chinese", "Vocabulary: QwenType"))
        self.assertNotIn("vocabulary", llm.build_user_message("t", "zh-CN", "Chinese", "").lower())

    def test_clean(self):
        self.assertEqual(llm.clean_output('"你好"', "你好"), "你好")
        self.assertEqual(llm.clean_output("「你好」", "你好"), "你好")


class InjectorTest(unittest.TestCase):
    def test_surrogates(self):
        self.assertEqual(utf16_units("a中"), [0x61, 0x4E2D])
        self.assertEqual(utf16_units("😀"), [0xD83D, 0xDE00])


if __name__ == "__main__":
    unittest.main()
