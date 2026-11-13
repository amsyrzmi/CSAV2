"""Payment settings and payment methods: tenant-tx reads and writes, one audit row per change.

Every function runs inside the caller's tenant transaction (`tx(pool, client_id)`). Refusals are
`HTTPException`s with a short code: 404 `method_not_found`; 409 `version_conflict`,
`no_active_method` and `last_active_method`; 422 `invalid_details`. Settings and methods share one
transaction-long lock per company, so "switch payments on" and "turn the last method off" cannot
both succeed on the strength of each other's old state. A method is never deleted, only turned off.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.core import db
from csa.payments.contracts import (
    MethodIn,
    MethodOut,
    MethodPatch,
    PaymentSettingsIn,
    PaymentSettingsOut,
    validate_details,
)

Conn = AsyncConnection[Any]

# What a company that has never saved its settings gets: switched off, the table's own defaults.
DEFAULT_SETTINGS = PaymentSettingsOut(
    enabled=False, lead_days=7, reminder_days=[-7, 0, 3, 7], suspend_after_days=None, version=0
)

# Methods a customer can be asked to pay with. Cash needs a booking, so it does not count as a way
# to switch payments on.
PAYABLE_KINDS = frozenset({"qr", "transfer"})


async def lock(conn: Conn, client_id: UUID) -> None:
    """Hold the company's payments-setup lock until the transaction ends."""
    await db.q.execute(
        "payments/advisory_lock.sql", {"key": f"payments:config:{client_id}"}, conn=conn
    )


async def get_settings(conn: Conn, client_id: UUID) -> PaymentSettingsOut:
    """The saved settings; no row yet reads as the default, switched off, version 0."""
    row = await db.q.fetch_one("payments/settings_get.sql", {"client_id": client_id}, conn=conn)
    if row is None:
        return DEFAULT_SETTINGS.model_copy(deep=True)
    return PaymentSettingsOut.model_validate(row)


def has_payable_method(methods: list[MethodOut]) -> bool:
    """Whether customers can be given a way to pay: an active QR or transfer method."""
    return any(m.is_active and m.kind in PAYABLE_KINDS for m in methods)


async def put_settings(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: PaymentSettingsIn
) -> PaymentSettingsOut:
    """Replace the settings. The first save makes the row; a save from an older read is 409.

    Switching on needs an active QR or transfer method (409 `no_active_method`).
    """
    await lock(conn, client_id)
    saved = await get_settings(conn, client_id)
    if saved.version != data.version:
        raise HTTPException(409, "version_conflict")
    if data.enabled and not has_payable_method(await list_methods(conn, client_id)):
        raise HTTPException(409, "no_active_method")
    await db.q.fetch_one(
        "payments/settings_upsert.sql",
        {
            "client_id": client_id,
            "enabled": data.enabled,
            "lead_days": data.lead_days,
            "reminder_days": data.reminder_days,
            "suspend_after_days": data.suspend_after_days,
        },
        conn=conn,
    )
    await audit(conn, actor, client_id, "payments.settings.updated", str(client_id))
    return await get_settings(conn, client_id)


async def list_methods(
    conn: Conn, client_id: UUID, *, active_only: bool = False
) -> list[MethodOut]:
    """The company's methods by sort order, then label; only the active ones when asked."""
    rows = await db.q.fetch_all(
        "payments/methods_list.sql",
        {"client_id": client_id, "active_only": active_only},
        conn=conn,
    )
    return [MethodOut.model_validate(row) for row in rows]


async def create_method(
    conn: Conn, client_id: UUID, actor: ConsoleActor, data: MethodIn
) -> MethodOut:
    """Add a method. Adding one can never leave an enabled company without a way to pay."""
    await lock(conn, client_id)
    row = await db.q.fetch_one(
        "payments/method_insert.sql",
        {"client_id": client_id, **data.model_dump(), "details": Jsonb(data.details)},
        conn=conn,
    )
    if row is None:
        raise RuntimeError("method_insert.sql returned no row")
    method = MethodOut.model_validate(row)
    await audit(conn, actor, client_id, "payments.method.created", str(method.id))
    return method


async def update_method(
    conn: Conn, client_id: UUID, actor: ConsoleActor, method_id: UUID, data: MethodPatch
) -> MethodOut:
    """Change a method.

    Turning off the last active QR or transfer method of an enabled company is 409
    `last_active_method`; a change from an older read is 409 `version_conflict`.
    """
    await lock(conn, client_id)
    current = await db.q.fetch_one(
        "payments/method_get.sql", {"client_id": client_id, "method_id": method_id}, conn=conn
    )
    if current is None:
        raise HTTPException(404, "method_not_found")
    if current["version"] != data.version:
        raise HTTPException(409, "version_conflict")
    details = None
    if data.details is not None:
        try:
            details = validate_details(current["kind"], data.details)
        except ValidationError as exc:
            raise HTTPException(422, "invalid_details") from exc
    if data.is_active is False and current["is_active"]:
        await ensure_another_payable_method(conn, client_id, method_id)
    row = await db.q.fetch_one(
        "payments/method_update.sql",
        {
            "client_id": client_id,
            "method_id": method_id,
            "version": data.version,
            "label": data.label,
            "details": Jsonb(details) if details is not None else None,
            "instructions": data.instructions,
            "is_active": data.is_active,
            "sort_order": data.sort_order,
        },
        conn=conn,
    )
    if row is None:
        raise HTTPException(409, "version_conflict")
    await audit(conn, actor, client_id, "payments.method.updated", str(method_id))
    return MethodOut.model_validate(row)


async def ensure_another_payable_method(conn: Conn, client_id: UUID, method_id: UUID) -> None:
    """Refuse (409 `last_active_method`) when turning `method_id` off leaves payments on and bare.

    Bare means no other active QR or transfer method.
    """
    if not (await get_settings(conn, client_id)).enabled:
        return
    others = [m for m in await list_methods(conn, client_id) if m.id != method_id]
    if not has_payable_method(others):
        raise HTTPException(409, "last_active_method")
