"""QwenType: hold Right Ctrl, speak, release — local Qwen3-ASR voice typing for Windows."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: pyproject.toml. CI sets it from the git tag before building,
    # and qwentype.spec bundles the package metadata into the exe.
    __version__ = version("qwentype")
except PackageNotFoundError:  # running from a plain source tree
    __version__ = "0.0.0+unknown"


def main() -> int:
    from .main import main as _main

    return _main()
