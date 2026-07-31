"""Validated domain models for Manga Thai Translator."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

type NonEmptyString = Annotated[str, Field(min_length=1)]
type NonNegativeInt = Annotated[int, Field(ge=0)]
type PositiveInt = Annotated[int, Field(gt=0)]
type Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


def utc_now() -> datetime:
    return datetime.now(UTC)


class SourceLanguage(StrEnum):
    JA = "ja"
    EN = "en"
    KO = "ko"
    ZH_HANS = "zh-Hans"
    ZH_HANT = "zh-Hant"
    AUTO = "auto"


class ReadingOrderPreset(StrEnum):
    MANGA_RTL = "manga_rtl"
    COMIC_LTR = "comic_ltr"
    WEBTOON_VERTICAL = "webtoon_vertical"
    CUSTOM = "custom"


class WritingMode(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class BlockStatus(StrEnum):
    DETECTED = "detected"
    LANGUAGE_REVIEW_REQUIRED = "language_review_required"
    OCR_COMPLETE = "ocr_complete"
    OCR_REVIEWED = "ocr_reviewed"
    TRANSLATED = "translated"
    TRANSLATION_REVIEWED = "translation_reviewed"
    ERROR = "error"


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value.astimezone(UTC)


class BoundingBox(DomainModel):
    x: float
    y: float
    width: Annotated[float, Field(gt=0)]
    height: Annotated[float, Field(gt=0)]


class TextBlock(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    page_id: UUID
    bbox: BoundingBox
    reading_order: NonNegativeInt
    source_language: SourceLanguage | None = None
    writing_mode: WritingMode = WritingMode.HORIZONTAL
    source_text: str = ""
    translated_text: str = ""
    ocr_confidence: Confidence | None = None
    ocr_provider: str | None = None
    speaker: str = ""
    note: str = ""
    status: BlockStatus = BlockStatus.DETECTED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _created_at_utc = field_validator("created_at")(_as_utc)
    _updated_at_utc = field_validator("updated_at")(_as_utc)

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "TextBlock":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class Page(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    source_path: NonEmptyString
    width: PositiveInt
    height: PositiveInt
    source_language: SourceLanguage | None = None
    reading_order: ReadingOrderPreset | None = None
    blocks: list[TextBlock] = Field(default_factory=list)


class ProjectSettings(DomainModel):
    default_source_language: SourceLanguage = SourceLanguage.AUTO
    default_reading_order: ReadingOrderPreset = ReadingOrderPreset.MANGA_RTL
    target_language: NonEmptyString = "th"


class Character(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    name: NonEmptyString
    aliases: list[str] = Field(default_factory=list)
    gender: str = ""
    approximate_age: str = ""
    personality: str = ""
    relationships: str = ""
    first_person_pronoun: str = ""
    sentence_endings: str = ""
    note: str = ""


class GlossaryEntry(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    source_term: NonEmptyString
    target_term: str
    source_language: SourceLanguage | None = None
    note: str = ""


class TranslationContext(DomainModel):
    default_source_language: SourceLanguage
    target_language: NonEmptyString = "th"
    characters: list[Character] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    previous_summary: str = ""
    translation_note: str = ""


class TranslationInput(DomainModel):
    id: UUID
    source_language: SourceLanguage
    source_text: str
    reading_order: NonNegativeInt = 0

    @field_validator("source_language")
    @classmethod
    def source_language_must_be_resolved(cls, value: SourceLanguage) -> SourceLanguage:
        if value is SourceLanguage.AUTO:
            raise ValueError("source_language must be resolved before translation")
        return value


class TranslationResult(DomainModel):
    id: UUID
    translated_text: str
    note: str = ""


class ProviderConfiguration(DomainModel):
    provider: NonEmptyString
    base_url: str | None = None
    model: NonEmptyString
    api_key_env: Annotated[str | None, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")] = None
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.2
    timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    retry_count: NonNegativeInt = 2
    instructions: str = ""
    uploads_images: bool = False


class Project(DomainModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    name: NonEmptyString
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    pages: list[Page] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    previous_summary: str = ""
    translation_note: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _created_at_utc = field_validator("created_at")(_as_utc)
    _updated_at_utc = field_validator("updated_at")(_as_utc)

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "Project":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self
