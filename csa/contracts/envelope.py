"""Inbound channel envelope models (LG02 §1)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base contract: reject fields that are not part of the versioned API."""

    model_config = ConfigDict(extra="forbid")


class Channel(StrEnum):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    WEB = "web"
    WEBHOOK = "webhook"
    VOICE = "voice"
    TIKTOK_SHOP = "tiktok_shop"
    SHOPEE = "shopee"
    MANUAL = "manual"


class Contact(ContractModel):
    id: UUID | None
    external_id: str = Field(min_length=1)
    channel: Channel
    display_name: str | None


class ConversationRef(ContractModel):
    id: UUID | None
    channel_thread: str = Field(min_length=1)
    turn_count: int = Field(ge=0)
    status: str = Field(min_length=1)


class Attachment(ContractModel):
    kind: Literal["image", "file", "audio", "video", "document", "other"]
    mime_type: str = Field(min_length=1)
    file_id: str | None = None
    filename: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class Voice(ContractModel):
    transcript: str | None = None
    duration_secs: float | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)


class Message(ContractModel):
    body_raw: str
    body_redacted: str
    language: str = Field(min_length=2, max_length=16)
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)
    voice: Voice | None = None


class Callback(ContractModel):
    action_id: UUID


class Meta(ContractModel):
    is_first_contact: bool = False
    business_hours: bool = True
    pii_map: dict[str, str] = Field(default_factory=dict)
    turn_deadline_ms: int = Field(default=90_000, gt=0)


class Envelope(ContractModel):
    schema_version: Literal["3.0"] = "3.0"
    trace_id: UUID
    received_at: datetime
    channel: Channel
    idempotency_key: str = Field(min_length=1, max_length=512)
    client_id: UUID
    contact: Contact
    conversation: ConversationRef
    message: Message
    callback: Callback | None = None
    meta: Meta
