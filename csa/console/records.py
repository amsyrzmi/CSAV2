"""Validated identity persistence records; secrets never appear in model reprs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from csa.hr.access_switches import PresetId

TenantRole = Literal["tenant_admin", "tenant_staff", "read_only"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserRecord(Record):
    id: UUID
    email: str
    display_name: str
    password_hash: str | None = Field(repr=False)
    is_platform_admin: bool
    is_enabled: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime


class SessionRecord(Record):
    session_id: UUID
    user_id: UUID
    client_id: UUID | None
    csrf_hash: str = Field(repr=False)
    expires_at: datetime
    last_seen_at: datetime
    email: str
    display_name: str
    is_platform_admin: bool
    is_enabled: bool
    must_change_password: bool


class MembershipRecord(Record):
    user_id: UUID
    client_id: UUID
    role: TenantRole
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class TokenRecord(Record):
    id: UUID
    user_id: UUID
    client_id: UUID | None
    role: TenantRole | None
    kind: Literal["invite", "reset"]
    token_hash: str = Field(repr=False)
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None
    created_by: UUID
    created_at: datetime
    hr_preset: PresetId | None
    hr_preset_chosen_by: UUID | None


class TokenDetails(TokenRecord):
    email: str
    display_name: str
    password_hash: str | None = Field(repr=False)
    is_enabled: bool
    is_platform_admin: bool
