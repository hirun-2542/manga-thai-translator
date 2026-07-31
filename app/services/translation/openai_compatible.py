"""OpenAI-compatible text-only translation provider."""

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.models import (
    ProviderConfiguration,
    TranslationContext,
    TranslationInput,
    TranslationResult,
)
from app.services.errors import ProviderError
from app.services.translation.http import AsyncTransport, post_json
from app.services.translation.validation import build_messages, validate_translation_response


class _EnvelopeModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Message(_EnvelopeModel):
    role: str | None = None
    content: str
    refusal: str | None = None
    annotations: list[Any] | None = None
    audio: Any = None
    tool_calls: list[Any] | None = None
    function_call: Any = None


class _Choice(_EnvelopeModel):
    index: int | None = None
    message: _Message
    logprobs: Any = None
    finish_reason: str | None = None


class _Response(_EnvelopeModel):
    id: str | None = None
    object: str | None = None
    created: int | None = None
    model: str | None = None
    choices: list[_Choice]
    usage: Any = None
    service_tier: str | None = None
    system_fingerprint: str | None = None


class OpenAICompatibleTranslationProvider:
    def __init__(
        self,
        configuration: ProviderConfiguration,
        *,
        api_key: str | None = None,
        transport: AsyncTransport = post_json,
        retry_delay: float = 0.25,
    ) -> None:
        self._configuration = configuration
        self._api_key = api_key
        self._transport = transport
        self._retry_delay = retry_delay

    async def translate_blocks(
        self,
        blocks: list[TranslationInput],
        context: TranslationContext,
    ) -> list[TranslationResult]:
        if not blocks:
            return []
        base_url = self._configuration.base_url
        if not base_url:
            raise ProviderError(
                "OpenAI-compatible base URL is required",
                recoverable=False,
            )
        payload: dict[str, Any] = {
            "model": self._configuration.model,
            "messages": build_messages(blocks, context, self._configuration.instructions),
            "temperature": self._configuration.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return await self._request(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            headers,
            blocks,
        )

    async def _request(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        blocks: list[TranslationInput],
    ) -> list[TranslationResult]:
        attempts = self._configuration.retry_count + 1
        last_error = "unknown provider response"
        for attempt in range(attempts):
            try:
                raw = await self._transport(
                    url=url,
                    payload=payload,
                    headers=headers,
                    timeout=self._configuration.timeout_seconds,
                )
                response = _Response.model_validate(raw)
                if len(response.choices) != 1:
                    raise ValueError("OpenAI-compatible response must contain exactly one choice")
                return validate_translation_response(response.choices[0].message.content, blocks)
            except ProviderError as error:
                if not error.recoverable:
                    raise
                last_error = str(error)
            except (ValidationError, ValueError, TypeError) as error:
                last_error = f"invalid OpenAI-compatible response: {error}"
            except (TimeoutError, OSError):
                last_error = "OpenAI-compatible transport failed"
            if attempt + 1 < attempts and self._retry_delay:
                await asyncio.sleep(self._retry_delay)
        raise ProviderError(last_error, recoverable=True)
