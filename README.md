# QwenType

**English** | [简体中文](README.zh-CN.md)

Hold Right Ctrl, speak, release — local Qwen3-ASR voice typing for Windows.

![QwenType demo](docs/demo.gif)

QwenType is a tray-only app for Windows 10/11. While you hold **Right Ctrl**, it streams your microphone to a
[Qwen3-ASR server](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm) on your PC or your own server and
shows the live transcript in a small capsule at the bottom of the screen. When you release the key, the text is typed
into the focused app. Shortcuts that use Right Ctrl (Ctrl+C, …) keep working and never start a recording.

- Default language: Simplified Chinese (zh-CN). Mixed Chinese–English speech works, and English words stay in
  Latin script. Change it under **Language** in the tray menu (Auto-detect, English, 简体中文, 繁體中文, 日本語, 한국어).
- Optional **LLM Refinement**: an OpenAI-compatible model fixes obvious recognition errors (配森 → Python,
  杰森 → JSON) and nothing else. If it fails or rewrites too much, the unrefined text is used.
- Settings are stored in `%APPDATA%\QwenType\settings.json`, with the server token and the LLM API key encrypted by
  Windows DPAPI. The log is `%APPDATA%\QwenType\qwentype.log`.

## Install the dependencies

You need [uv](https://docs.astral.sh/uv/). It also installs a suitable Python version (3.11+) if necessary.

```powershell
uv sync
```

## Start the ASR server

QwenType only talks to the server over WebSocket/HTTP. It doesn't manage Docker itself. The server has three images
with the same API; see the [server README](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#readme) for
all details.

| Image | Hardware | Use |
|---|---|---|
| `latest-gpu` (recommended) | NVIDIA GPU, driver R580+ (CUDA 13) | Local machine, ~3 GB image |
| `latest-cpu` | Any x86-64 / ARM64 CPU, 2+ GB RAM | Small VPS reached over the internet, **requires a token** |
| vLLM (built locally) | RTX 30 series or newer | Many concurrent users, ~14 GB image |

### GPU on this PC (recommended)

Prerequisites: an NVIDIA GPU with driver R580 or newer, and Docker Desktop with the WSL 2 backend.

1. Download the model (~2.7 GB, pinned to the revisions the server was tested with):

   ```powershell
   $D = "D:/models/qwen3-asr-1.7b-onnx"
   uvx --from huggingface_hub hf download andrewleech/qwen3-asr-1.7b-onnx `
     config.json tokenizer.json embed_tokens.bin encoder.onnx `
     --revision df916193ac67e59347769891a21e10d81d12acdd --local-dir $D
   uvx --from huggingface_hub hf download sorryhyun/qwen3-asr-onnx-gqa `
     decoder-1.7b-fp16.onnx decoder-1.7b-fp16.onnx.data `
     --revision 075249f70b56cdded1cf4b189cbdde0fb77aeec1 --local-dir $D
   ```

2. Get [`compose.gpu.yaml`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/compose.gpu.yaml)
   and [`.env.gpu.example`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/.env.gpu.example)
   from the server repository, copy `.env.gpu.example` to `.env` and set `MODEL_DIR=D:/models`.

3. Start it (this pulls the image from GHCR) and wait for `Server is ready` in the log:

   ```powershell
   docker compose -f compose.gpu.yaml up -d
   docker compose -f compose.gpu.yaml logs -f
   ```

The server listens on `127.0.0.1:8907`, which is QwenType's default URL, so no token is needed on the same PC.

### CPU server (VPS)

Follow [CPU deployment](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#cpu-deployment) in the server
README (`compose.cpu.yaml`, `.env.cpu.example`). The CPU image **requires** `API_TOKEN` (generate one with
`openssl rand -hex 32`). With a domain and `--profile tls`, Caddy provides HTTPS automatically. Then, in QwenType's
**ASR Server…** dialog, fill in:

- **WebSocket URL**: `wss://your-domain/transcribe-streaming` (HTTPS via Caddy, recommended) or
  `ws://SERVER_IP:8907/transcribe-streaming` (unencrypted, trusted networks only)
- **API Token**: the server's `API_TOKEN`

On a CPU server, live partial results only cover the first 20 s of an utterance, and the final result is computed
after you release the key, so it can take a few seconds. Audio beyond `STREAM_MAX_SEC` (60 s on the CPU image) is
dropped by the server; QwenType then stops recording and shows "Server limit … reached".

### vLLM (advanced)

The original vLLM backend is no longer published and builds a ~14 GB image locally. Use it only for many
concurrent users; see [vLLM image (advanced)](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#vllm-image-advanced).

The first tray menu item shows the server state: `ASR: ready`, `ASR: loading_models`, `ASR: offline` or
`ASR: token rejected`.

## Configure the server connection

Open **ASR Server…** in the tray menu:

- **WebSocket URL**: default `ws://127.0.0.1:8907/transcribe-streaming`; use `wss://…` for a server behind HTTPS.
- **API Token**: the server's `API_TOKEN`, sent as `Authorization: Bearer <token>` and stored encrypted with
  Windows DPAPI. Leave it empty if the server has no token.
- **Hotwords**: optional context that biases recognition toward names and terms, e.g.
  `Vocabulary: Kubernetes, QwenType, 张三`. Keep it short; it is part of every decode. It is also passed to LLM
  refinement as preferred spellings.

**Test** checks `GET /ready` and verifies the token against `GET /health` (`ws://` → `http://`, `wss://` →
`https://`, same host and port), so a wrong token shows up before the first recording.

Other options in `%APPDATA%\QwenType\settings.json` (edit while QwenType isn't running): `max_record_seconds`
(default 60), `ready_timeout_seconds` (5), `final_timeout_seconds` (30), `capsule_blur` and `clipboard_apps`.
`clipboard_apps` lists process names such as `"mstsc.exe"` that drop typed Unicode input, so text for them is always
pasted instead. If no final result arrives within `final_timeout_seconds`, the last partial result is used.

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

GitHub Actions (`.github/workflows/build.yml`) runs the tests and builds `QwenType.exe` on Windows for every push and
pull request; the exe is attached as a workflow artifact. Pushing a `v*` tag also publishes it as a GitHub release.

## Elevated (administrator) windows

Windows doesn't let a normal process send input to an app running as administrator (UIPI). To dictate into
elevated windows, such as an admin terminal or Task Manager, run QwenType as administrator too.

## Build your own

The whole app was generated from [`client-prompt-qwentype.md`](client-prompt-qwentype.md). To build your own
variant, for example with another hotkey, ASR server or platform, edit that prompt and give it to a coding agent.

## Acknowledgements

Thanks to [yetone/voice-input-src](https://github.com/yetone/voice-input-src), whose client prompt this project's
prompt is based on.
