"""Proposal registry: session-bound, single-use confirmation of assistant mutations.

Every method runs on a caller-owned tenant transaction (``csa.core.tenancy.tx``). A
proposal is visible only to its owner; only the login session that created it may confirm
it, once, before it expires, and only while the payload hash and the executor's bound
record version still match.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.console.contracts import ConsoleActor
from csa.core import db
from csa.workspace.contracts import CardField, ProposalCard, ProposalKind, ProposalOut

ProposalErrorCode = Literal[
    "proposal_session_mismatch",
    "proposal_not_found",
    "proposal_expired",
    "proposal_consumed",
    "proposal_stale",
]
_ERROR_STATUS: dict[ProposalErrorCode, int] = {
    "proposal_session_mismatch": 403,
    "proposal_not_found": 404,
    "proposal_expired": 409,
    "proposal_consumed": 409,
    "proposal_stale": 409,
}


class ProposalError(HTTPException):
    """Registry refusal; ``detail`` is one stable code from ``_ERROR_STATUS``."""

    def __init__(self, code: ProposalErrorCode) -> None:
        super().__init__(_ERROR_STATUS[code], code)


@dataclass(frozen=True)
class CardContent:
    """What an executor renders for a proposal; the registry adds id, kind and expiry."""

    title: str
    summary: str
    fields: list[CardField]


class ProposalExecutor(Protocol):
    kind: ProposalKind

    async def card(
        self,
        conn: AsyncConnection[Any],
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
    ) -> tuple[CardContent, str | None]:
        """Gate and validate the payload, load the record as the user, return content and version.

        ``request`` carries the caller's session cookie and CSRF header, which the owning
        services need to revalidate the login session.
        """
        ...

    async def execute(
        self,
        conn: AsyncConnection[Any],
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        proposal_id: UUID,
        expected_version: str | None,
    ) -> dict[str, Any]:
        """Perform the action through the owning service and return its JSON receipt.

        Idempotency keys derive from ``proposal_id`` so a retry replays in the service.
        """
        ...


def payload_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class ProposalRegistry:
    def __init__(self, executors: Mapping[ProposalKind, ProposalExecutor]) -> None:
        for kind, executor in executors.items():
            if executor.kind != kind:
                raise ValueError(f"executor for {kind} reports kind {executor.kind}")
        self._executors: dict[str, ProposalExecutor] = {k: v for k, v in executors.items()}

    def _executor(self, kind: str) -> ProposalExecutor:
        executor = self._executors.get(kind)
        if executor is None:
            raise LookupError(f"no proposal executor registered for kind {kind!r}")
        return executor

    async def create(
        self,
        conn: AsyncConnection[Any],
        actor: ConsoleActor,
        client_id: UUID,
        kind: ProposalKind,
        payload: dict[str, Any],
        *,
        thread_id: UUID | None,
        request: Request,
    ) -> ProposalOut:
        executor = self._executor(kind)
        content, version = await executor.card(conn, request, actor, client_id, payload)
        card = {
            "kind": kind,
            "title": content.title,
            "summary": content.summary,
            "fields": [f.model_dump(mode="json") for f in content.fields],
            "expected_version": version,
        }
        row = await db.q.fetch_one(
            "workspace/proposal_insert.sql",
            (
                client_id,
                actor.user_id,
                actor.session_id,
                thread_id,
                kind,
                Jsonb(payload),
                payload_sha256(payload),
                version,
                Jsonb(card),
            ),
            conn=conn,
        )
        if row is None:
            raise RuntimeError("proposal insert returned no row")
        await _audit(conn, actor, client_id, "workspace.proposal.created", row)
        return _out(row)

    async def get(
        self, conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID, proposal_id: UUID
    ) -> ProposalOut:
        await _expire_owned(conn, actor, client_id)
        return _out(await _locked(conn, actor, client_id, proposal_id))

    async def confirm(
        self,
        conn: AsyncConnection[Any],
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        proposal_id: UUID,
    ) -> ProposalOut:
        row = await _locked(conn, actor, client_id, proposal_id)
        if row["login_session_id"] != actor.session_id:
            raise ProposalError("proposal_session_mismatch")
        if row["status"] == "executed":
            return _out(row)  # replay: the stored receipt, nothing runs again
        if row["status"] != "requested":
            raise ProposalError("proposal_consumed")
        if row["is_expired"]:
            # Persisted when the caller commits; otherwise the next read's sweep persists it.
            await _set_status(conn, client_id, proposal_id, "expired", None)
            raise ProposalError("proposal_expired")
        payload: dict[str, Any] = row["payload"]
        if payload_sha256(payload) != row["payload_sha256"]:
            raise ProposalError("proposal_stale")
        executor = self._executor(row["kind"])
        _, version = await executor.card(conn, request, actor, client_id, payload)
        if version != row["expected_version"]:
            raise ProposalError("proposal_stale")
        row = await _set_status(conn, client_id, proposal_id, "confirmed", None)
        await _audit(conn, actor, client_id, "workspace.proposal.confirmed", row)
        receipt = await executor.execute(
            conn,
            request,
            actor,
            client_id,
            payload,
            proposal_id=proposal_id,
            expected_version=version,
        )
        row = await _set_status(conn, client_id, proposal_id, "executed", Jsonb(receipt))
        await _audit(conn, actor, client_id, "workspace.proposal.executed", row)
        return _out(row)

    async def cancel(
        self, conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID, proposal_id: UUID
    ) -> ProposalOut:
        await _expire_owned(conn, actor, client_id)
        row = await _locked(conn, actor, client_id, proposal_id)
        if row["status"] in ("confirmed", "executed"):
            raise ProposalError("proposal_consumed")
        if row["status"] == "requested":
            row = await _set_status(conn, client_id, proposal_id, "cancelled", None)
            await _audit(conn, actor, client_id, "workspace.proposal.cancelled", row)
        return _out(row)

    async def cancel_thread(
        self, conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID, thread_id: UUID
    ) -> int:
        await _expire_owned(conn, actor, client_id)
        rows = await db.q.fetch_all(
            "workspace/proposal_cancel_thread.sql",
            (client_id, actor.user_id, thread_id),
            conn=conn,
        )
        for row in rows:
            await _audit(conn, actor, client_id, "workspace.proposal.cancelled", row)
        return len(rows)


async def _locked(
    conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID, proposal_id: UUID
) -> dict[str, Any]:
    row = await db.q.fetch_one(
        "workspace/proposal_get.sql", (proposal_id, client_id, actor.user_id), conn=conn
    )
    if row is None:
        raise ProposalError("proposal_not_found")
    return row


async def _expire_owned(conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID) -> None:
    await db.q.execute("workspace/proposal_expire.sql", (client_id, actor.user_id), conn=conn)


async def _set_status(
    conn: AsyncConnection[Any],
    client_id: UUID,
    proposal_id: UUID,
    status: str,
    result: Jsonb | None,
) -> dict[str, Any]:
    row = await db.q.fetch_one(
        "workspace/proposal_set_status.sql",
        (status, result, status, proposal_id, client_id),
        conn=conn,
    )
    if row is None:
        raise RuntimeError(f"proposal {proposal_id} status update returned no row")
    return row


async def _audit(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    action: str,
    row: Mapping[str, Any],
) -> None:
    await db.q.execute(
        "console/audit.sql",
        (actor.user_id, client_id, action, str(row["id"]), Jsonb({"kind": row["kind"]})),
        conn=conn,
    )


def _out(row: Mapping[str, Any]) -> ProposalOut:
    """Render from the stored card; records may have changed since creation."""
    card = ProposalCard.model_validate(
        {**row["card"], "proposal_id": row["id"], "expires_at": row["expires_at"]}
    )
    return ProposalOut(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        card=card,
        result=row["result"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )
