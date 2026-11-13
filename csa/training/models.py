"""Request and response shapes of the training library routes."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

ViewMode = Literal["slides", "document"]
Kind = Literal["pdf", "video"]
VideoStatus = Literal["uploading", "uploaded", "transcribing", "summarising", "ready", "failed"]
Stage = Literal["transcribing", "summarising"]

TITLE_MAX_CHARS = 120
DESCRIPTION_MAX_CHARS = 1000
CATEGORY_MAX_CHARS = 60


def _one_line(value: str) -> str:
    """Titles and categories are shown on one line: no control characters at all."""
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise ValueError("control characters are not allowed")
    return value


def _text(value: str) -> str:
    """Descriptions may hold line breaks and tabs, nothing else from the control range."""
    if any(unicodedata.category(ch) == "Cc" and ch not in "\n\t" for ch in value):
        raise ValueError("control characters are not allowed")
    return value


# Whitespace is stripped before the length is checked, so a blank title is refused.
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_CHARS),
    AfterValidator(_one_line),
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_CHARS),
    AfterValidator(_text),
]
Category = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=CATEGORY_MAX_CHARS),
    AfterValidator(_one_line),
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Processing(_Model):
    """A video's progress, for managers; ``waiting`` says the daily processing limit is used."""

    stage: Stage
    chunks_done: int
    chunks_total: int | None
    waiting: bool


class ItemOut(_Model):
    """One library item. A PDF has a view mode and a page count; a video has a duration instead.

    ``failure``, ``processing`` and ``notice`` are for managers; readers always get ``None``.
    ``status`` is ``ready`` for every PDF.
    """

    id: UUID
    kind: Kind
    title: str
    description: str
    category: str
    view_mode: ViewMode | None
    page_count: int | None
    byte_size: int
    created_at: AwareDatetime
    uploaded_by_name: str | None
    has_summary: bool
    status: VideoStatus
    duration_ms: int | None
    failure: str | None
    processing: Processing | None
    notice: Literal["hevc"] | None


class VideoStart(_Model):
    """Starting a video upload: the file's name (data only), its size and its media type."""

    file_name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    byte_size: Annotated[int, Field(ge=1)]
    media_type: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class VideoUploadOut(_Model):
    """An upload's plan: send pieces ``1..piece_count`` of ``piece_size`` bytes (the last one the
    remainder); ``received`` lists the numbers already stored, for resuming."""

    item: ItemOut
    piece_size: int
    piece_count: int
    received: list[int]


class PieceOut(_Model):
    """A stored piece and how many pieces of the upload are stored now."""

    number: int
    received: int


class ItemList(_Model):
    items: list[ItemOut]
    categories: list[str]
    can_manage: bool


class ItemPatch(_Model):
    """The fields to change; a field left out (or null) keeps its value."""

    title: Title | None = None
    description: Description | None = None
    category: Category | None = None
    view_mode: ViewMode | None = None

    @model_validator(mode="after")
    def _some_change(self) -> Self:
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("a change is required")
        return self


class SummaryRequest(_Model):
    """Asking for an item's summary; ``regenerate`` replaces a stored one and needs manage."""

    regenerate: bool = False


class SummaryOut(_Model):
    summary: str
    key_points: list[str]
    truncated: bool
    model: str
    summarised_at: AwareDatetime


class NoSummary(_Model):
    """No summary has been made for the item yet."""

    summary: None = None
