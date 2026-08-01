"""Codex CLI text and opted-in page-image translation provider."""

import asyncio
import json
import tempfile
from collections.abc import Sequence
from math import ceil
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from app.core.coordinates import clamp_bbox
from app.core.models import (
    BlockStatus,
    BoundingBox,
    Page,
    ProviderConfiguration,
    SourceLanguage,
    TextBlock,
    TranslationContext,
    TranslationInput,
    TranslationResult,
)
from app.core.reading_order import normalize_reading_order
from app.services.errors import ProviderError
from app.services.translation.validation import build_messages, validate_translation_response

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "translated_text": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["id", "translated_text", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}
_TRANSLATION_ONLY_INSTRUCTION = (
    "This is a translation-only request. Do not inspect files or run tools; "
    "translate only the text and context provided in this prompt."
)
_TALL_IMAGE_THRESHOLD = 2400
_TILE_OVERLAP = 200
type _ImageAttachment = tuple[Path, int, int, int, int]
_IMAGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "bbox": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "width": {"type": "number", "exclusiveMinimum": 0},
                            "height": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["x", "y", "width", "height"],
                        "additionalProperties": False,
                    },
                    "reading_order": {"type": "integer", "minimum": 0},
                    "source_language": {
                        "type": "string",
                        "enum": ["ja", "en", "ko", "zh-Hans", "zh-Hant"],
                    },
                    "source_text": {"type": "string"},
                    "translated_text": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": [
                    "bbox",
                    "reading_order",
                    "source_language",
                    "source_text",
                    "translated_text",
                    "note",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["blocks"],
    "additionalProperties": False,
}
_IMAGE_PROMPT = """Read every dialogue and narration block visible in this manga/comic crop.
Return exactly one block per distinct speech-bubble or narration container. Do not omit, invent,
split, or merge blocks. Transcribe source_text literally, preserving every character and exact
punctuation; do not infer, correct, or repair words from the scene or story. Each bbox must tightly
enclose all original source-text glyphs in that block with 4 pixels of padding on every side,
excluding the bubble border, tail, character art, and adjacent panels. Report every bbox in
crop-local pixel coordinates with crop origin (0, 0).
Preserve reading order
and identify each block's resolved source language. OCR the source text and translate it into
natural Thai using the supplied context. Do not inspect the filesystem or run tools; use only the
single attached crop and this prompt. Never return `auto` as source_language."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ImageBlock(_StrictModel):
    bbox: BoundingBox
    reading_order: int = Field(ge=0)
    source_language: SourceLanguage
    source_text: StrictStr
    translated_text: StrictStr
    note: StrictStr

    @field_validator("source_language")
    @classmethod
    def source_language_must_be_resolved(cls, value: SourceLanguage) -> SourceLanguage:
        if value is SourceLanguage.AUTO:
            raise ValueError("source_language must be resolved")
        return value


class _ImageResponse(_StrictModel):
    blocks: list[_ImageBlock]


class CodexCliTranslationProvider:
    def __init__(
        self,
        configuration: ProviderConfiguration,
        *,
        retry_delay: float = 0.25,
    ) -> None:
        self._configuration = configuration
        self._retry_delay = retry_delay

    async def translate_blocks(
        self,
        blocks: list[TranslationInput],
        context: TranslationContext,
    ) -> list[TranslationResult]:
        if not blocks:
            return []

        messages = build_messages(blocks, context, self._configuration.instructions)
        messages[0]["content"] = f"{messages[0]['content']}\n\n{_TRANSLATION_ONLY_INSTRUCTION}"
        prompt = json.dumps(
            messages,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        attempts = self._configuration.retry_count + 1
        last_error = "Codex CLI failed"
        for attempt in range(attempts):
            try:
                content = await self._run(prompt)
                return validate_translation_response(content, blocks)
            except FileNotFoundError as error:
                raise ProviderError(
                    "Codex CLI executable was not found",
                    recoverable=False,
                ) from error
            except OSError as error:
                raise ProviderError(
                    "Codex CLI could not be started",
                    recoverable=False,
                ) from error
            except ProviderError as error:
                last_error = str(error)
            except (ValueError, TypeError) as error:
                last_error = f"invalid Codex CLI response: {error}"
            if attempt + 1 < attempts and self._retry_delay:
                await asyncio.sleep(self._retry_delay)
        raise ProviderError(last_error, recoverable=True)

    async def translate_page_image(
        self,
        page: Page,
        image_path: str | Path,
        context: TranslationContext,
    ) -> list[TextBlock]:
        """OCR and translate one opted-in page image with Codex CLI."""
        if not self._configuration.uploads_images:
            raise ProviderError("Codex CLI image upload is not enabled", recoverable=False)
        resolved_image_path = Path(image_path).resolve()
        if not resolved_image_path.is_file():
            raise ProviderError("Codex CLI image file does not exist", recoverable=False)

        request = {
            "page": {
                "width": page.width,
                "height": page.height,
                "source_language": page.source_language,
            },
            "context": context.model_dump(mode="json"),
        }
        with tempfile.TemporaryDirectory(prefix="manga-codex-tiles-") as tile_directory:
            try:
                attachments = _prepare_image_attachments(
                    resolved_image_path,
                    page.height,
                    Path(tile_directory),
                )
            except OSError as error:
                raise ProviderError(
                    "Codex CLI image could not be tiled",
                    recoverable=False,
                ) from error
            blocks = []
            for attachment in attachments:
                blocks.extend(await self._translate_image_attachment(request, attachment, page))
            return normalize_reading_order(blocks)

    async def _translate_image_attachment(
        self,
        request: dict[str, object],
        attachment: _ImageAttachment,
        page: Page,
    ) -> list[TextBlock]:
        prompt = _build_image_prompt(request, attachment, self._configuration.instructions)
        attempts = self._configuration.retry_count + 1
        last_error = "invalid Codex CLI image response"
        for attempt in range(attempts):
            try:
                content = await self._run(
                    prompt,
                    schema=_IMAGE_OUTPUT_SCHEMA,
                    image_paths=[attachment[0]],
                )
                return self._validate_image_response(content, page, attachment)
            except FileNotFoundError as error:
                raise ProviderError(
                    "Codex CLI executable was not found",
                    recoverable=False,
                ) from error
            except OSError as error:
                raise ProviderError(
                    "Codex CLI could not be started",
                    recoverable=False,
                ) from error
            except ProviderError as error:
                last_error = str(error)
            except (ValidationError, ValueError, TypeError) as error:
                last_error = f"invalid Codex CLI image response: {error}"
            if attempt + 1 < attempts and self._retry_delay:
                await asyncio.sleep(self._retry_delay)
        raise ProviderError(last_error, recoverable=True)

    @staticmethod
    def _validate_image_response(
        content: str,
        page: Page,
        attachment: _ImageAttachment,
    ) -> list[TextBlock]:
        response = _ImageResponse.model_validate_json(content)
        _, crop_top, crop_bottom, core_top, core_bottom = attachment
        local_core_top = core_top - crop_top
        local_core_bottom = core_bottom - crop_top
        blocks = []
        for item in response.blocks:
            if not item.source_text and item.translated_text:
                raise ValueError("empty source block received a non-empty translation")
            local_bbox = clamp_bbox(item.bbox, page.width, crop_bottom - crop_top)
            center_y = local_bbox.y + local_bbox.height / 2
            if not local_core_top <= center_y < local_core_bottom:
                continue
            page_bbox = clamp_bbox(
                local_bbox.model_copy(update={"y": local_bbox.y + crop_top}),
                page.width,
                page.height,
            )
            blocks.append(
                TextBlock(
                    page_id=page.id,
                    bbox=page_bbox,
                    reading_order=item.reading_order,
                    source_language=item.source_language,
                    source_text=item.source_text,
                    translated_text=item.translated_text,
                    ocr_provider="codex-image",
                    note=item.note,
                    status=BlockStatus.TRANSLATED,
                )
            )
        return sorted(blocks, key=lambda block: block.reading_order)

    async def _run(
        self,
        prompt: str,
        *,
        schema: dict[str, object] = _OUTPUT_SCHEMA,
        image_paths: Sequence[Path] = (),
    ) -> str:
        with tempfile.TemporaryDirectory(prefix="manga-codex-schema-") as directory:
            schema_path = Path(directory, "translation-response.schema.json")
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
            ]
            model = self._configuration.model.strip()
            if model and model != "default":
                command.extend(("--model", model))
            for image_path in image_paths:
                command.extend(("--image", str(image_path)))
            command.append("-")
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=directory,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode()),
                    timeout=self._configuration.timeout_seconds,
                )
            except TimeoutError:
                await _stop_process(process)
                raise ProviderError("Codex CLI timed out", recoverable=True) from None
            except asyncio.CancelledError:
                await _stop_process(process)
                raise

            if process.returncode:
                raise ProviderError(
                    f"Codex CLI exited with status {process.returncode}",
                    recoverable=True,
                )
            try:
                return stdout.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ProviderError("Codex CLI returned invalid UTF-8", recoverable=True) from error


def _prepare_image_attachments(
    image_path: Path,
    image_height: int,
    tile_directory: Path,
) -> list[_ImageAttachment]:
    if image_height <= _TALL_IMAGE_THRESHOLD:
        return [(image_path, 0, image_height, 0, image_height)]

    tile_count = ceil(image_height / (_TALL_IMAGE_THRESHOLD - _TILE_OVERLAP))
    attachments = []
    with Image.open(image_path) as source:
        for index in range(tile_count):
            core_top = index * image_height // tile_count
            core_bottom = (index + 1) * image_height // tile_count
            crop_top = max(0, core_top - _TILE_OVERLAP // 2)
            crop_bottom = min(image_height, core_bottom + _TILE_OVERLAP // 2)
            tile_path = tile_directory / f"tile-{index + 1:03d}.png"
            source.crop((0, crop_top, source.width, crop_bottom)).save(tile_path, format="PNG")
            attachments.append((tile_path, crop_top, crop_bottom, core_top, core_bottom))
    return attachments


def _build_image_prompt(
    request: dict[str, object],
    attachment: _ImageAttachment,
    instructions: str,
) -> str:
    _, crop_top, _, core_top, core_bottom = attachment
    local_core_top = core_top - crop_top
    local_core_bottom = core_bottom - crop_top
    prompt = (
        f"{_IMAGE_PROMPT}\n\nReturn a block only when its crop-local bbox vertical center is "
        f"inside the half-open local core y range [{local_core_top},{local_core_bottom}). "
        "Content in the overlap outside that range is context only and must not be returned."
        f"\n\nRequest:\n{json.dumps(request, ensure_ascii=False)}"
    )
    if instructions:
        prompt = f"{prompt}\n\nAdditional translation instructions:\n{instructions}"
    return prompt


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=1)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
