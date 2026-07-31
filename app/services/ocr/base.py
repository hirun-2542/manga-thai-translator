"""OCR provider contracts."""

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.models import BoundingBox, SourceLanguage
from app.services.types import ImageInput


class OcrCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    supported_languages: frozenset[SourceLanguage]
    supports_vertical_text: bool
    supports_cpu: bool
    requires_network: bool
    uploads_images: bool

    @field_validator("supported_languages")
    @classmethod
    def languages_must_be_concrete(
        cls, value: frozenset[SourceLanguage]
    ) -> frozenset[SourceLanguage]:
        if not value:
            raise ValueError("supported_languages must not be empty")
        if SourceLanguage.AUTO in value:
            raise ValueError("supported_languages must not include auto")
        return value


class OcrResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)

    source_text: str
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    provider: str
    detected_language: SourceLanguage | None = None


class OcrProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> OcrCapabilities: ...

    async def recognize(
        self,
        image: ImageInput,
        bbox: BoundingBox,
        source_language: SourceLanguage,
    ) -> OcrResult: ...
