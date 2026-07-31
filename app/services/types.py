"""Shared provider input types."""

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ImageInput(BaseModel):
    """Image metadata; providers must not modify the referenced source file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
