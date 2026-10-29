"""CSAV2 human-session contracts; no browser-supplied identity authority."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

# The B2 preset ids (csa.hr.access_switches.PresetId), spelled out so the contracts module stays
# free of HR imports; tests/test_console_identity.py pins the two lists together.
HrPreset = Literal["staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"]


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    TENANT_STAFF = "tenant_staff"
    READ_ONLY = "read_only"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MembershipOut(Contract):
    client_id: UUID
    role: Literal["tenant_admin", "tenant_staff", "read_only"]


class UserOut(Contract):
    id: UUID
    email: str
    display_name: str
    is_platform_admin: bool
    must_change_password: bool


class MeOut(Contract):
    user: UserOut
    memberships: list[MembershipOut]
    active_client_id: UUID | None
    role: Role | None
    csrf_token: str
    # WORKSPACE_ASSISTANT_ENABLED: the console renders the assistant dock only when true.
    workspace_assistant: bool = False


class ConsoleActor(Contract):
    user_id: UUID
    email: str
    display_name: str
    platform_admin: bool
    must_change_password: bool
    session_id: UUID
    client_id: UUID | None
    role: Role | None
    memberships: list[MembershipOut]
    csrf_hash: str = Field(exclude=True, repr=False)

    def me(self, csrf_token: str, *, workspace_assistant: bool = False) -> MeOut:
        return MeOut(
            user=UserOut(
                id=self.user_id,
                email=self.email,
                display_name=self.display_name,
                is_platform_admin=self.platform_admin,
                must_change_password=self.must_change_password,
            ),
            memberships=self.memberships,
            active_client_id=self.client_id,
            role=self.role,
            csrf_token=csrf_token,
            workspace_assistant=workspace_assistant,
        )


class EmailInput(Contract):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("invalid email")
        return value


class LoginIn(EmailInput):
    password: SecretStr = Field(min_length=1, max_length=1024)


class PasswordChangeIn(Contract):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class TokenIn(Contract):
    token: SecretStr = Field(min_length=32, max_length=256)


class TokenAcceptIn(TokenIn):
    password: SecretStr = Field(min_length=12, max_length=1024)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


class TokenInfoOut(Contract):
    email: str
    display_name: str
    role: str | None
    client_id: UUID | None
    expires_at: datetime
    kind: Literal["invite", "reset"]


class InviteIn(EmailInput):
    client_id: UUID
    role: Literal["tenant_admin", "tenant_staff", "read_only"]
    display_name: str = Field(min_length=1, max_length=120)
    hr_preset: HrPreset | None = None


class IssuedTokenOut(Contract):
    id: UUID
    # None when the link was emailed: the raw token then never leaves the server.
    token: str | None
    expires_at: datetime
    delivery: Literal["manual", "email"] = "manual"


class MemberPatchIn(Contract):
    role: Literal["tenant_admin", "tenant_staff", "read_only"] | None = None
    is_enabled: bool | None = None


class UserPatchIn(Contract):
    is_enabled: bool


class MemberOut(Contract):
    user_id: UUID
    client_id: UUID
    email: str
    display_name: str
    role: str
    is_enabled: bool


class AuditOut(Contract):
    id: UUID
    actor_id: UUID | None
    client_id: UUID | None
    action: str
    target_id: str
    details: dict[str, object]
    created_at: datetime


class AdminUserOut(UserOut):
    is_enabled: bool


class InvitationOut(Contract):
    id: UUID
    user_id: UUID
    client_id: UUID | None
    email: str
    display_name: str
    role: str | None
    kind: str
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    hr_preset: HrPreset | None = None
