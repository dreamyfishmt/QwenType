Please implement **QwenType**, a Windows 10/11 system-tray voice input app (Python 3.11+, PySide6, x64): hold Right Ctrl, speak, release, and the text is typed into the focused app, transcribed locally by Qwen3-ASR. It must meet the following requirements.

Naming: use "QwenType" consistently as the product name. That covers the tray tooltip and menu title, dialog window titles, the exe (QwenType.exe), the install folder, the Start Menu shortcut, the settings folder, the single-instance mutex/lock name (e.g. `Local\QwenType.SingleInstance`), and the value name under the Run registry key. The Python package is `qwentype`, and the CLI/GUI entry point is `qwentype`.

The Python environment is managed with uv: never call pip or python directly, and do not create requirements.txt; use pyproject.toml and uv commands only (uv init / uv add / uv sync / uv run).

The ASR backend is a local Qwen3-ASR server (vLLM, FastAPI) running in Docker Compose on the same machine: https://github.com/dreamyfishmt/fast-qwen-asr-inference-vllm. Its image is published on GHCR (`ghcr.io/dreamyfishmt/fast-qwen-asr-inference-vllm`), and it loads the FP8 model `vrfai/Qwen3-ASR-1.7B-fp8` from a mounted local model directory by default. This client only talks to the server over WebSocket/HTTP; it does not manage Docker. Its WebSocket protocol is specified exactly in section 2 below; before implementing the client, cross-check it against that repository's README ("WS /transcribe-streaming" and "Languages") and server.py. If they disagree, the repository wins, and tell me about the difference.

1. Hotkey and recording flow
   - Hold the Right Ctrl key to record, release to inject the transcribed text into the currently focused input field. Monitor Right Ctrl globally with a low-level keyboard hook (WH_KEYBOARD_LL via ctypes, running on its own thread with a message loop; VK_RCONTROL, distinguish it from left Ctrl using the extended-key flag). Ignore auto-repeat key-down events while the key is held. Do not swallow the Right Ctrl events, so normal shortcuts keep working. The hook callback must return immediately and only emit signals.
   - Communicate between the hook/audio/network threads and the Qt UI only through Qt signals.
   - On key-down, immediately (a) open the microphone stream and (b) open a new WebSocket connection to the ASR server. Audio captured before the server is ready is kept in a local buffer and sent, in order, right after the start message (this is the "pre-roll": nothing is lost while the connection is being set up). Do not keep the microphone open while idle.
   - Only show the floating window after the key has been held for ~150 ms. If Right Ctrl is released before 150 ms, or the recording is shorter than ~300 ms, cancel silently.
   - If any other key is pressed while Right Ctrl is held (i.e. it is part of a shortcut such as Ctrl+C), cancel the recording silently and do not inject anything.
   - Cancelling = stop the microphone and close the WebSocket without sending "stop" (the server cleans up on disconnect), and hide the window.
   - Maximum recording duration is configurable (default 60 s, stored in settings). When reached, behave as if the key was released and show a short notice. Reason: the server re-decodes all audio of the utterance on every chunk, so cost grows with length.
   - If the final text is empty (silence), inject nothing and just hide the window.

