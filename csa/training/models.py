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
    StringConstraints,
    model_validator,
)

ViewMode = Literal["slides", "document"]

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


class ItemOut(_Model):
    id: UUID
    title: str
    description: str
    category: str
    view_mode: ViewMode
    page_count: int
    byte_size: int
    created_at: AwareDatetime
    uploaded_by_name: str | None
    has_summary: bool


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
