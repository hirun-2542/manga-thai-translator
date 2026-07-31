"""Text detection contracts."""

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import BoundingBox
from app.services.types import ImageInput


class DetectedRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)

    bbox: BoundingBox
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class TextDetectionProvider(Protocol):
    async def detect(self, image: ImageInput) -> list[DetectedRegion]: ...
