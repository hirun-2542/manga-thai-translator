"""Translation provider configuration dialog."""

from urllib.parse import urlsplit

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.models import ProviderConfiguration

OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_BASE_URL = "http://localhost:11434"


class TranslationProviderDialog(QDialog):
    """Edit runtime-only translation provider settings."""

    def __init__(
        self,
        configuration: ProviderConfiguration | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Translation Provider Settings")
        self._configuration: ProviderConfiguration | None = None

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenAI-compatible", "openai-compatible")
        self.provider_combo.addItem("Ollama (local)", "ollama")
        self.provider_combo.addItem("Codex CLI (logged in)", "codex-cli")
        self.provider_combo.setToolTip("Choose OpenAI-compatible, Ollama, or Codex CLI.")

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setToolTip("Provider server URL.")

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("Enter a translation model ID")
        self.model_edit.setToolTip("Model ID supplied by the selected provider.")

        self.api_key_env_edit = QLineEdit()
        self.api_key_env_edit.setPlaceholderText("Example: MANGA_TRANSLATION_API_KEY")
        self.api_key_env_edit.setToolTip(
            "Environment variable containing the API key; never enter the key itself."
        )

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setDecimals(2)
        self.temperature_spin.setToolTip("Translation sampling temperature from 0 to 2.")

        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 86400.0)
        self.timeout_spin.setSuffix(" seconds")
        self.timeout_spin.setToolTip("Maximum time to wait for one provider request.")

        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 100)
        self.retry_spin.setToolTip("Number of retries after a recoverable provider error.")

        self.instructions_edit = QPlainTextEdit()
        self.instructions_edit.setTabChangesFocus(True)
        self.instructions_edit.setPlaceholderText("Optional translation instructions")
        self.instructions_edit.setToolTip(
            "Optional instructions included with translation requests."
        )

        self.upload_images_checkbox = QCheckBox("Allow Codex to receive source page images")
        self.upload_images_checkbox.setChecked(False)
        self.upload_images_checkbox.setToolTip(
            "Opt in to sending source page images through the logged-in Codex CLI."
        )

        form = QFormLayout()
        form.addRow("Provider", self.provider_combo)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("Translation Model", self.model_edit)
        form.addRow("API key environment variable", self.api_key_env_edit)
        form.addRow("Temperature", self.temperature_spin)
        form.addRow("Timeout", self.timeout_spin)
        form.addRow("Retry count", self.retry_spin)
        form.addRow("Instructions", self.instructions_edit)
        form.addRow("Image upload", self.upload_images_checkbox)

        self.privacy_label = QLabel(
            "Only OCR text, block IDs, project context, glossary, and instructions are sent. "
            "Images are not sent by these providers."
        )
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setAccessibleName("Translation provider privacy notice")
        privacy_group = QGroupBox("Privacy")
        privacy_layout = QVBoxLayout(privacy_group)
        privacy_layout.addWidget(self.privacy_label)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("Configuration validation error")
        self.error_label.setVisible(False)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("OK")
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        self.button_box.setToolTip("Save or discard the translation provider settings.")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(privacy_group)
        layout.addWidget(self.error_label)
        layout.addWidget(self.button_box)

        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.upload_images_checkbox.toggled.connect(self._update_privacy_notice)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        if configuration is None:
            self._provider_changed()
            self.temperature_spin.setValue(0.2)
            self.timeout_spin.setValue(60.0)
            self.retry_spin.setValue(2)
        else:
            self._load(configuration)

    def configuration(self) -> ProviderConfiguration:
        """Return the validated configuration after successful acceptance."""
        if self.result() != QDialog.DialogCode.Accepted or self._configuration is None:
            raise RuntimeError("Configuration is available only after the dialog is accepted")
        return self._configuration

    def accept(self) -> None:
        """Validate the fields before closing the dialog."""
        provider = self.provider_combo.currentData()
        codex_cli = provider == "codex-cli"
        base_url = self.base_url_edit.text().strip()
        if not codex_cli:
            try:
                parsed_url = urlsplit(base_url)
                if (
                    parsed_url.scheme not in {"http", "https"}
                    or not parsed_url.hostname
                    or any(character.isspace() for character in parsed_url.netloc)
                ):
                    raise ValueError
                parsed_url.port
            except ValueError:
                self._configuration = None
                self.error_label.setText(
                    "Invalid configuration: Base URL must be an absolute HTTP or HTTPS URL with a host."
                )
                self.error_label.setVisible(True)
                return

        try:
            configuration = ProviderConfiguration(
                provider=provider,
                base_url=None if codex_cli else base_url,
                model=self.model_edit.text().strip(),
                api_key_env=(
                    None
                    if provider in {"ollama", "codex-cli"}
                    else self.api_key_env_edit.text().strip() or None
                ),
                temperature=self.temperature_spin.value(),
                timeout_seconds=self.timeout_spin.value(),
                retry_count=self.retry_spin.value(),
                instructions=self.instructions_edit.toPlainText(),
                uploads_images=codex_cli and self.upload_images_checkbox.isChecked(),
            )
        except ValidationError as error:
            self._configuration = None
            self.error_label.setText(f"Invalid configuration: {error.errors()[0]['msg']}")
            self.error_label.setVisible(True)
            return

        self._configuration = configuration
        self.error_label.clear()
        self.error_label.setVisible(False)
        super().accept()

    def _provider_changed(self, *_args: object) -> None:
        provider = self.provider_combo.currentData()
        codex_cli = provider == "codex-cli"
        ollama = provider == "ollama"
        self.base_url_edit.setText(
            "" if codex_cli else OLLAMA_BASE_URL if ollama else OPENAI_BASE_URL
        )
        self.base_url_edit.setEnabled(not codex_cli)
        self.upload_images_checkbox.setVisible(codex_cli)
        self.upload_images_checkbox.setEnabled(codex_cli)
        if not codex_cli:
            self.upload_images_checkbox.setChecked(False)
        if codex_cli:
            self.model_edit.setText("default")
        else:
            if self.model_edit.text() == "default":
                self.model_edit.clear()
        if ollama or codex_cli:
            self.api_key_env_edit.clear()
        self.api_key_env_edit.setEnabled(not (ollama or codex_cli))
        self._update_privacy_notice()

    def _update_privacy_notice(self, *_args: object) -> None:
        if self.provider_combo.currentData() == "codex-cli":
            image_notice = (
                "Source images will be sent through Codex."
                if self.upload_images_checkbox.isChecked()
                else "Images will not be sent."
            )
            self.privacy_label.setText(
                "OCR text, block IDs, project context, glossary, and instructions are sent "
                f"through the logged-in Codex CLI. {image_notice}"
            )
            return
        self.privacy_label.setText(
            "Only OCR text, block IDs, project context, glossary, and instructions are sent. "
            "Images are not sent by these providers."
        )

    def _load(self, configuration: ProviderConfiguration) -> None:
        index = self.provider_combo.findData(configuration.provider)
        if index < 0:
            raise ValueError(f"Unsupported translation provider: {configuration.provider!r}")
        self.provider_combo.setCurrentIndex(index)
        self.base_url_edit.setText(configuration.base_url or "")
        self.model_edit.setText(configuration.model)
        self.api_key_env_edit.setText(configuration.api_key_env or "")
        self.temperature_spin.setValue(configuration.temperature)
        self.timeout_spin.setValue(configuration.timeout_seconds)
        self.retry_spin.setValue(configuration.retry_count)
        self.instructions_edit.setPlainText(configuration.instructions)
        self.upload_images_checkbox.setChecked(
            configuration.provider == "codex-cli" and configuration.uploads_images
        )
        if self.provider_combo.currentData() in {"ollama", "codex-cli"}:
            self.api_key_env_edit.clear()
            self.api_key_env_edit.setEnabled(False)
        if self.provider_combo.currentData() == "codex-cli":
            self.base_url_edit.clear()
        self._update_privacy_notice()
