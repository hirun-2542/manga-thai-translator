import pytest
from PySide6.QtWidgets import QDialog

from app.core.models import ProviderConfiguration
from app.ui.provider_dialog import (
    OLLAMA_BASE_URL,
    OPENAI_BASE_URL,
    TranslationProviderDialog,
)


def test_provider_defaults_are_explicit_without_an_agent_model(qapp) -> None:
    dialog = TranslationProviderDialog()

    assert [
        dialog.provider_combo.itemData(index) for index in range(dialog.provider_combo.count())
    ] == ["openai-compatible", "ollama"]
    assert dialog.base_url_edit.text() == OPENAI_BASE_URL
    assert dialog.model_edit.text() == ""
    assert "gpt-5.6-sol" not in dialog.model_edit.placeholderText()

    dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData("ollama"))

    assert dialog.base_url_edit.text() == OLLAMA_BASE_URL
    assert dialog.model_edit.text() == ""


def test_existing_configuration_round_trips_after_acceptance(qapp) -> None:
    existing = ProviderConfiguration(
        provider="openai-compatible",
        base_url="https://translator.example/v1",
        model="user-selected-model",
        api_key_env="TRANSLATOR_API_KEY",
        temperature=0.7,
        timeout_seconds=12.5,
        retry_count=4,
        instructions="Keep honorifics.",
    )
    dialog = TranslationProviderDialog(existing)

    assert dialog.provider_combo.currentData() == existing.provider
    assert dialog.base_url_edit.text() == existing.base_url
    assert dialog.model_edit.text() == existing.model
    assert dialog.api_key_env_edit.text() == existing.api_key_env
    assert dialog.temperature_spin.value() == existing.temperature
    assert dialog.timeout_spin.value() == existing.timeout_seconds
    assert dialog.retry_spin.value() == existing.retry_count
    assert dialog.instructions_edit.toPlainText() == existing.instructions

    dialog.accept()

    assert dialog.configuration() == existing


def test_invalid_configuration_keeps_dialog_open_with_accessible_error(qapp) -> None:
    dialog = TranslationProviderDialog()
    dialog.show()
    dialog.model_edit.setText("user-selected-model")
    dialog.api_key_env_edit.setText("not a valid environment name")

    dialog.accept()

    assert dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.error_label.isVisible()
    assert dialog.error_label.text()
    assert dialog.error_label.accessibleName()
    with pytest.raises(RuntimeError, match="only after"):
        dialog.configuration()


@pytest.mark.parametrize("base_url", ["", "ftp://provider.example", "https:///v1"])
def test_invalid_base_url_keeps_dialog_open(qapp, base_url: str) -> None:
    dialog = TranslationProviderDialog()
    dialog.show()
    dialog.model_edit.setText("user-selected-model")
    dialog.base_url_edit.setText(base_url)

    dialog.accept()

    assert dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert "absolute HTTP or HTTPS URL with a host" in dialog.error_label.text()
    assert dialog.error_label.isVisible()


def test_loading_unsupported_provider_is_rejected(qapp) -> None:
    configuration = ProviderConfiguration(provider="unsupported", model="user-selected-model")

    with pytest.raises(ValueError, match="Unsupported translation provider"):
        TranslationProviderDialog(configuration)


def test_ollama_disables_and_clears_api_key_environment_name(qapp) -> None:
    dialog = TranslationProviderDialog()
    dialog.api_key_env_edit.setText("TRANSLATOR_API_KEY")

    dialog.provider_combo.setCurrentIndex(dialog.provider_combo.findData("ollama"))
    dialog.model_edit.setText("local-model")
    dialog.accept()

    assert not dialog.api_key_env_edit.isEnabled()
    assert dialog.api_key_env_edit.text() == ""
    assert dialog.configuration().api_key_env is None


def test_privacy_notice_and_secret_reference_only(qapp) -> None:
    dialog = TranslationProviderDialog()
    notice = dialog.privacy_label.text().lower()

    assert "ocr text" in notice
    assert "block ids" in notice
    assert "project context" in notice
    assert "glossary" in notice
    assert "instructions" in notice
    assert "images are not sent" in notice
    assert "environment variable" in dialog.api_key_env_edit.toolTip().lower()
    assert not hasattr(dialog, "api_key_edit")
