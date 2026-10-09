"""Settings stored as JSON in %APPDATA%\\QwenType\\settings.json.

Secrets (the ASR server token and the LLM API key) are never written in plain
text on Windows: they are protected with DPAPI (CryptProtectData, current-user
scope) and stored base64-encoded.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)

APP_NAME = "QwenType"

DEFAULT_WS_URL = "ws://127.0.0.1:8907/transcribe-streaming"
DEFAULT_LANGUAGE = "zh-CN"
SETTINGS_VERSION = 2

# In-memory field -> JSON key of its DPAPI-protected copy.
SECRET_FIELDS = {"asr_token": "asr_token_dpapi", "llm_api_key": "llm_api_key_dpapi"}

# (menu label, language code); "" = auto-detect (query parameter omitted)
LANGUAGES: list[tuple[str, str]] = [
    ("Auto-detect", ""),
    ("English", "en"),
    ("简体中文", "zh-CN"),
    ("繁體中文", "zh-TW"),
    ("日本語", "ja"),
    ("한국어", "ko"),
]


def settings_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def http_base_from_ws(ws_url: str) -> str:
    """ws://host:port/path -> http://host:port (wss -> https)."""
    parts = urlsplit(ws_url.strip())
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme.lower(), parts.scheme.lower() or "http")
    return urlunsplit((scheme, parts.netloc, "", "", ""))


def is_valid_ws_url(ws_url: str) -> bool:
    try:
        parts = urlsplit(ws_url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("ws", "wss") and bool(parts.hostname)


# --- DPAPI -------------------------------------------------------------------

def _protect(secret: str) -> str:
    """Encrypt with DPAPI (current user) and return base64; plain base64 elsewhere."""
    data = secret.encode("utf-8")
    if sys.platform == "win32":
        import win32crypt  # pywin32

        data = win32crypt.CryptProtectData(data, APP_NAME, None, None, None, 0x01)  # UI_FORBIDDEN
    return base64.b64encode(data).decode("ascii")


def _unprotect(blob: str) -> str:
    data = base64.b64decode(blob)
    if sys.platform == "win32":
        import win32crypt

        _desc, data = win32crypt.CryptUnprotectData(data, None, None, None, 0x01)
    return data.decode("utf-8")


# --- Settings ----------------------------------------------------------------

@dataclass
class Settings:
    settings_version: int = SETTINGS_VERSION
    ws_url: str = DEFAULT_WS_URL
    # Hotwords / context sent in the "start" message to bias recognition,
    # e.g. "Vocabulary: Kubernetes, QwenType, 张三". Empty = not sent.
    asr_context: str = ""
    language: str = DEFAULT_LANGUAGE
    max_record_seconds: float = 60.0
    ready_timeout_seconds: float = 5.0
    # CPU servers compute the final result after "stop"; a long utterance on a
    # small VPS can take well over 10 s.
    final_timeout_seconds: float = 30.0
    # Text longer than this is pasted via the clipboard instead of typed.
    unicode_max_chars: int = 200
    # Process names (e.g. "mstsc.exe") that drop KEYEVENTF_UNICODE input: always paste.
    clipboard_apps: list[str] = field(default_factory=list)
    capsule_blur: bool = True

    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_timeout_seconds: float = 8.0
    # Secrets: kept in memory only, persisted encrypted (see SECRET_FIELDS).
    llm_api_key: str = field(default="", repr=False)
    # Shared secret of the ASR server (API_TOKEN), sent as "Authorization: Bearer <token>".
    asr_token: str = field(default="", repr=False)

    @property
    def http_base(self) -> str:
        return http_base_from_ws(self.ws_url)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url.strip() and self.llm_model.strip())

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        s = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return s
        except (OSError, ValueError) as e:
            log.warning("Could not read settings (%s); using defaults", e)
            return s
        if not isinstance(raw, dict):
            return s
        for f in fields(cls):
            if f.name in SECRET_FIELDS or f.name not in raw:
                continue
            value = raw[f.name]
            default = getattr(s, f.name)
            # Keep defaults for values of the wrong type (hand-edited files).
            if isinstance(default, bool):
                ok = isinstance(value, bool)
            elif isinstance(default, float):
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                value = float(value) if ok else value
            elif isinstance(default, int):
                ok = isinstance(value, int) and not isinstance(value, bool)
            elif isinstance(default, list):
                ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
            else:
                ok = isinstance(value, str)
            if ok:
                setattr(s, f.name, value)
        if s.language not in {code for _, code in LANGUAGES}:
            s.language = DEFAULT_LANGUAGE
        version = raw.get("settings_version")
        if not isinstance(version, int) or version < 2:
            # v1 stored the old 10 s default explicitly; move it to the new default.
            if s.final_timeout_seconds == 10.0:
                s.final_timeout_seconds = cls.final_timeout_seconds
        s.settings_version = SETTINGS_VERSION
        for name, key in SECRET_FIELDS.items():
            blob = raw.get(key)
            if isinstance(blob, str) and blob:
                try:
                    setattr(s, name, _unprotect(blob))
                except Exception as e:  # wrong user / corrupted
                    log.warning("Could not decrypt the stored %s: %s", name, e)
        return s

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        data = asdict(self)
        for name, key in SECRET_FIELDS.items():
            secret = data.pop(name)
            if secret:
                data[key] = _protect(secret)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: never leave a half-written settings file behind.
        fd, tmp = tempfile.mkstemp(prefix="settings.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
