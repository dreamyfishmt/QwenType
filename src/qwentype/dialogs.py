"""Settings dialogs: ASR server URL and LLM refinement."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from . import llm
from .aio import AsyncRunner
from .asr.qwen3 import TOKEN_REJECTED, fetch_status
from .qtasync import run_async
from .settings import DEFAULT_WS_URL, Settings, http_base_from_ws, is_valid_ws_url


def _bring_to_front(dialog: QDialog) -> None:
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


class AsrServerDialog(QDialog):
    def __init__(self, settings: Settings, runner: AsyncRunner) -> None:
        super().__init__(None)
        self.setWindowTitle("QwenType – ASR Server")
        self.setMinimumWidth(520)
        self._settings = settings
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

        note = QLabel("URL: ws://host:8907/transcribe-streaming, or wss://your-domain/transcribe-streaming "
                      "for a server behind HTTPS. API Token: the server's API_TOKEN (required by the CPU image), "
                      "encrypted with Windows DPAPI. Hotwords: short context that biases recognition toward "
                      "names and terms; keep it short, it is part of every decode.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

    def _url(self) -> str | None:
        url = self.url.text().strip()
        if not is_valid_ws_url(url):
            self.status.setText("The URL must start with ws:// or wss:// and contain a host.")
            return None
        return url

    def _test(self) -> None:
        url = self._url()
        if url is None:
            return
        base = http_base_from_ws(url)
        self.status.setText(f"Checking {base} …")
        self.test_button.setEnabled(False)

        def done(result, error) -> None:
            self.test_button.setEnabled(True)
            if error is not None:
                self.status.setText(f"Error: {error}")
            elif result == TOKEN_REJECTED:
                self.status.setText("ASR: token rejected (HTTP 401). Check the API Token; the server's "
                                    "API_TOKEN must match exactly.")
            else:
                accepted = result == "ready" and self.token.text().strip()
                self.status.setText(f"ASR: {result}" + (" – token accepted" if accepted else ""))

        run_async(self._runner, fetch_status(base, self.token.text()), done)

    def _save(self) -> None:
        url = self._url()
        if url is None:
            return
        self._settings.ws_url = url
        self._settings.asr_token = self.token.text().strip()
        self._settings.asr_context = self.context.text().strip()
        self.accept()

    def exec_front(self) -> int:
        _bring_to_front(self)
        return self.exec()


class LlmSettingsDialog(QDialog):
    def __init__(self, settings: Settings, runner: AsyncRunner) -> None:
        super().__init__(None)
        self.setWindowTitle("QwenType – LLM Refinement Settings")
        self.setMinimumWidth(480)
        self._settings = settings
        self._runner = runner

        self.base_url = QLineEdit(settings.llm_base_url)
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        self.api_key = QLineEdit(settings.llm_api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("(none)")
        self.api_key.setClearButtonEnabled(True)  # an empty field removes the stored key
        self.model = QLineEdit(settings.llm_model)
        self.model.setPlaceholderText("e.g. gpt-4o-mini, qwen-plus, deepseek-chat")

        form = QFormLayout()
        form.addRow("API Base URL", self.base_url)
        form.addRow("API Key", self.api_key)
        form.addRow("Model", self.model)

        note = QLabel("OpenAI-compatible Chat Completions API. The API key is encrypted with "
                      "Windows DPAPI for the current user. Leave it empty for servers without auth.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

    def _values(self) -> tuple[str, str, str] | None:
        base, key, model = self.base_url.text().strip(), self.api_key.text().strip(), self.model.text().strip()
        if not base.lower().startswith(("http://", "https://")):
            self.status.setText("API Base URL must start with http:// or https://")
            return None
        return base, key, model

    def _test(self) -> None:
        values = self._values()
        if values is None:
            return
        base, key, model = values
        if not model:
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

        run_async(self._runner, llm.test_connection(base, key, model), done)

    def _save(self) -> None:
        values = self._values()
        if values is None:
            return
        self._settings.llm_base_url, self._settings.llm_api_key, self._settings.llm_model = values
        self.accept()

    def exec_front(self) -> int:
        _bring_to_front(self)
        return self.exec()
