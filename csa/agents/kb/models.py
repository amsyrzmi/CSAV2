"""Data contracts for Knowledge Base agent (LG13)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KbInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["query", "ingest", "delete", "list_gaps", "resolve_gap"]

    # Query fields
    query: str | None = Field(default=None, max_length=1000)
    threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    limit: int = Field(default=3, ge=1, le=50)

    # Ingest fields
    title: str | None = Field(default=None, max_length=255)
    content: str | None = None
    category: str = Field(default="general", max_length=64)
    language: str = Field(default="en", max_length=10)
    source_uri: str | None = Field(default=None, max_length=500)

    # Delete / Management fields
    document_id: UUID | None = None
    gap_id: UUID | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> KbInput:
        if self.action == "query":
            if not self.query or not self.query.strip():
                raise ValueError("query is required for query action")
        elif self.action == "ingest":
            if not self.title or not self.title.strip():
                raise ValueError("title is required for ingest action")
            if not self.content or not self.content.strip():
                raise ValueError("content is required for ingest action")
        elif self.action == "delete":
            if self.document_id is None:
                raise ValueError("document_id is required for delete action")
        elif self.action == "resolve_gap":
            if self.gap_id is None:
                raise ValueError("gap_id is required for resolve_gap action")
        return self


class KbChunkMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    heading: str | None = None
    title: str
    source_uri: str | None = None
    similarity: float


class KbGapItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    query: str
    hits: int
    first_seen: str
    last_seen: str
    resolved: bool
