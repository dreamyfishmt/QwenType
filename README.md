# QwenType

Hold Right Ctrl, speak, release — local Qwen3-ASR voice typing for Windows. 按住右 Ctrl 说话，松开即出字，本地运行。

![QwenType demo](docs/demo.gif)

QwenType is a tray-only app for Windows 10/11. While you hold **Right Ctrl**, it streams your microphone to a
local [Qwen3-ASR server](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm) and shows the live
transcript in a small capsule at the bottom of the screen. When you release the key, the text is typed into
the focused app. Shortcuts that use Right Ctrl (Ctrl+C, …) keep working and never start a recording.

QwenType 是一个只驻留在系统托盘的 Windows 10/11 应用。按住**右 Ctrl** 时，它把麦克风音频实时发送到本地的
[Qwen3-ASR 服务](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm)，并在屏幕底部的小胶囊窗口里显示实时识别结果。
松开按键后，文字会输入到当前焦点所在的应用。用到右 Ctrl 的快捷键（Ctrl+C 等）照常可用，不会触发录音。

- Default language: Simplified Chinese (zh-CN). Mixed Chinese–English speech works, and English words stay in
  Latin script. Change it under **Language** in the tray menu (Auto-detect, English, 简体中文, 繁體中文, 日本語, 한국어).
- Optional **LLM Refinement**: an OpenAI-compatible model fixes obvious recognition errors (配森 → Python,
  杰森 → JSON) and nothing else. If it fails or rewrites too much, the unrefined text is used.
- Settings are stored in `%APPDATA%\QwenType\settings.json`, with the API key encrypted by Windows DPAPI. The log
  is `%APPDATA%\QwenType\qwentype.log`.

- 默认语言为简体中文（zh-CN）。支持中英混说，英文单词保持英文拼写。可在托盘菜单 **Language** 中切换
  （自动检测、English、简体中文、繁體中文、日本語、한국어）。
- 可选的 **LLM 纠错**（LLM Refinement）：用兼容 OpenAI 接口的模型只修正明显的识别错误（配森 → Python、杰森 → JSON），
  不做其他改动。调用失败或改动过大时，直接使用未纠错的原文。
- 设置保存在 `%APPDATA%\QwenType\settings.json`，API Key 用 Windows DPAPI 加密。日志位于
  `%APPDATA%\QwenType\qwentype.log`。

## Install the dependencies / 安装依赖

