# QwenType

Hold Right Ctrl, speak, release — local Qwen3-ASR voice typing for Windows. 按住右 Ctrl 说话，松开即出字，本地运行。

![QwenType demo](docs/demo.gif)

QwenType is a tray-only app for Windows 10/11. While you hold **Right Ctrl**, it streams your microphone to a
[Qwen3-ASR server](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm) on your PC or your own server and shows the live
transcript in a small capsule at the bottom of the screen. When you release the key, the text is typed into
the focused app. Shortcuts that use Right Ctrl (Ctrl+C, …) keep working and never start a recording.

QwenType 是一个只驻留在系统托盘的 Windows 10/11 应用。按住**右 Ctrl** 时，它把麦克风音频实时发送到本机或你自己服务器上的
[Qwen3-ASR 服务](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm)，并在屏幕底部的小胶囊窗口里显示实时识别结果。
松开按键后，文字会输入到当前焦点所在的应用。用到右 Ctrl 的快捷键（Ctrl+C 等）照常可用，不会触发录音。

- Default language: Simplified Chinese (zh-CN). Mixed Chinese–English speech works, and English words stay in
  Latin script. Change it under **Language** in the tray menu (Auto-detect, English, 简体中文, 繁體中文, 日本語, 한국어).
- Optional **LLM Refinement**: an OpenAI-compatible model fixes obvious recognition errors (配森 → Python,
  杰森 → JSON) and nothing else. If it fails or rewrites too much, the unrefined text is used.
- Settings are stored in `%APPDATA%\QwenType\settings.json`, with the server token and the LLM API key encrypted by Windows DPAPI. The log
  is `%APPDATA%\QwenType\qwentype.log`.

- 默认语言为简体中文（zh-CN）。支持中英混说，英文单词保持英文拼写。可在托盘菜单 **Language** 中切换
  （自动检测、English、简体中文、繁體中文、日本語、한국어）。
- 可选的 **LLM 纠错**（LLM Refinement）：用兼容 OpenAI 接口的模型只修正明显的识别错误（配森 → Python、杰森 → JSON），
  不做其他改动。调用失败或改动过大时，直接使用未纠错的原文。
- 设置保存在 `%APPDATA%\QwenType\settings.json`，服务端 token 和 LLM API Key 用 Windows DPAPI 加密。日志位于
  `%APPDATA%\QwenType\qwentype.log`。

## Install the dependencies / 安装依赖

