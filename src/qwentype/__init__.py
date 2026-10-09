"""QwenType: hold Right Ctrl, speak, release — local Qwen3-ASR voice typing for Windows."""

__version__ = "0.1.0"


def main() -> int:
    from .main import main as _main

    return _main()
