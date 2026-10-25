"""Typed, sandbox-only voice session and finalized-utterance boundaries."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class VoiceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class CreateVoiceSession(VoiceContract):
    request_key: UUID
    conversation_id: UUID
    language: Literal["en", "ms"] = "en"
    consent: Literal[True]

    @field_validator("consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("explicit voice consent is required")
        return value


class VoiceSession(VoiceContract):
    id: UUID
    conversation_id: UUID
    request_key: UUID
    profile: Literal["sandbox"]
    language: Literal["en", "ms"]
    status: Literal["active", "ended", "expired"]
    created_at: datetime
    expires_at: datetime


class VoiceSessionRecord(VoiceSession):
    client_id: UUID
    user_id: UUID
    login_session_id: UUID


class FinalUtterance(VoiceContract):
    client_id: UUID
    transcript: str = Field(min_length=1, max_length=4000, repr=False)
    final: Literal[True]

    @field_validator("final", mode="before")
    @classmethod
    def finalized(cls, value):
        if value is not True:
            raise ValueError("only finalized utterances can be submitted")
        return value

    @field_validator("transcript")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("transcript must not be blank")
        return value


class VoiceReceipt(VoiceContract):
    session_id: UUID
    utterance_id: UUID
    request_key: UUID
    turn_id: int
    trace_id: UUID
    duplicate: bool = False


class MediaSessionLookup(VoiceContract):
    client_id: UUID


class VoiceReceiptRecord(VoiceReceipt):
    client_id: UUID
    user_id: UUID
    conversation_id: UUID
    fingerprint: str
    created_at: datetime


class VoiceConnection(VoiceContract):
    server_url: str
    room: str
    participant_identity: str
    token: str = Field(repr=False)
    expires_at: datetime


class VoiceSessionConfig(VoiceContract):
    server_url: str
    api_key: SecretStr
    api_secret: SecretStr = Field(min_length=32)
    media_secret: SecretStr = Field(min_length=32)
    session_idle_seconds: int = Field(default=1800, ge=60, le=86400)


class VoiceAuthority(VoiceContract):
    expires_at: datetime | None
