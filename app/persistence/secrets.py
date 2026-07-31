"""Resolve provider secrets without persisting them."""

import os
from collections.abc import Mapping

from app.core.models import ProviderConfiguration
from app.services.errors import ProviderError


def resolve_api_key(
    configuration: ProviderConfiguration,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the configured API key from the environment."""
    if configuration.api_key_env is None:
        return None

    variable = configuration.api_key_env
    value = (os.environ if environ is None else environ).get(variable)
    if not value:
        raise ProviderError(
            f"Environment variable {variable!r} is missing or empty",
            recoverable=True,
        )
    return value