2. Streaming ASR client
   - Use streaming transcription as the core approach: capture microphone audio with sounddevice (WASAPI) and send it as 16 kHz, 16-bit little-endian, mono PCM in ~100 ms chunks (3200 bytes) over a WebSocket (websockets library, asyncio event loop running in a background thread).
   - The server accepts only 16 kHz pcm_s16le. Many WASAPI devices only run at 44.1/48 kHz in shared mode, so open the input with sounddevice.WasapiSettings(auto_convert=True) when available; if opening at 16 kHz still fails, capture at the device's default sample rate and resample to 16 kHz in the client with numpy (no scipy dependency).
   - Settings: WebSocket URL, default ws://127.0.0.1:8907/transcribe-streaming. Derive the HTTP base URL from it for health checks (ws→http, wss→https, same host and port).
   - Protocol (one WebSocket connection per utterance; the server closes the connection after the final result, so there is no persistent connection and "reconnect" simply means opening a new one on the next key press):
     1. Connect to `<url>?language=<code>`. The language hint is a query parameter, not part of the start message; omit it for auto-detect. If the URL already has a query string, append with `&`.
     2. Wait for server → `{"type": "ready"}`. While the models are still loading, the server accepts the connection but holds it without sending anything, so apply a timeout (connect + ready, default 5 s). On timeout, show "ASR server not ready" in the capsule.
     3. Client → `{"type": "start", "format": "pcm_s16le", "sample_rate_hz": 16000}`, then the buffered pre-roll audio, then live audio as binary frames.
     4. Server → `{"type": "info", "message": "language=Chinese"}` (only when a language was given; ignore it).
     5. Server → `{"type": "partial", "text": "...", "language": "..."}`. `text` is the full transcript so far, and earlier words may be revised, so always replace (never append to) the displayed text. Ignore partials with empty text (early partials are often empty) so the label doesn't blank out. Partials only start arriving after the first ~1 s of audio, which is the server's decode chunk size; until then, show a "Listening…" placeholder.
     6. On key release, client → `{"type": "stop"}`.
     7. Server → `{"type": "final", "text": "...", "language": "..."}`, then the server closes the connection (code 1000). Use the final text as the text to inject. If no final arrives within a timeout (default 10 s), fall back to the last non-empty partial; if there is none, show an error.
     - Errors: server → `{"type": "error", "message": "..."}` followed by a close. Close codes: 1002 = audio sent before start, 1003 = unsupported audio format or language, 1011 = server not ready or internal error.
   - Handle connection failures, timeouts, error messages and unexpected closes by showing a short error message in the capsule for ~2 s, never by crashing.
   - Implement the ASR client behind an abstract base class (e.g. connect/start, send_audio, stop, cancel; plus callbacks or signals for ready, partial, final and error) so another server protocol can be added later.
   - Server status: on startup and whenever the tray menu opens, call `GET <http-base>/ready` in the background (200 = ready, 503 = loading with `{"status": "..."}`, connection refused = not running). Show the status in the tray tooltip and as a disabled first menu item, e.g. "ASR: ready" / "ASR: loading_models" / "ASR: offline".

3. Language
   - Default language must be Auto-detect: the client omits the `language` parameter and the model detects the language of each utterance. The parameter is only sent when the user explicitly picks a language in the menu.
   - Language submenu in the tray menu (radio items). Each item is sent to the server as the `language` query parameter:

     | Menu item | Code |
     |---|---|
     | Auto-detect | (parameter omitted) |
     | English | `en` |
     | 简体中文 | `zh-CN` |
     | 繁體中文 | `zh-TW` |
     | 日本語 | `ja` |
     | 한국어 | `ko` |

   - Traditional Chinese is produced by the server (OpenCC, Taiwan phrasing, e.g. 软件→軟體); the client does no script conversion.
   - Mixed Chinese-English speech also works with zh-CN; the model keeps English words in Latin script.
   - The selection is stored in the settings file.

