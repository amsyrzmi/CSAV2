"""Compact, contact-bound callback actions stored in ``pending_actions`` (LG02 §4)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.contracts.envelope import ContractModel
from csa.core.db import q
from csa.core.tenancy import tx

_CALLBACK_RE = re.compile(r"\Aa:[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
_STAFF_CALLBACK_RE = re.compile(r"\As:[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")


class PendingAction(ContractModel):
    id: UUID
    client_id: UUID
    conversation_id: UUID
    contact_id: UUID
    agent: str
    action: str
    payload: dict[str, object]
    status: Literal["open", "used", "expired", "cancelled"]
    expires_at: datetime


class PendingActionContextError(ValueError):
    """Raised when an action does not belong to the supplied tenant context."""


class CallbackCodec:
    """Stateless parser/formatter for the compact platform callback id."""

    @staticmethod
    def encode(action_id: UUID) -> str:
        """Encode an action id as a Telegram-safe 38-byte callback value."""
        value = f"a:{action_id}"
        if len(value.encode("utf-8")) > 64:
            raise ValueError("callback id exceeds the Telegram callback_data limit")
        return value

    @staticmethod
    def decode(callback_data: str) -> UUID | None:
        """Return the UUID for a valid callback token, otherwise ``None``."""
        if not _CALLBACK_RE.fullmatch(callback_data):
            return None
        return UUID(callback_data[2:])


class StaffCallbackCodec:
    """Callback id for a staff button; resolved through ``staff_actions``, not contacts."""

    @staticmethod
    def encode(action_id: UUID) -> str:
        return f"s:{action_id}"

    @staticmethod
    def decode(callback_data: str) -> UUID | None:
        if not _STAFF_CALLBACK_RE.fullmatch(callback_data):
            return None
        return UUID(callback_data[2:])


class PendingActionStore:
    """Tenant-scoped persistence for one-time callback actions."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def issue(
        self,
        *,
        client_id: UUID | str,
        conversation_id: UUID | str,
        contact_id: UUID | str,
        agent: str,
        action: str,
        payload: dict[str, object],
        ttl: timedelta,
    ) -> str:
        """Persist an action bound to the matching tenant/contact/conversation."""
        if ttl <= timedelta(0):
            raise ValueError("pending action ttl must be positive")
        if not agent.strip() or not action.strip():
            raise ValueError("agent and action must be non-empty")

        client_uuid = UUID(str(client_id))
        conversation_uuid = UUID(str(conversation_id))
        contact_uuid = UUID(str(contact_id))
        async with tx(self._pool, str(client_uuid)) as conn:
            row = await q.fetch_one(
                "pending_actions/insert.sql",
                (
                    client_uuid,
                    agent,
                    action,
                    Jsonb(payload),
                    ttl,
                    conversation_uuid,
                    client_uuid,
                    contact_uuid,
                ),
                conn=conn,
            )
        if row is None:
            raise PendingActionContextError(
                "contact and conversation must belong to the specified client"
            )
        return CallbackCodec.encode(row["id"])

    async def resolve(
        self,
        callback_data: str,
        *,
        client_id: UUID | str,
        contact_id: UUID | str,
    ) -> PendingAction | None:
        """Resolve an open, unexpired action only for its issuing contact."""
        action_id = CallbackCodec.decode(callback_data)
        if action_id is None:
            return None
        client_uuid = UUID(str(client_id))
        contact_uuid = UUID(str(contact_id))
        async with tx(self._pool, str(client_uuid)) as conn:
            await q.execute(
                "pending_actions/expire.sql",
                (action_id, client_uuid, contact_uuid),
                conn=conn,
            )
            row = await q.fetch_one(
                "pending_actions/get_open.sql",
                (action_id, client_uuid, contact_uuid),
                conn=conn,
            )
        return PendingAction.model_validate(row) if row is not None else None

    async def consume(
        self,
        callback_data: str,
        *,
        client_id: UUID | str,
        contact_id: UUID | str,
    ) -> PendingAction | None:
        """Atomically consume an open action once; invalid tokens return ``None``."""
        action_id = CallbackCodec.decode(callback_data)
        if action_id is None:
            return None
        client_uuid = UUID(str(client_id))
        contact_uuid = UUID(str(contact_id))
        async with tx(self._pool, str(client_uuid)) as conn:
            row = await q.fetch_one(
                "pending_actions/consume.sql",
                (action_id, client_uuid, contact_uuid),
                conn=conn,
            )
        return PendingAction.model_validate(row) if row is not None else None
