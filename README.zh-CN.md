# QwenType

[English](README.md) | **简体中文**

按住右 Ctrl 说话，松开即出字，本地运行的 Qwen3-ASR Windows 语音输入。

QwenType 是一个只驻留在系统托盘的 Windows 10/11 应用。按住**右 Ctrl** 时，它把麦克风音频实时发送到本机或你自己服务器上的
[Qwen3-ASR 服务](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm)，并在屏幕底部的小胶囊窗口里显示实时识别结果。
松开按键后，文字会输入到当前焦点所在的应用。用到右 Ctrl 的快捷键（Ctrl+C 等）照常可用，不会触发录音。

![QwenType：按住右 Ctrl，胶囊窗口显示实时识别结果，松开后文字输入到应用中](docs/capsule.gif)

- **快捷键**：默认是右 Ctrl。可在托盘菜单 **Hotkey** 中改为右 Alt / AltGr、右 Shift、Caps Lock、Scroll Lock、Pause
  或鼠标侧键（选 Caps Lock、Scroll Lock、Pause 或鼠标侧键时，该键归 QwenType 专用，不再有原来的功能）。勾选
  **Tap to start, tap to stop** 后无需一直按住：按一下开始，再按一下结束。两种模式下都可以按 **Esc** 取消当前录音。
- 托盘菜单 **Recent** 列出最近 10 条识别结果，点击即可复制。这些记录只保存在内存中。输入失败时，文字会被复制到剪贴板，不会丢失。

- 默认语言为自动检测：QwenType 不发送 `language` 参数，由模型逐段识别语言。如需固定语言，在托盘菜单
  **Language** 中选择（English、简体中文、繁體中文、日本語、한국어）；选简体中文时也支持中英混说，英文单词保持英文拼写。
- 可选的 **LLM 纠错**（LLM Refinement）：用兼容 OpenAI 接口的模型只修正明显的识别错误（配森 → Python、杰森 → JSON），
  不做其他改动。调用失败或改动过大时，直接使用未纠错的原文。
- 设置保存在 `%APPDATA%\QwenType\settings.json`，服务端 token 和 LLM API Key 用 Windows DPAPI 加密。日志位于
  `%APPDATA%\QwenType\qwentype.log`。

## 安装依赖

