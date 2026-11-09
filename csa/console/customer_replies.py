"""Go live: a company's brand name and customer replies, set by its tenant admin."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from csa.console.contracts import ConsoleActor, Role
from csa.console.packages import PACKAGES
from csa.core import db
from csa.core.tenancy import tx
from csa.ops.runbooks import set_deployment_mode

Mode = Literal["off", "approve", "auto"]

_TO_DEPLOYMENT: dict[str, str] = {"off": "shadow", "approve": "suggest", "auto": "auto"}
_TO_MODE: dict[str, Mode] = {"shadow": "off", "suggest": "approve", "auto": "auto"}
# A company with no policy behaves as the turn gate's Policy default.
_UNSET_DEPLOYMENT = "auto"
_ADDONS = frozenset(p.key for p in PACKAGES if p.kind == "addon")
MAX_BRAND_NAME = 120
MAX_PERSONA_NAME = 60
MAX_BRAND_TONE = 200
MAX_INTRO_LINE = 300
# The customer bot's voice: wording only, on top of the default guide it can never turn off.
_PERSONA_FIELDS = ("persona_name", "brand_tone", "intro_line")
# The longest the gate's Policy allows: a small business reviews drafts the next day.
APPROVAL_TTL_MINUTES = 1440


class CustomerRepliesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode
    brand_name: str | None
    persona_name: str | None
    brand_tone: str | None
    intro_line: str | None
    can_manage: bool
    warnings: list[str]


class CustomerRepliesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode
    brand_name: str | None = Field(default=None)
    # Left out or null: unchanged. An empty string: back to the default.
    persona_name: str | None = Field(default=None)
    brand_tone: str | None = Field(default=None)
    intro_line: str | None = Field(default=None)

    @field_validator("brand_name")
    @classmethod
    def clean_brand_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _one_line(value, MAX_BRAND_NAME, "brand name") or None

    @field_validator("persona_name")
    @classmethod
    def clean_persona_name(cls, value: str | None) -> str | None:
        return None if value is None else _one_line(value, MAX_PERSONA_NAME, "persona name")

    @field_validator("brand_tone")
    @classmethod
    def clean_brand_tone(cls, value: str | None) -> str | None:
        return None if value is None else _one_line(value, MAX_BRAND_TONE, "tone")

    @field_validator("intro_line")
    @classmethod
    def clean_intro_line(cls, value: str | None) -> str | None:
        return None if value is None else _one_line(value, MAX_INTRO_LINE, "greeting")


def _one_line(value: str, limit: int, what: str) -> str:
    """These go into the bot's prompt, so no line breaks or control characters."""
    cleaned = value.replace("\u00a0", " ").strip()
    if len(cleaned) > limit or any(not c.isprintable() for c in cleaned):
        raise ValueError(f"{what} must be a single line of at most {limit} characters")
    return cleaned


def _persona_of(row: dict[str, Any]) -> dict[str, str | None]:
    return {name: row[name] or None for name in _PERSONA_FIELDS}


def _mode(row: dict[str, Any]) -> Mode:
    policy = row["policy"] or {}
    if not isinstance(policy, dict):
        return "off"
    # Only a hand edit reaches "off" here; it reads as off so a save can overwrite it.
    return _TO_MODE.get(policy.get("deployment_mode", _UNSET_DEPLOYMENT), "off")


def _can_manage(actor: ConsoleActor, client_id: UUID) -> bool:
    return actor.platform_admin or (
        actor.client_id == client_id and actor.role == Role.TENANT_ADMIN
    )


async def _active_packages(conn: AsyncConnection[Any], client_id: UUID) -> dict[str, str]:
    rows = await db.q.fetch_all("console/service_packages_list.sql", (client_id,), conn=conn)
    return {row["package"]: row["status"] for row in rows}


