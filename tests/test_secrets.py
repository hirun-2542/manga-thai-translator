import pytest

from app.core.models import ProviderConfiguration
from app.persistence.secrets import resolve_api_key
from app.services.errors import ProviderError

API_KEY_ENV = "MANGA_TRANSLATION_API_KEY"
SENTINEL_SECRET = "sentinel-secret-must-not-leak"


def configuration(api_key_env: str | None = API_KEY_ENV) -> ProviderConfiguration:
    return ProviderConfiguration(
        provider="openai-compatible",
        model="user-selected-model",
        api_key_env=api_key_env,
    )


def test_resolve_api_key_returns_none_without_reference() -> None:
    assert resolve_api_key(configuration(None), {API_KEY_ENV: SENTINEL_SECRET}) is None


def test_resolve_api_key_reads_present_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, SENTINEL_SECRET)

    assert resolve_api_key(configuration()) == SENTINEL_SECRET


@pytest.mark.parametrize("environ", [{}, {API_KEY_ENV: ""}])
def test_resolve_api_key_rejects_missing_or_empty_value_without_leaking_secret(
    environ: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, SENTINEL_SECRET)

    with pytest.raises(ProviderError) as error:
        resolve_api_key(configuration(), environ)

    assert error.value.recoverable is True
    assert API_KEY_ENV in str(error.value)
    assert SENTINEL_SECRET not in str(error.value)
    assert SENTINEL_SECRET not in repr(error.value)


def test_resolve_api_key_uses_injected_mapping_without_persisting_secret() -> None:
    provider_configuration = configuration()

    assert (
        resolve_api_key(provider_configuration, {API_KEY_ENV: SENTINEL_SECRET}) == SENTINEL_SECRET
    )
    assert SENTINEL_SECRET not in provider_configuration.model_dump_json()