You need [uv](https://docs.astral.sh/uv/). It also installs a suitable Python version (3.11+) if necessary.

需要先安装 [uv](https://docs.astral.sh/uv/)，它会在需要时自动安装合适的 Python 版本（3.11+）。

```powershell
uv sync
```

## Start the ASR server / 启动 ASR 服务

QwenType only talks to the server over WebSocket/HTTP. It doesn't manage Docker itself.

QwenType 只通过 WebSocket/HTTP 与服务通信，不负责管理 Docker。

Prerequisites: an NVIDIA GPU of the RTX 30 series or newer, and Docker Desktop with the WSL 2 backend.

前提条件：RTX 30 系列或更新的 NVIDIA 显卡，以及使用 WSL 2 后端的 Docker Desktop。

1. Download the model / 下载模型：

   ```powershell
   uvx --from huggingface_hub hf download vrfai/Qwen3-ASR-1.7B-fp8 --local-dir D:/models/Qwen3-ASR-1.7B-fp8
   ```

2. Get [`compose.yaml`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/compose.yaml) and
   [`.env.example`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/.env.example) from the
   server repository. Copy `.env.example` to `.env` and set `MODEL_DIR=D:/models`.

   从服务仓库获取 `compose.yaml` 和 `.env.example`，把 `.env.example` 复制为 `.env`，并设置 `MODEL_DIR=D:/models`。

3. Run `docker compose up -d`, which pulls the image from GHCR. Then wait until `docker compose ps` shows the
   container as **healthy**. The first start takes a few minutes.

   运行 `docker compose up -d`（会从 GHCR 拉取镜像），然后等到 `docker compose ps` 显示容器为 **healthy**。
   首次启动需要几分钟。

The [server README](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#readme) covers the rest, such as the
BF16 model, building the image locally and streaming tuning (`STREAM_CHUNK_SIZE_SEC`, …).

更多内容见[服务端 README](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#readme)，例如 BF16 模型、本地构建镜像和
流式参数调优（`STREAM_CHUNK_SIZE_SEC` 等）。

The first tray menu item shows the server state: `ASR: ready`, `ASR: loading_models` or `ASR: offline`.

托盘菜单第一项显示服务状态：`ASR: ready`、`ASR: loading_models` 或 `ASR: offline`。

## Set the WebSocket URL / 设置 WebSocket 地址

The default is `ws://127.0.0.1:8907/transcribe-streaming`. To change it, open **ASR Server…** in the tray menu.
**Test** calls `GET /ready` on the matching HTTP address (`ws://` → `http://`, `wss://` → `https://`, same host and
port).

默认地址为 `ws://127.0.0.1:8907/transcribe-streaming`。要修改，打开托盘菜单中的 **ASR Server…**。
**Test** 按钮会请求对应 HTTP 地址的 `GET /ready`（`ws://` → `http://`，`wss://` → `https://`，主机和端口不变）。

You can also set `ws_url` in `%APPDATA%\QwenType\settings.json` while QwenType isn't running. The same file holds
`max_record_seconds` (default 60), `ready_timeout_seconds` (5), `final_timeout_seconds` (10), `capsule_blur` and
`clipboard_apps`. `clipboard_apps` lists process names such as `"mstsc.exe"` that drop typed Unicode input, so
text for them is always pasted instead.

也可以在 QwenType 未运行时编辑 `%APPDATA%\QwenType\settings.json` 中的 `ws_url`。同一文件还包含
`max_record_seconds`（默认 60）、`ready_timeout_seconds`（5）、`final_timeout_seconds`（10）、`capsule_blur` 和
`clipboard_apps`。`clipboard_apps` 列出会丢失 Unicode 键入的进程名（如 `"mstsc.exe"`），对这些程序始终改用粘贴方式输入。

## Run and build / 运行与构建

`build.ps1` runs every task through uv:

`build.ps1` 中的所有任务都通过 uv 执行：

```powershell
.\build.ps1 run       # uv run qwentype
.\build.ps1 build     # uv run pyinstaller qwentype.spec -> dist\QwenType.exe (windowed, single file, trimmed Qt)
.\build.ps1 install   # copies the exe to %LOCALAPPDATA%\Programs\QwenType and adds a "QwenType" Start Menu shortcut
.\build.ps1 clean     # removes build\, dist\ and __pycache__
```

If `upx` is on `PATH`, the build uses it. At the end, the build prints the excluded modules, the dropped Qt
files and the final exe size. To start QwenType automatically, use **Start with Windows** in the tray menu.

如果 `PATH` 中有 `upx`，构建时会使用它压缩。构建结束时会打印被排除的模块、被裁掉的 Qt 文件和最终 exe 大小。
要开机自启，勾选托盘菜单中的 **Start with Windows**。

Unit tests / 单元测试：`uv run -m unittest discover -s tests -t .`

GitHub Actions (`.github/workflows/build.yml`) runs the tests and builds `QwenType.exe` on Windows for every push and
pull request; the exe is attached as a workflow artifact. Pushing a `v*` tag also publishes it as a GitHub release.

GitHub Actions（`.github/workflows/build.yml`）会在每次 push 和 pull request 时在 Windows 上运行测试并构建
`QwenType.exe`，exe 作为 workflow artifact 提供下载。推送 `v*` 标签时还会把它发布为 GitHub Release。

## Elevated (administrator) windows / 管理员权限窗口

Windows doesn't let a normal process send input to an app running as administrator (UIPI). To dictate into
elevated windows, such as an admin terminal or Task Manager, run QwenType as administrator too.

Windows 不允许普通进程向以管理员身份运行的程序发送输入（UIPI）。如果要向管理员权限的窗口（如管理员终端、任务管理器）
输入文字，QwenType 也需要以管理员身份运行。

## Build your own / 自行开发

The whole app was generated from [`client-prompt-qwentype.md`](client-prompt-qwentype.md). To build your own
variant, for example with another hotkey, ASR server or platform, edit that prompt and give it to a coding agent.

整个应用是根据 [`client-prompt-qwentype.md`](client-prompt-qwentype.md) 生成的。如果想开发自己的版本（例如换热键、
换 ASR 服务或换平台），可以修改这份提示词，再交给编程 agent 实现。

## Acknowledgements / 致谢

Thanks to [yetone/voice-input-src](https://github.com/yetone/voice-input-src), whose client prompt this project's
prompt is based on.

感谢 [yetone/voice-input-src](https://github.com/yetone/voice-input-src) 仓库提供的客户端提示词，本项目的提示词在其基础上改写而成。