async def _warnings(conn: AsyncConnection[Any], client_id: UUID, mode: Mode) -> list[str]:
    statuses = await _active_packages(conn, client_id)
    active = {key for key, status in statuses.items() if status == "active"}
    # A suspended company can't turn replies on, so telling it to would be a dead end.
    warnings = ["replies_off"] if mode == "off" and "customer_support" in active else []
    if "knowledge_base" in active:
        found = await db.q.fetch_one(
            "console/customer_replies_has_knowledge.sql", (client_id, client_id), conn=conn
        )
        if not (found and found["has_knowledge"]):
            warnings.append("no_knowledge")
    if not active & _ADDONS:
        warnings.append("base_only")
    return warnings


async def promote_to_pilot(conn: AsyncConnection[Any], client_id: UUID, actor_id: UUID) -> bool:
    """Background jobs skip ``onboarding``, so files wouldn't process before going live."""
    row = await db.q.fetch_one("console/client_status_promote.sql", (client_id,), conn=conn)
    if row is None:
        return False
    await db.q.execute(
        "console/audit.sql",
        (
            actor_id,
            client_id,
            "tenant.status_changed",
            str(client_id),
            Jsonb({"before": "onboarding", "after": "pilot"}),
        ),
        conn=conn,
    )
    return True


async def read_replies(
    pool: AsyncConnectionPool, client_id: UUID, actor: ConsoleActor
) -> CustomerRepliesOut:
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("console/customer_replies_get.sql", (client_id,), conn=conn)
        if row is None:
            raise HTTPException(404, "tenant not found")
        mode = _mode(row)
        warnings = await _warnings(conn, client_id, mode)
    return CustomerRepliesOut(
        mode=mode,
        brand_name=row["brand_name"],
        **_persona_of(row),
        can_manage=_can_manage(actor, client_id),
        warnings=warnings,
    )


async def save_replies(
    pool: AsyncConnectionPool, client_id: UUID, body: CustomerRepliesIn, actor: ConsoleActor
) -> CustomerRepliesOut:
    if not _can_manage(actor, client_id):
        raise HTTPException(403, "tenant administrator required")
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("console/customer_replies_lock.sql", (client_id,), conn=conn)
        if row is None:
            raise HTTPException(404, "tenant not found")
        statuses = await _active_packages(conn, client_id)
        if statuses.get("customer_support") != "active":
            raise HTTPException(409, "package_suspended")
        before_mode, before_brand = _mode(row), row["brand_name"]
        brand = body.brand_name if body.brand_name is not None else before_brand
        if body.mode != "off" and not brand:
            raise HTTPException(422, "brand_name_required")
        before_persona = _persona_of(row)
        after_persona = {
            name: before_persona[name]
            if getattr(body, name) is None
            else getattr(body, name) or None
            for name in _PERSONA_FIELDS
        }
        core_changed = (body.mode, brand) != (before_mode, before_brand)
        persona_changed = after_persona != before_persona
        await promote_to_pilot(conn, client_id, actor.user_id)
        if core_changed:
            await set_deployment_mode(conn, client_id, _TO_DEPLOYMENT[body.mode])
            if body.mode == "approve":
                await db.q.execute(
                    "console/customer_replies_ttl_set.sql",
                    (APPROVAL_TTL_MINUTES, client_id),
                    conn=conn,
                )
            await db.q.execute(
                "console/customer_replies_brand_set.sql", (brand, client_id), conn=conn
            )
        if persona_changed:
            persona = {
                key: after_persona[name]
                for key, name in (("name", "persona_name"), ("intro_line", "intro_line"))
                if after_persona[name]
            }
            await db.q.execute(
                "console/customer_replies_persona_set.sql",
                (after_persona["brand_tone"], Jsonb(persona), client_id),
                conn=conn,
            )
        if core_changed or persona_changed:
            before = {"mode": before_mode, "brand_name": before_brand}
            after = {"mode": body.mode, "brand_name": brand}
            if persona_changed:
                before |= before_persona
                after |= after_persona
            await db.q.execute(
                "console/audit.sql",
                (
                    actor.user_id,
                    client_id,
                    "tenant.customer_replies_set",
                    str(client_id),
                    Jsonb({"before": before, "after": after}),
                ),
                conn=conn,
            )
    return await read_replies(pool, client_id, actor)
