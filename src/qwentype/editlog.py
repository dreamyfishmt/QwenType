"""Local log of review-box sessions (opt-in: Settings.review_log).

One JSON object per line in edits.jsonl: the ASR transcript, the refined text, each voice edit
and the text that was finally typed (or null when the box was discarded). With the final text
next to what was recognized, later versions can learn the user's frequent corrections.
The file is rotated to edits.1.jsonl when it grows past MAX_BYTES.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .settings import settings_dir

log = logging.getLogger(__name__)

MAX_BYTES = 5_000_000


@dataclass
class EditRecord:
    asr: str
    refined: str | None  # None = refinement was off or failed
    language: str = ""
    app: str = ""  # process name of the window the text was meant for
    edits: list[dict[str, str]] = field(default_factory=list)  # {"instruction", "result"}
    final: str | None = None  # None = discarded
    time: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))


def log_path() -> Path:
    return settings_dir() / "edits.jsonl"


def append(record: EditRecord, path: Path | None = None) -> None:
    path = path or log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            os.replace(path, path.with_name(path.stem + ".1" + path.suffix))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Could not write the edit log: %s", e)
