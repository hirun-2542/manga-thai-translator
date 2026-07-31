"""Shared request serialization and response validation for translation providers."""

import json
from collections import Counter
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from app.core.models import TranslationContext, TranslationInput, TranslationResult

SYSTEM_PROMPT = """คุณคือนักแปลมังงะ คอมิก และเว็บตูนเป็นภาษาไทย

กฎสำคัญ:
- ตอบเป็น JSON ตาม Schema ที่กำหนดเท่านั้น
- แปลแต่ละ Block ตาม source_language และตามลำดับที่ได้รับ
- ห้ามเพิ่ม ลบ รวม แยก หรือสลับ Text Block และต้องใช้ ID เดิม
- รักษาความหมาย อารมณ์ บุคลิกตัวละคร และศัพท์ตาม Glossary
- ห้ามแต่งข้อความที่ไม่มีในต้นฉบับ
- source_text ว่างต้องคืน translated_text ว่าง
- ห้ามอธิบายนอก JSON

รูปแบบผลลัพธ์:
{"translations":[{"id":"ID เดิม","translated_text":"คำแปลภาษาไทย","note":""}]}"""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _TranslationItem(_StrictModel):
    id: UUID
    translated_text: StrictStr
    note: StrictStr = ""


class _TranslationResponse(_StrictModel):
    translations: Annotated[list[_TranslationItem], Field()]


def build_messages(
    blocks: list[TranslationInput],
    context: TranslationContext,
    instructions: str = "",
) -> list[dict[str, str]]:
    """Build deterministic text-only chat messages."""
    system = SYSTEM_PROMPT
    if instructions:
        system = f"{system}\n\nคำสั่งเพิ่มเติม:\n{instructions}"
    request = {
        "context": context.model_dump(mode="json"),
        "blocks": [
            {
                "id": str(block.id),
                "source_language": block.source_language.value,
                "source_text": block.source_text,
                "reading_order": block.reading_order,
            }
            for block in blocks
        ],
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    ]


def validate_translation_response(
    content: str,
    blocks: list[TranslationInput],
) -> list[TranslationResult]:
    """Parse and validate a complete assistant response in input order."""
    try:
        response = _TranslationResponse.model_validate_json(content)
    except ValidationError as error:
        raise ValueError(f"invalid translation JSON: {error}") from error

    expected_ids = [block.id for block in blocks]
    result_ids = [item.id for item in response.translations]
    counts = Counter(result_ids)
    expected = set(expected_ids)
    actual = set(result_ids)
    missing = expected - actual
    duplicate = {item_id for item_id, count in counts.items() if count > 1}
    unknown = actual - expected
    if len(response.translations) != len(blocks) or missing or duplicate or unknown:
        raise ValueError(
            "invalid translation IDs "
            f"(missing={sorted(map(str, missing))}, "
            f"duplicate={sorted(map(str, duplicate))}, "
            f"unknown={sorted(map(str, unknown))})"
        )

    by_id = {item.id: item for item in response.translations}
    for block in blocks:
        if not block.source_text and by_id[block.id].translated_text:
            raise ValueError(f"empty source block {block.id} received a non-empty translation")

    return [
        TranslationResult(
            id=block.id,
            translated_text=by_id[block.id].translated_text,
            note=by_id[block.id].note,
        )
        for block in blocks
    ]
