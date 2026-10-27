"""Console routes to connect and disconnect a tenant's WhatsApp number."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import Field, SecretStr

from csa.console.access import authenticate, tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.kb_uploads import audit
from csa.console.services import package_gate
from csa.core import crypto, db
from csa.core.settings import get_settings
from csa.core.tenancy import tx

DIGITS = r"^[0-9]+$"


class WhatsAppNumberIn(Contract):
    phone_number_id: str = Field(min_length=5, max_length=32, pattern=DIGITS)
    waba_id: str = Field(min_length=5, max_length=32, pattern=DIGITS)
    display_phone: str = Field(min_length=1, max_length=32)
    access_token: SecretStr = Field(min_length=20, max_length=4096)


class WhatsAppNumberOut(Contract):
    connected: bool
    display_phone: str | None
    phone_number_id_last4: str | None
    waba_id: str | None
    enabled: bool
    callback_url: str | None
    connected_at: datetime | None


async def session_only(request: Request) -> None:
    """Human console sessions only; the machine token never reaches channel settings."""
    if request.headers.get("authorization"):
        raise HTTPException(403, "human session required")
    await authenticate(request, None, frozenset())


def require_member(request: Request, client_id: UUID, *, write: bool) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, "human session required")
    if actor.platform_admin:
        return actor
    if actor.client_id != client_id or actor.role is None:
        raise HTTPException(403, "tenant is not authorized")
    if write and actor.role != Role.TENANT_ADMIN:
        raise HTTPException(403, "a company administrator is required to change WhatsApp")
    return actor


def present(row: dict[str, Any] | None) -> WhatsAppNumberOut:
    callback = None
    settings = get_settings()
    if settings.whatsapp_enabled:
        origin = settings.WHATSAPP_CALLBACK_BASE_URL or settings.PUBLIC_BASE_URL
        callback = f"{str(origin).rstrip('/')}/channels/whatsapp"
    if row is None:
        return WhatsAppNumberOut(
            connected=False,
            display_phone=None,
            phone_number_id_last4=None,
            waba_id=None,
            enabled=False,
            callback_url=callback,
            connected_at=None,
        )
    meta = row["meta"] or {}
    enabled = bool(row["is_enabled"])
    connected = enabled and bool(row["has_token"])
    return WhatsAppNumberOut(
        connected=connected,
        display_phone=meta.get("display_phone"),
        phone_number_id_last4=(row["shop_id"] or "")[-4:] or None,
        waba_id=meta.get("waba_id"),
        enabled=enabled,
        callback_url=callback,
        connected_at=row["updated_at"] if connected else None,
    )


def build_channels_router() -> APIRouter:
    router = APIRouter(
        prefix="/console/v1/channels",
        tags=["Channels"],
        dependencies=[Depends(session_only), Depends(package_gate("customer_support"))],
    )

    @router.get("/whatsapp", response_model=WhatsAppNumberOut)
    async def get_number(
        request: Request, client_id: Annotated[UUID, Depends(tenant)]
    ) -> WhatsAppNumberOut:
        require_member(request, client_id, write=False)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one("console/whatsapp_number_get.sql", (client_id,), conn=conn)
        return present(row)

    @router.put("/whatsapp", response_model=WhatsAppNumberOut)
    async def connect_number(
        request: Request, payload: WhatsAppNumberIn, client_id: Annotated[UUID, Depends(tenant)]
    ) -> WhatsAppNumberOut:
        actor = require_member(request, client_id, write=True)
        try:
            async with tx(request.app.state.pool, str(client_id)) as conn:
                existing = await db.q.fetch_one(
                    "console/whatsapp_number_get.sql", (client_id,), conn=conn
                )
                credential_id = existing["credential_id"] if existing else uuid4()
                sealed = crypto.encrypt(
                    payload.access_token.get_secret_value(),
                    crypto.credential_aad(
                        "channel_credentials", "access_token", str(credential_id)
                    ),
                )
                await db.q.fetch_one(
                    "console/whatsapp_number_upsert.sql",
                    (
                        credential_id,
                        client_id,
                        payload.phone_number_id,
                        sealed,
                        Jsonb({"waba_id": payload.waba_id, "display_phone": payload.display_phone}),
                        client_id,
                        secrets.token_urlsafe(24),
                        f"WhatsApp {payload.display_phone}",
                    ),
                    conn=conn,
                )
                await audit(
                    conn, actor, client_id, "channels.whatsapp.connected", str(credential_id)
                )
                row = await db.q.fetch_one(
                    "console/whatsapp_number_get.sql", (client_id,), conn=conn
                )
        except errors.UniqueViolation:
            raise HTTPException(409, "whatsapp_number_in_use") from None
        return present(row)

    @router.delete("/whatsapp", status_code=204)
    async def disconnect_number(
        request: Request, client_id: Annotated[UUID, Depends(tenant)]
    ) -> Response:
        actor = require_member(request, client_id, write=True)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "console/whatsapp_number_disable.sql", (client_id, client_id), conn=conn
            )
            if row is not None:
                await audit(
                    conn,
                    actor,
                    client_id,
                    "channels.whatsapp.disconnected",
                    str(row["credential_id"]),
                )
        return Response(status_code=204)

    return router
