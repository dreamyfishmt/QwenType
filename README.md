# QwenType

Hold Right Ctrl, speak, release — local Qwen3-ASR voice typing for Windows. 按住右 Ctrl 说话，松开即出字，本地运行。

![QwenType demo](docs/demo.gif)

QwenType is a tray-only app for Windows 10/11. While you hold **Right Ctrl**, it streams your microphone to a
local [Qwen3-ASR server](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm) and shows the live
transcript in a small capsule at the bottom of the screen. When you release the key, the text is typed into
the focused app. Shortcuts that use Right Ctrl (Ctrl+C, …) keep working and never start a recording.

- Default language: Simplified Chinese (zh-CN). Mixed Chinese–English speech works, and English words stay in
  Latin script. Change it under **Language** in the tray menu (Auto-detect, English, 简体中文, 繁體中文, 日本語, 한국어).
- Optional **LLM Refinement**: an OpenAI-compatible model fixes obvious recognition errors (配森 → Python,
  杰森 → JSON) and nothing else. If it fails or rewrites too much, the unrefined text is used.
- Settings are stored in `%APPDATA%\QwenType\settings.json`, with the API key encrypted by Windows DPAPI. The log
  is `%APPDATA%\QwenType\qwentype.log`.

## Install the dependencies

You need [uv](https://docs.astral.sh/uv/). It also installs a suitable Python version (3.11+) if necessary.

```powershell
uv sync
```

## Start the ASR server

QwenType only talks to the server over WebSocket/HTTP. It doesn't manage Docker itself.

Prerequisites: an NVIDIA GPU of the RTX 30 series or newer, and Docker Desktop with the WSL 2 backend.

1. Download the model:

   ```powershell
   uvx --from huggingface_hub hf download vrfai/Qwen3-ASR-1.7B-fp8 --local-dir D:/models/Qwen3-ASR-1.7B-fp8
   ```

2. Get [`compose.yaml`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/compose.yaml) and
   [`.env.example`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/.env.example) from the
   server repository. Copy `.env.example` to `.env` and set `MODEL_DIR=D:/models`.

3. Run `docker compose up -d`, which pulls the image from GHCR. Then wait until `docker compose ps` shows the
   container as **healthy**. The first start takes a few minutes.

The [server README](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#readme) covers the rest, such as the
BF16 model, building the image locally and streaming tuning (`STREAM_CHUNK_SIZE_SEC`, …).

The first tray menu item shows the server state: `ASR: ready`, `ASR: loading_models` or `ASR: offline`.

## Set the WebSocket URL

The default is `ws://127.0.0.1:8907/transcribe-streaming`. To change it, open **ASR Server…** in the tray menu.
**Test** calls `GET /ready` on the matching HTTP address (`ws://` → `http://`, `wss://` → `https://`, same host and
port).

You can also set `ws_url` in `%APPDATA%\QwenType\settings.json` while QwenType isn't running. The same file holds
`max_record_seconds` (default 60), `ready_timeout_seconds` (5), `final_timeout_seconds` (10), `capsule_blur` and
`clipboard_apps`. `clipboard_apps` lists process names such as `"mstsc.exe"` that drop typed Unicode input, so
text for them is always pasted instead.

## Run and build

`build.ps1` runs every task through uv:

```powershell
.\build.ps1 run       # uv run qwentype
.\build.ps1 build     # uv run pyinstaller qwentype.spec -> dist\QwenType.exe (windowed, single file, trimmed Qt)
.\build.ps1 install   # copies the exe to %LOCALAPPDATA%\Programs\QwenType and adds a "QwenType" Start Menu shortcut
.\build.ps1 clean     # removes build\, dist\ and __pycache__
```

If `upx` is on `PATH`, the build uses it. At the end, the build prints the excluded modules, the dropped Qt
files and the final exe size. To start QwenType automatically, use **Start with Windows** in the tray menu.

Unit tests: `uv run -m unittest discover -s tests -t .`

## Elevated (administrator) windows

Windows doesn't let a normal process send input to an app running as administrator (UIPI). To dictate into
elevated windows, such as an admin terminal or Task Manager, run QwenType as administrator too.