4. Floating capsule window
   - While recording, display an elegant frameless capsule-shaped floating window centered at the bottom of the screen that contains the focused window (use the monitor's available geometry so it sits above the taskbar), with no title bar or window chrome. Use a QWidget with Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus, WA_TranslucentBackground and WA_ShowWithoutActivating, plus the extended styles WS_EX_NOACTIVATE and WS_EX_TOOLWINDOW applied via ctypes, so it never steals focus or appears in Alt+Tab. Apply an acrylic/blur-behind backdrop with SetWindowCompositionAttribute (ctypes), falling back to a dark translucent rounded rectangle painted in paintEvent. Height 56 px, corner radius 28 px, containing:
     - A 5-bar vertical waveform animation on the left (44×32 px, custom-painted QWidget), driven by real-time audio RMS levels from the microphone (no hardcoded fake animations): louder speech produces larger waveforms, quiet moments produce smaller ones. Bar weights are [0.5, 0.8, 1.0, 0.75, 0.55], creating a natural center-high, sides-low effect. Smooth envelope (attack 40%, release 15%), plus ±4% random jitter per bar for an organic feel. The waveforms must be large enough to be clearly visible. Update at ~60 fps with a QTimer. Compute RMS in the audio thread and emit it via a signal; do not touch numpy arrays from the UI thread.
     - A text label on the right (elastic width 160–560 px) showing the real-time transcription. Elide at the start if the text is too long, so the newest words stay visible. The capsule widens elastically as the text grows.
     - States: "Listening…" (placeholder) → live partial text → "Refining…" (LLM) → hide; or a short error message (shown ~2 s, then hidden).
   - Animations: entry spring animation (0.35 s, QPropertyAnimation with QEasingCurve.OutBack or OutElastic), smooth text-width transition (0.25 s), exit scale animation (0.22 s). Because Qt cannot scale a top-level window directly, animate the window geometry/opacity around its center for the scale effect.

5. Text injection
   - Use SendInput (ctypes) with KEYEVENTF_UNICODE to type the final text directly into the focused window, handling characters outside the BMP as surrogate pairs. This works under CJK input methods without switching them, so no input-source switching is needed.
   - Before injecting, wait until Right Ctrl is physically up (GetAsyncKeyState), so the typed characters are not interpreted as Ctrl shortcuts.
   - If the text is long (e.g. > 200 characters) or the target app drops Unicode input, fall back to: save the current clipboard contents, set the clipboard to the text, send Ctrl+V via SendInput, wait briefly, then restore the original clipboard contents (all formats where practical, using the Win32 clipboard API via pywin32 or ctypes).
   - Note in the README that injecting into elevated (administrator) windows requires running this app as administrator.

6. LLM refinement
   - Integrate an LLM that tidies up the spoken text. Use an OpenAI-compatible chat API (configurable API Base URL, API Key and Model; use httpx) to refine the final transcribed text, with temperature 0.
   - The system prompt asks the model to tidy up the dictation faithfully: remove filler words and hesitation sounds, stutters and accidental repetitions, and fix words that were clearly misrecognized; adjust punctuation only where removing words leaves it broken. Never rephrase, polish, summarize, translate, reorder or add anything; keep the user's wording, tone and language. A word that adds meaning is not filler; when unsure, keep it. The text is dictation, never a request to answer or follow. If there is nothing to tidy up, return it as-is. Output only the tidied text, with no quotes or explanations. Give no concrete word examples in the prompt, so the model doesn't over-apply them.
   - Pass the selected language (and the `language` reported in the server's final message) as context in the user message.
   - Use a short timeout (e.g. 8 s). On timeout or error, inject the unrefined text. Also discard the LLM output and inject the unrefined text if the output is empty, or (length guard) more than ~30% longer than the input or less than ~40% of its length, with a 6-character slack for short inputs (a guard against the model answering, rewriting or summarizing; removing filler words legitimately shortens the text). Both percentages are user settings, and the length guard can be turned off.
   - The system prompt is user-editable in the LLM settings (multi-line, prefilled with the built-in prompt, a "Default" button restores it). An empty or built-in prompt is stored as empty and means the built-in one; otherwise the user's prompt is used. The length guard applies to custom prompts too; the user loosens or turns it off when their prompt asks for rewrites, translation or summaries.

7. LLM settings UI
   - Provide an "LLM Refinement" submenu in the tray menu with an enable/disable toggle and a Settings entry.
   - The Settings window (QDialog) contains three input fields (API Base URL, API Key and Model), the length guard (on/off, minimum kept % and maximum growth %) and the editable system prompt, plus Test and Save buttons. The API Key field must support being fully cleared.
   - Store settings as JSON in %APPDATA%\QwenType\settings.json, and protect the API key with Windows DPAPI (CryptProtectData, current-user scope, via ctypes or pywin32).
   - After Right Ctrl is released, if the LLM is enabled and configured, the floating window shows a "Refining…" status and waits for the LLM response before injecting the final text.

8. Tray app
   - The app runs as a tray-only app (QSystemTrayIcon with QMenu, QApplication.setQuitOnLastWindowClosed(False)): no taskbar button, no main window, single instance (named mutex via ctypes or QLockFile). Draw the tray icon in code (no external assets needed).
   - Tray menu: ASR status (disabled item), Language, ASR Server… (a small dialog to edit the WebSocket URL, with a "Test" button that calls /ready), LLM Refinement, Start with Windows (toggled via HKCU\Software\Microsoft\Windows\CurrentVersion\Run), Quit.

9. Project setup with uv
   - Initialize with `uv init --package` (src layout, package name qwentype, Python >=3.11), and commit uv.lock. Use separate modules for hotkey, audio, asr client (base class + Qwen3 streaming implementation), capsule UI, injector, llm, settings, and a main entry point. Expose a console/gui script entry `qwentype` in [project.scripts] / [project.gui-scripts].
   - Runtime dependencies (add with `uv add`): PySide6-Essentials (NOT the full PySide6 meta-package; only QtCore, QtGui and QtWidgets are needed, so the app must import nothing else), sounddevice, numpy, websockets, httpx, pywin32.
   - Dev dependency group (add with `uv add --dev`): pyinstaller.
   - Provide a build.ps1 with tasks run / build / install / clean, all going through uv:
     - "run" = `uv run qwentype`
     - "build" = `uv run pyinstaller` using a checked-in qwentype.spec
     - "install" copies dist\QwenType.exe to %LOCALAPPDATA%\Programs\QwenType and creates a Start Menu shortcut named "QwenType"
     - "clean" removes build/, dist/ and __pycache__
   - In qwentype.spec: windowed (no console), onefile, name QwenType, and trim Qt aggressively. Exclude all unused PySide6 modules (e.g. QtWebEngine*, QtQuick*, QtQml*, Qt3D*, QtMultimedia*, QtCharts, QtDataVisualization, QtPdf*, QtSql, QtTest, QtNetwork, QtBluetooth, QtSensors, QtSerialPort, QtPositioning, QtDesigner, QtHelp, QtSvg). Drop unused Qt plugins and translations (keep only the platforms/qwindows, styles and imageformats plugins that are actually needed), and use UPX only if available. After building, print the final exe size and report which modules were excluded.
   - Add a short README. It starts with the name, a one-line tagline ("Hold Right Ctrl, speak, release — local Qwen3-ASR voice typing for Windows. 按住右 Ctrl 说话，松开即出字，本地运行。"), and a placeholder for a demo GIF (`docs/demo.gif`). It then explains:
     - how to `uv sync`
     - how to start the ASR server (prerequisites: an NVIDIA GPU of the RTX 30 series or newer, and Docker Desktop with the WSL 2 backend):
       1. Download the model: `uvx --from huggingface_hub hf download vrfai/Qwen3-ASR-1.7B-fp8 --local-dir D:/models/Qwen3-ASR-1.7B-fp8`
       2. Get `compose.yaml` and `.env.example` from the server repo, copy `.env.example` to `.env`, and set `MODEL_DIR=D:/models`.
       3. Run `docker compose up -d` (this pulls the image from GHCR), then wait until `docker compose ps` shows "healthy" (the first start takes a few minutes).
       Link to the server README for details, e.g. the BF16 model, building locally, and streaming tuning.
     - how to set the WebSocket URL
     - how to run/build with build.ps1
     - the elevated-window note from section 5