You need [uv](https://docs.astral.sh/uv/). It also installs a suitable Python version (3.11+) if necessary.

需要先安装 [uv](https://docs.astral.sh/uv/)，它会在需要时自动安装合适的 Python 版本（3.11+）。

```powershell
uv sync
```

## Start the ASR server / 启动 ASR 服务

QwenType only talks to the server over WebSocket/HTTP. It doesn't manage Docker itself. The server has three images
with the same API; see the [server README](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#readme) for
all details.

QwenType 只通过 WebSocket/HTTP 与服务通信，不负责管理 Docker。服务端有三种镜像，接口相同，详见
[服务端 README](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#readme)。

| Image / 镜像 | Hardware / 硬件 | Use / 适用 |
|---|---|---|
| `latest-gpu` (recommended / 推荐) | NVIDIA GPU, driver R580+ (CUDA 13) | Local machine, ~3 GB image / 本机使用，镜像约 3 GB |
| `latest-cpu` | Any x86-64 / ARM64 CPU, 2+ GB RAM | Small VPS reached over the internet, **requires a token** / 公网小 VPS，**必须设置 token** |
| vLLM (built locally / 本地构建) | RTX 30 series or newer | Many concurrent users, ~14 GB image / 多用户并发，镜像约 14 GB |

### GPU on this PC (recommended) / 本机 GPU（推荐）

Prerequisites: an NVIDIA GPU with driver R580 or newer, and Docker Desktop with the WSL 2 backend.

前提条件：驱动版本 R580 或更新的 NVIDIA 显卡，以及使用 WSL 2 后端的 Docker Desktop。

1. Download the model (~2.7 GB, pinned to the revisions the server was tested with) /
   下载模型（约 2.7 GB，固定为服务端测试过的版本）：

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

   从服务仓库获取 `compose.gpu.yaml` 和 `.env.gpu.example`，复制为 `.env`，并设置 `MODEL_DIR=D:/models`。

3. Start it (pulls the image from GHCR) and wait for `Server is ready` in the log /
   启动（会从 GHCR 拉取镜像），等日志出现 `Server is ready`：

   ```powershell
   docker compose -f compose.gpu.yaml up -d
   docker compose -f compose.gpu.yaml logs -f
   ```

The server listens on `127.0.0.1:8907`, which is QwenType's default URL, so no token is needed on the same PC.

服务监听 `127.0.0.1:8907`，正是 QwenType 的默认地址，本机使用无需 token。

### CPU server (VPS) / CPU 服务器（VPS）

Follow [CPU deployment](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#cpu-deployment) in the server
README (`compose.cpu.yaml`, `.env.cpu.example`). The CPU image **requires** `API_TOKEN` (generate one with
`openssl rand -hex 32`). With a domain and `--profile tls`, Caddy provides HTTPS automatically. Then, in QwenType's
**ASR Server…** dialog:

按服务端 README 的 [CPU deployment](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#cpu-deployment) 部署
（`compose.cpu.yaml`、`.env.cpu.example`）。CPU 镜像**必须**设置 `API_TOKEN`（可用 `openssl rand -hex 32` 生成）。
配置域名并使用 `--profile tls` 时，Caddy 会自动提供 HTTPS。然后在 QwenType 的 **ASR Server…** 窗口中填写：

- **WebSocket URL**: `wss://your-domain/transcribe-streaming` (HTTPS via Caddy, recommended / 推荐) or
  `ws://SERVER_IP:8907/transcribe-streaming` (unencrypted, trusted networks only / 不加密，仅限可信网络)
- **API Token**: the server's `API_TOKEN` / 服务端的 `API_TOKEN`

On a CPU server, live partial results only cover the first 20 s of an utterance, and the final result is computed
after you release the key, so it can take a few seconds. Audio beyond `STREAM_MAX_SEC` (60 s on the CPU image) is
dropped by the server; QwenType then stops recording and shows "Server limit … reached".

CPU 服务器只在每段话的前 20 秒发送实时中间结果，最终结果在松开按键后才计算，可能需要几秒。超过 `STREAM_MAX_SEC`
（CPU 镜像为 60 秒）的音频会被服务端丢弃，此时 QwenType 会自动结束录音并显示 "Server limit … reached"。

### vLLM (advanced) / vLLM（进阶）

The original vLLM backend is no longer published and builds a ~14 GB image locally. Use it only for many
concurrent users; see [vLLM image (advanced)](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#vllm-image-advanced).

原来的 vLLM 后端不再发布镜像，需要在本地构建约 14 GB 的镜像，只适合多用户并发场景，见
[vLLM image (advanced)](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#vllm-image-advanced)。

The first tray menu item shows the server state: `ASR: ready`, `ASR: loading_models`, `ASR: offline` or
`ASR: token rejected`.

托盘菜单第一项显示服务状态：`ASR: ready`、`ASR: loading_models`、`ASR: offline` 或 `ASR: token rejected`。

## Configure the server connection / 配置服务连接

Open **ASR Server…** in the tray menu / 打开托盘菜单中的 **ASR Server…**：

- **WebSocket URL**: default `ws://127.0.0.1:8907/transcribe-streaming`; use `wss://…` for a server behind HTTPS.
  默认 `ws://127.0.0.1:8907/transcribe-streaming`；服务在 HTTPS 后面时用 `wss://…`。
- **API Token**: the server's `API_TOKEN`, sent as `Authorization: Bearer <token>` and stored encrypted with
  Windows DPAPI. Leave it empty if the server has no token.
  服务端的 `API_TOKEN`，以 `Authorization: Bearer <token>` 发送，用 Windows DPAPI 加密保存。服务端未设置 token 时留空。
- **Hotwords**: optional context that biases recognition toward names and terms, e.g.
  `Vocabulary: Kubernetes, QwenType, 张三`. Keep it short; it is part of every decode. It is also passed to LLM
  refinement as preferred spellings.
  可选的热词上下文，让识别更倾向于指定的人名和术语，例如 `Vocabulary: Kubernetes, QwenType, 张三`。
  请保持简短，每次解码都会带上它。它也会作为首选写法提供给 LLM 纠错。

**Test** checks `GET /ready` and verifies the token against `GET /health` (`ws://` → `http://`, `wss://` →
`https://`, same host and port), so a wrong token shows up before the first recording.

**Test** 会检查 `GET /ready`，并用 `GET /health` 验证 token（`ws://` → `http://`，`wss://` → `https://`，主机和端口不变），
token 填错会立刻提示，而不是等到录音时才失败。

Other options in `%APPDATA%\QwenType\settings.json` (edit while QwenType isn't running): `max_record_seconds`
(default 60), `ready_timeout_seconds` (5), `final_timeout_seconds` (30), `capsule_blur` and `clipboard_apps`.
`clipboard_apps` lists process names such as `"mstsc.exe"` that drop typed Unicode input, so text for them is always
pasted instead. If no final result arrives within `final_timeout_seconds`, the last partial result is used.

`%APPDATA%\QwenType\settings.json` 中的其他选项（请在 QwenType 未运行时编辑）：`max_record_seconds`（默认 60）、
`ready_timeout_seconds`（5）、`final_timeout_seconds`（30）、`capsule_blur` 和 `clipboard_apps`。`clipboard_apps`
列出会丢失 Unicode 键入的进程名（如 `"mstsc.exe"`），对这些程序始终改用粘贴方式输入。如果在 `final_timeout_seconds`
内没有收到最终结果，则使用最后一次的中间结果。

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
