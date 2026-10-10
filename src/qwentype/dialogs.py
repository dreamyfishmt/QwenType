"""Settings dialog with three tabs: ASR server, LLM refinement and advanced options."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import llm
from .aio import AsyncRunner
from .asr.qwen3 import TOKEN_REJECTED, fetch_status, is_local_url
from .qtasync import run_async
from .settings import DEFAULT_WS_URL, Settings, http_base_from_ws, is_valid_ws_url, settings_dir


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray")
    return label


def _status_label() -> QLabel:
    label = QLabel("")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def parse_app_list(text: str) -> list[str]:
    """'mstsc.exe, vmconnect.exe' -> ['mstsc.exe', 'vmconnect.exe'] (commas, semicolons or spaces)."""
    seen: dict[str, None] = {}
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        if part.strip():
            seen.setdefault(part.strip(), None)
    return list(seen)


class AsrServerPage(QWidget):
    def __init__(self, settings: Settings, runner: AsyncRunner) -> None:
        super().__init__()
        self._runner = runner

        self.url = QLineEdit(settings.ws_url)
        self.url.setPlaceholderText(DEFAULT_WS_URL)
        reset = QPushButton("Default")
        reset.clicked.connect(lambda: self.url.setText(DEFAULT_WS_URL))
        url_row = QHBoxLayout()
        url_row.addWidget(self.url, 1)
        url_row.addWidget(reset)

        self.token = QLineEdit(settings.asr_token)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("(none)")
        self.token.setClearButtonEnabled(True)  # an empty field removes the stored token

        self.context = QLineEdit(settings.asr_context)
        self.context.setPlaceholderText("e.g. Vocabulary: Kubernetes, QwenType, 张三")
        self.context.setClearButtonEnabled(True)

        form = QFormLayout()
        form.addRow("WebSocket URL", url_row)
        form.addRow("API Token", self.token)
        form.addRow("Hotwords", self.context)

        self.status = _status_label()
        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            _note(
                "URL: ws://host:8907/transcribe-streaming, or wss://your-domain/transcribe-streaming "
                "for a server behind HTTPS. API Token: the server's API_TOKEN (required by the CPU image), "
                "encrypted with Windows DPAPI. Hotwords: short context that biases recognition toward "
                "names and terms; keep it short, it is part of every decode."
            )
        )
        layout.addWidget(self.status)
        layout.addStretch(1)
        layout.addLayout(test_row)

    def validate(self) -> str | None:
        if not is_valid_ws_url(self.url.text()):
            return "The URL must start with ws:// or wss:// and contain a host."
        return None

    def apply(self, settings: Settings) -> None:
        settings.ws_url = self.url.text().strip()
        settings.asr_token = self.token.text().strip()
        settings.asr_context = self.context.text().strip()

    def _test(self) -> None:
        error = self.validate()
        if error:
            self.status.setText(error)
            return
        url = self.url.text().strip()
        base = http_base_from_ws(url)
        token = self.token.text().strip()
        warning = ""
        if token and url.lower().startswith("ws://") and not is_local_url(url):
            warning = "\nWarning: the token is sent unencrypted over ws://; use wss:// for remote servers."
        self.status.setText(f"Checking {base} …")
        self.test_button.setEnabled(False)

        def done(result, error) -> None:
            self.test_button.setEnabled(True)
            if error is not None:
                self.status.setText(f"Error: {error}{warning}")
            elif result == TOKEN_REJECTED:
                self.status.setText(
                    "ASR: token rejected (HTTP 401). Check the API Token; the server's "
                    "API_TOKEN must match exactly." + warning
                )
            else:
                accepted = result == "ready" and token
                self.status.setText(f"ASR: {result}" + (" – token accepted" if accepted else "") + warning)

        run_async(self._runner, fetch_status(base, token), done)


class LlmPage(QWidget):
    def __init__(self, settings: Settings, runner: AsyncRunner) -> None:
        super().__init__()
        self._runner = runner

        self.base_url = QLineEdit(settings.llm_base_url)
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        self.api_key = QLineEdit(settings.llm_api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("(none)")
        self.api_key.setClearButtonEnabled(True)  # an empty field removes the stored key
        self.model = QLineEdit(settings.llm_model)
        self.model.setPlaceholderText("e.g. gpt-4o-mini, qwen-plus, deepseek-chat")
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setDecimals(1)
        self.timeout.setSuffix(" s")
        self.timeout.setValue(settings.llm_timeout_seconds)
        self.timeout.setToolTip("If the model doesn't answer in time, the unrefined text is typed.")

        form = QFormLayout()
        form.addRow("API Base URL", self.base_url)
        form.addRow("API Key", self.api_key)
        form.addRow("Model", self.model)
        form.addRow("Timeout", self.timeout)

        self.status = _status_label()
        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            _note(
                "OpenAI-compatible Chat Completions API. The API key is encrypted with "
                "Windows DPAPI for the current user. Leave it empty for servers without auth. "
                "Turn refinement on or off under LLM Refinement in the tray menu."
            )
        )
        layout.addWidget(self.status)
        layout.addStretch(1)
        layout.addLayout(test_row)

    def validate(self) -> str | None:
        base = self.base_url.text().strip().lower()
        if base and not base.startswith(("http://", "https://")):
            return "API Base URL must start with http:// or https://"
        return None

    def apply(self, settings: Settings) -> None:
        settings.llm_base_url = self.base_url.text().strip()
        settings.llm_api_key = self.api_key.text().strip()
        settings.llm_model = self.model.text().strip()
        settings.llm_timeout_seconds = self.timeout.value()

    def _test(self) -> None:
        error = self.validate()
        if error:
            self.status.setText(error)
            return
        if not self.model.text().strip():
            self.status.setText("Enter a model name first.")
            return
        self.status.setText("Testing …")
        self.test_button.setEnabled(False)

        def done(result, error) -> None:
            self.test_button.setEnabled(True)
            if error is None:
                self.status.setText(result or "OK")
            else:
                msg = str(error) or type(error).__name__
                self.status.setText(f"Failed: {msg[:300]}")

        base, key, model = self.base_url.text().strip(), self.api_key.text().strip(), self.model.text().strip()
        run_async(self._runner, llm.test_connection(base, key, model), done)


def _seconds(value: float, low: float, high: float, tip: str) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(1)
    box.setSuffix(" s")
    box.setValue(value)
    box.setToolTip(tip)
    return box


class AdvancedPage(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.max_record = _seconds(
            settings.max_record_seconds, 5, 600, "Recording stops automatically after this long."
        )
        self.ready_timeout = _seconds(
            settings.ready_timeout_seconds, 1, 60, "How long to wait for the server to accept a new stream."
        )
        self.final_timeout = _seconds(
            settings.final_timeout_seconds,
            5,
            300,
            "How long to wait for the final result after the key is released; then the last partial "
            "result is used. CPU servers need longer for long utterances.",
        )

        self.unicode_max = QSpinBox()
        self.unicode_max.setRange(1, 100_000)
        self.unicode_max.setSuffix(" characters")
        self.unicode_max.setValue(settings.unicode_max_chars)
        self.unicode_max.setToolTip("Longer text is pasted via the clipboard instead of typed.")

        self.clipboard_apps = QLineEdit(", ".join(settings.clipboard_apps))
        self.clipboard_apps.setPlaceholderText("e.g. mstsc.exe, vmconnect.exe")
        self.clipboard_apps.setToolTip("Process names of apps that drop typed Unicode input: text is always pasted.")

        self.history_size = QSpinBox()
        self.history_size.setRange(0, 50)
        self.history_size.setSpecialValueText("Off")
        self.history_size.setValue(settings.history_size)
        self.history_size.setToolTip("Recent transcripts in the tray menu. Kept in memory only, never saved.")

        self.copy_on_failure = QCheckBox("Copy the text to the clipboard when typing fails")
        self.copy_on_failure.setChecked(settings.copy_on_failure)

        self.capsule_blur = QCheckBox("Blur behind the capsule (acrylic)")
        self.capsule_blur.setChecked(settings.capsule_blur)
        self.capsule_blur.setToolTip("Windows draws the blur over the whole window rectangle, which can show as a box.")

        form = QFormLayout()
        form.addRow("Max recording", self.max_record)
        form.addRow("Server ready timeout", self.ready_timeout)
        form.addRow("Final result timeout", self.final_timeout)
        form.addRow("Paste text longer than", self.unicode_max)
        form.addRow("Always paste in", self.clipboard_apps)
        form.addRow("Recent transcripts", self.history_size)
        form.addRow("", self.copy_on_failure)
        form.addRow("", self.capsule_blur)

        open_folder = QPushButton("Open Settings Folder")
        open_folder.setToolTip(str(settings_dir()))
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(settings_dir()))))
        folder_row = QHBoxLayout()
        folder_row.addWidget(open_folder)
        folder_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_note("The settings folder also has the log file, qwentype.log."))
        layout.addStretch(1)
        layout.addLayout(folder_row)

    def validate(self) -> str | None:
        return None

    def apply(self, settings: Settings) -> None:
        settings.max_record_seconds = self.max_record.value()
        settings.ready_timeout_seconds = self.ready_timeout.value()
        settings.final_timeout_seconds = self.final_timeout.value()
        settings.unicode_max_chars = self.unicode_max.value()
        settings.clipboard_apps = parse_app_list(self.clipboard_apps.text())
        settings.history_size = self.history_size.value()
        settings.copy_on_failure = self.copy_on_failure.isChecked()
        settings.capsule_blur = self.capsule_blur.isChecked()


class SettingsDialog(QDialog):
    PAGES = ("asr", "llm", "advanced")

    def __init__(self, settings: Settings, runner: AsyncRunner, page: str = "asr") -> None:
        super().__init__(None)
        self.setWindowTitle("QwenType – Settings")
        self.setMinimumWidth(560)
        self._settings = settings

        self.asr = AsrServerPage(settings, runner)
        self.llm = LlmPage(settings, runner)
        self.advanced = AdvancedPage(settings)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.asr, "ASR Server")
        self.tabs.addTab(self.llm, "LLM Refinement")
        self.tabs.addTab(self.advanced, "Advanced")
        self.tabs.setCurrentIndex(self.PAGES.index(page) if page in self.PAGES else 0)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d9534f")
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.error, 1)
        buttons.addWidget(save)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addLayout(buttons)

    def _save(self) -> None:
        pages = (self.asr, self.llm, self.advanced)
        for page in pages:
            error = page.validate()
            if error:
                self.tabs.setCurrentWidget(page)
                self.error.setText(error)
                return
        for page in pages:
            page.apply(self._settings)
        self.accept()

    def exec_front(self) -> int:
        self.show()
        self.raise_()
        self.activateWindow()
        return self.exec()