需要先安装 [uv](https://docs.astral.sh/uv/)，它会在需要时自动安装合适的 Python 版本（3.11+）。

```powershell
uv sync
```

## 启动 ASR 服务

QwenType 只通过 WebSocket/HTTP 与服务通信，不负责管理 Docker。服务端有三种镜像，接口相同，详见
[服务端 README](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#readme)。

| 镜像 | 硬件 | 适用 |
|---|---|---|
| `latest-gpu`（推荐） | NVIDIA 显卡，驱动 R580+（CUDA 13） | 本机使用，镜像约 3 GB |
| `latest-cpu` | 任意 x86-64 / ARM64 CPU，2 GB 以上内存 | 通过公网访问的小 VPS，**必须设置 token** |
| vLLM（本地构建） | RTX 30 系列或更新 | 多用户并发，镜像约 14 GB |

### 本机 GPU（推荐）

这是服务端 [Quick start: GPU](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#quick-start-gpu-onnx-runtime) 的精简版。

前提条件：驱动版本 R580 或更新的 NVIDIA 显卡、使用 WSL 2 后端的 Docker Desktop，以及
[uv](https://docs.astral.sh/uv/)（用于 `uvx`）。

1. 下载模型（约 2.25 GB）：

   ```powershell
   uvx --from huggingface_hub hf download dreamyfishmt/qwen3-asr-1.7b-onnx --local-dir D:/models/qwen3-asr-1.7b-onnx
   ```

   [`dreamyfishmt/qwen3-asr-1.7b-onnx`](https://huggingface.co/dreamyfishmt/qwen3-asr-1.7b-onnx) 原样打包了 `andrewleech/qwen3-asr-1.7b-onnx`
   的词嵌入和分词器，以及 `sorryhyun/qwen3-asr-onnx-gqa` 的 int4 解码器。编码器 `encoder.fp16.onnx` 是从 andrewleech 的
   FP32 `encoder.onnx` 转换来的 FP16 版本。来源版本和验证结果见[模型卡](https://huggingface.co/dreamyfishmt/qwen3-asr-1.7b-onnx)。

   如果下载时 `cas-server.xethub.hf.co` 返回 401（有些代理会拦截 Hugging Face 的 Xet 存储），
   设置 `$env:HF_HUB_DISABLE_XET = "1"` 后重试。

2. 从服务仓库获取 [`compose.gpu.yaml`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/compose.gpu.yaml)
   和 [`.env.gpu.example`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm/blob/main/.env.gpu.example)。
   不 clone 仓库的话，在一个空文件夹里运行下面两条命令（PowerShell 里要用 `curl.exe`，不要用 `curl`）：

   ```powershell
   curl.exe -LO https://raw.githubusercontent.com/dreamyfishmt/fast-qwen-asr-inference-vllm/main/compose.gpu.yaml
   curl.exe -L -o .env https://raw.githubusercontent.com/dreamyfishmt/fast-qwen-asr-inference-vllm/main/.env.gpu.example
   ```

   这样已经生成了 `.env`，只需把其中的 `MODEL_DIR` 改成 `D:/models`。也可以 `git clone` 服务端仓库，
   里面还有下载脚本和测试样本（这时需要把 `.env.gpu.example` 复制为 `.env`），参见
   [Quick start: GPU](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#quick-start-gpu-onnx-runtime)。

3. 启动（会从 GHCR 拉取镜像），等日志先出现 `provider cuda`，再出现 `Server is ready`：

   ```powershell
   docker compose -f compose.gpu.yaml up -d
   docker compose -f compose.gpu.yaml logs -f
   ```

服务监听 `127.0.0.1:8907`，正是 QwenType 的默认地址，本机使用无需 token。

### CPU 服务器（VPS）

按服务端 README 的 [CPU deployment](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#cpu-deployment) 部署
（`compose.cpu.yaml`、`.env.cpu.example`）。CPU 镜像**必须**设置 `API_TOKEN`
（见 [Authentication](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#authentication)，可用 `openssl rand -hex 32` 生成）。
配置域名并使用 `--profile tls` 时，Caddy 会自动提供 HTTPS。然后在 QwenType 的 **Settings…** 窗口（**ASR Server** 标签页）中填写：

- **WebSocket URL**：`wss://你的域名/transcribe-streaming`（经 Caddy 走 HTTPS，推荐），或
  `ws://服务器IP:8907/transcribe-streaming`（不加密，仅限可信网络）
- **API Token**：服务端的 `API_TOKEN`

CPU 服务器只在每段话的前 20 秒发送实时中间结果，最终结果在松开按键后才计算，可能需要几秒。超过 `STREAM_MAX_SEC`
（CPU 镜像为 60 秒）的音频会被服务端丢弃，此时 QwenType 会自动结束录音并显示 "Server limit … reached"。

### vLLM（进阶）

原来的 vLLM 后端不再发布镜像，需要在本地构建约 14 GB 的镜像，只适合多用户并发场景，见
[vLLM image (advanced)](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#vllm-image-advanced)。

托盘菜单第一项显示服务状态：`ASR: ready`、`ASR: loading_models`、`ASR: offline` 或 `ASR: token rejected`。

## 配置服务连接

打开托盘菜单中的 **Settings…**，**ASR Server** 标签页包含：

- **WebSocket URL**：默认 `ws://127.0.0.1:8907/transcribe-streaming`；服务在 HTTPS 后面时用 `wss://…`。
- **API Token**：服务端的 `API_TOKEN`，以 `Authorization: Bearer <token>` 发送，用 Windows DPAPI 加密保存。
  服务端未设置 token 时留空。
- **Hotwords**：可选的热词上下文（即流式接口的 [`context`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#ws-transcribe-streaming)），让识别更倾向于指定的人名和术语，例如 `Vocabulary: Kubernetes, QwenType, 张三`。
  请保持简短，每次解码都会带上它。它也会作为首选写法提供给 LLM 纠错。

**Test** 会检查 `GET /ready`，并用 `GET /health` 验证 token（`ws://` → `http://`，`wss://` → `https://`，主机和端口不变），
token 填错会立刻提示，而不是等到录音时才失败。

**LLM Refinement** 标签页包含 API Base URL、Key、模型和超时时间（在托盘菜单 **LLM Refinement** 中开启纠错）。
**Advanced** 标签页包含：

| 选项 | 默认值 | |
|---|---|---|
| Max recording | 60 秒 | 录音达到该时长后自动停止。 |
| Server ready timeout | 5 秒 | 等待服务端接受新音频流的时间。 |
| Final result timeout | 30 秒 | 松开按键后等待最终结果的时间，超时则使用最后一次的中间结果。 |
| Paste text longer than | 200 字符 | 超过该长度的文字改用剪贴板粘贴，而不是逐字键入。 |
| Always paste in | （无） | 会丢失 Unicode 键入的进程名（如 `mstsc.exe`），对这些程序始终改用粘贴。 |
| Recent transcripts | 10 | 托盘 **Recent** 菜单保留的条数；`Off` 表示关闭。 |
| Copy the text to the clipboard when typing fails | 开 | |
| Blur behind the capsule | 关 | Windows 会把亚克力模糊画满整个窗口矩形，可能显示成一个方框。 |

**Open Settings Folder** 会打开 `%APPDATA%\QwenType`，日志文件也在这里。这些选项同样保存在 `settings.json` 中
（`max_record_seconds`、`ready_timeout_seconds`、`final_timeout_seconds`、`unicode_max_chars`、`clipboard_apps`、
`history_size`、`copy_on_failure`、`capsule_blur`、`hotkey`、`hotkey_mode`）；请只在 QwenType 未运行时手动编辑该文件。

## 运行与构建

`build.ps1` 中的所有任务都通过 uv 执行：

```powershell
.\build.ps1 run       # uv run qwentype
.\build.ps1 build     # uv run pyinstaller qwentype.spec -> dist\QwenType.exe（无控制台窗口、单文件、精简 Qt）
.\build.ps1 install   # 把 exe 复制到 %LOCALAPPDATA%\Programs\QwenType，并创建名为 "QwenType" 的开始菜单快捷方式
.\build.ps1 clean     # 删除 build\、dist\ 和 __pycache__
.\build.ps1 check     # 运行 ruff、pyright 和单元测试（与 CI 相同）
```

如果 `PATH` 中有 `upx`，构建时会使用它压缩。构建结束时会打印被排除的模块、被裁掉的 Qt 文件和最终 exe 大小。
要开机自启，勾选托盘菜单中的 **Start with Windows**。

开发检查（都在 `dev` 依赖组中）：

```powershell
uv run -m unittest discover -s tests -t .   # 单元测试
uv run ruff check                           # 代码检查
uv run ruff format                          # 格式化（CI 运行 `ruff format --check`）
uv run pyright                              # 类型检查
```

`docs/capsule.gif` 由 `uv run python scripts/make_capsule_gif.py` 根据真实的胶囊窗口代码渲染生成。

GitHub Actions（`.github/workflows/build.yml`）会在每次 push 和 pull request 时在 Linux 上运行 ruff 和 pyright，并在 Windows 上运行测试并构建
`QwenType.exe`，exe 作为 workflow artifact 提供下载（`QwenType-<版本>-<提交>.exe`）。推送 `v*` 标签（如 `v1.2.3`）时，
会用这个版本号构建（写入文件属性、托盘提示和日志），并把 `QwenType-v1.2.3.exe` 发布为 GitHub Release。

## 管理员权限窗口

Windows 不允许普通进程向以管理员身份运行的程序发送输入（UIPI）。如果要向管理员权限的窗口（如管理员终端、任务管理器）
输入文字，QwenType 也需要以管理员身份运行。

## 自行开发

整个应用是根据 [`client-prompt-qwentype.md`](client-prompt-qwentype.md) 生成的。如果想开发自己的版本（例如换热键、
换 ASR 服务或换平台），可以修改这份提示词，再交给编程 agent 实现。

## 致谢

感谢 [yetone/voice-input-src](https://github.com/yetone/voice-input-src) 仓库提供的客户端提示词，本项目的提示词在其基础上改写而成。

## 许可证

Copyright (c) 2026 dreamyfishmt

QwenType 是自由软件：你可以依据自由软件基金会发布的 [GNU Affero 通用公共许可证](LICENSE) 第 3 版或（由你选择）任何更新的版本
（`AGPL-3.0-or-later`）重新分发和/或修改它。本软件不提供任何担保，详见许可证全文。以上为说明性译文，具有法律效力的是英文许可证原文。

更早的版本以 MIT 许可证发布，已依据该许可证获得的副本仍可继续按 MIT 条款使用。
