"""Deterministic offline translation provider."""

from app.core.models import TranslationContext, TranslationInput, TranslationResult


class MockTranslationProvider:
    async def translate_blocks(
        self,
        blocks: list[TranslationInput],
        context: TranslationContext,
    ) -> list[TranslationResult]:
        return [
            TranslationResult(
                id=block.id,
                translated_text=f"แปลไทย: {block.source_text}" if block.source_text else "",
            )
            for block in blocks
        ]
