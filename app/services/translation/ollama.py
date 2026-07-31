"""Ollama text-only translation provider."""

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
    thinking: str | None = None
    images: list[str] | None = None
    tool_calls: list[Any] | None = None


class _Response(_EnvelopeModel):
    model: str | None = None
    created_at: str | None = None
    message: _Message
    done: bool | None = None
    done_reason: str | None = None
    total_duration: int | None = None
    load_duration: int | None = None
    prompt_eval_count: int | None = None
    prompt_eval_duration: int | None = None
    eval_count: int | None = None
    eval_duration: int | None = None


class OllamaTranslationProvider:
    def __init__(
        self,
        configuration: ProviderConfiguration,
        *,
        transport: AsyncTransport = post_json,
        retry_delay: float = 0.25,
    ) -> None:
        self._configuration = configuration
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
            raise ProviderError("Ollama base URL is required", recoverable=False)
        payload: dict[str, Any] = {
            "model": self._configuration.model,
            "messages": build_messages(blocks, context, self._configuration.instructions),
            "stream": False,
            "format": "json",
            "options": {"temperature": self._configuration.temperature},
        }
        return await self._request(
            f"{base_url.rstrip('/')}/api/chat",
            payload,
            {"Content-Type": "application/json", "Accept": "application/json"},
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
                return validate_translation_response(response.message.content, blocks)
            except ProviderError as error:
                if not error.recoverable:
                    raise
                last_error = str(error)
            except (ValidationError, ValueError, TypeError) as error:
                last_error = f"invalid Ollama response: {error}"
            except (TimeoutError, OSError):
                last_error = "Ollama transport failed"
            if attempt + 1 < attempts and self._retry_delay:
                await asyncio.sleep(self._retry_delay)
        raise ProviderError(last_error, recoverable=True)
