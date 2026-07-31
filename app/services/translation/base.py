"""Translation provider contract."""

from typing import Protocol

from app.core.models import TranslationContext, TranslationInput, TranslationResult


class TranslationProvider(Protocol):
    async def translate_blocks(
        self,
        blocks: list[TranslationInput],
        context: TranslationContext,
    ) -> list[TranslationResult]: ...
