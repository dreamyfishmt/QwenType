# QwenType

[English](README.md) | **简体中文**

按住右 Ctrl 说话，松开即出字，本地运行的 Qwen3-ASR Windows 语音输入。

QwenType 是一个只驻留在系统托盘的 Windows 10/11 应用。按住**右 Ctrl** 时，它把麦克风音频实时发送到本机或你自己服务器上的
[Qwen3-ASR 服务](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm)，并在屏幕底部的小胶囊窗口里显示实时识别结果。
松开按键后，文字会输入到当前焦点所在的应用。用到右 Ctrl 的快捷键（Ctrl+C 等）照常可用，不会触发录音。

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
配置域名并使用 `--profile tls` 时，Caddy 会自动提供 HTTPS。然后在 QwenType 的 **ASR Server…** 窗口中填写：

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

打开托盘菜单中的 **ASR Server…**：

- **WebSocket URL**：默认 `ws://127.0.0.1:8907/transcribe-streaming`；服务在 HTTPS 后面时用 `wss://…`。
- **API Token**：服务端的 `API_TOKEN`，以 `Authorization: Bearer <token>` 发送，用 Windows DPAPI 加密保存。
  服务端未设置 token 时留空。
- **Hotwords**：可选的热词上下文（即流式接口的 [`context`](https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm#ws-transcribe-streaming)），让识别更倾向于指定的人名和术语，例如 `Vocabulary: Kubernetes, QwenType, 张三`。
  请保持简短，每次解码都会带上它。它也会作为首选写法提供给 LLM 纠错。

**Test** 会检查 `GET /ready`，并用 `GET /health` 验证 token（`ws://` → `http://`，`wss://` → `https://`，主机和端口不变），
token 填错会立刻提示，而不是等到录音时才失败。

`%APPDATA%\QwenType\settings.json` 中的其他选项（请在 QwenType 未运行时编辑）：`max_record_seconds`（默认 60）、
`ready_timeout_seconds`（5）、`final_timeout_seconds`（30）、`capsule_blur` 和 `clipboard_apps`。`clipboard_apps`
列出会丢失 Unicode 键入的进程名（如 `"mstsc.exe"`），对这些程序始终改用粘贴方式输入。如果在 `final_timeout_seconds`
内没有收到最终结果，则使用最后一次的中间结果。

## 运行与构建

`build.ps1` 中的所有任务都通过 uv 执行：

```powershell
.\build.ps1 run       # uv run qwentype
.\build.ps1 build     # uv run pyinstaller qwentype.spec -> dist\QwenType.exe（无控制台窗口、单文件、精简 Qt）
.\build.ps1 install   # 把 exe 复制到 %LOCALAPPDATA%\Programs\QwenType，并创建名为 "QwenType" 的开始菜单快捷方式
.\build.ps1 clean     # 删除 build\、dist\ 和 __pycache__
```

如果 `PATH` 中有 `upx`，构建时会使用它压缩。构建结束时会打印被排除的模块、被裁掉的 Qt 文件和最终 exe 大小。
要开机自启，勾选托盘菜单中的 **Start with Windows**。

单元测试：`uv run -m unittest discover -s tests -t .`

GitHub Actions（`.github/workflows/build.yml`）会在每次 push 和 pull request 时在 Windows 上运行测试并构建
`QwenType.exe`，exe 作为 workflow artifact 提供下载。推送 `v*` 标签时还会把它发布为 GitHub Release。

## 管理员权限窗口

Windows 不允许普通进程向以管理员身份运行的程序发送输入（UIPI）。如果要向管理员权限的窗口（如管理员终端、任务管理器）
输入文字，QwenType 也需要以管理员身份运行。

## 自行开发

整个应用是根据 [`client-prompt-qwentype.md`](client-prompt-qwentype.md) 生成的。如果想开发自己的版本（例如换热键、
换 ASR 服务或换平台），可以修改这份提示词，再交给编程 agent 实现。

## 致谢

感谢 [yetone/voice-input-src](https://github.com/yetone/voice-input-src) 仓库提供的客户端提示词，本项目的提示词在其基础上改写而成。
