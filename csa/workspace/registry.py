"""Proposal registry: session-bound, single-use confirmation of assistant mutations.

``get``, ``cancel``, ``cancel_thread`` and ``insert`` run on a caller-owned tenant
transaction (``csa.core.tenancy.tx``); ``create`` and ``confirm`` take the pool and open
their own short transactions so no connection is held across executor calls. A
proposal is visible only to its owner; only the login session that created it may confirm
it, once, before it expires, and only while the payload hash and the executor's bound
record version still match.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.console.contracts import ConsoleActor
from csa.core import db
from csa.core.tenancy import tx
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


ProposalOrigin = Literal["tab", "assistant"]
"""Who proposed it: a human on a console tab, or the assistant (planner or HR chat).

Executors gate assistant-origin proposals on the tenant's assistant switches; a tab
proposal is a human action, so only its package and the user's own permissions apply.
"""


CLAIM_LEASE = timedelta(minutes=2)
"""How long a confirm's execution claim excludes others (hard-coded in proposal_get.sql)."""


class ProposalError(HTTPException):
    """Registry refusal; ``detail`` is one stable code from ``_ERROR_STATUS``."""

    def __init__(self, code: ProposalErrorCode) -> None:
        super().__init__(_ERROR_STATUS[code], code)


class ProposalActionRefused(HTTPException):
    """An owning service's refusal whose response body carries more than the code.

    ``content`` is the whole JSON body, for example ``{"detail": ..., "retry_after": 1200}``;
    the confirm route returns it unchanged, with ``headers`` (such as ``Retry-After``).
    """

    def __init__(
        self, status_code: int, content: dict[str, Any], headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(status_code, content["detail"], headers=dict(headers or {}) or None)
        self.content = content


@dataclass(frozen=True)
class CardContent:
    """What an executor renders for a proposal; the registry adds id, kind and expiry."""

    title: str
    summary: str
    fields: list[CardField]


class ProposalExecutor(Protocol):
    """Acts through the owning service, which opens its own short transactions.

    The registry never holds a connection while it calls an executor, so an executor may
    borrow pool connections (one at a time) without nesting inside the registry's.
    """

    kind: ProposalKind

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        """Refuse unless the tenant's package (and, for the assistant, any switch) allows it."""
        ...

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
    ) -> tuple[CardContent, str | None]:
        """Run ``gate``, validate the payload, load the record as the user; return card+version.

        ``request`` carries the caller's session cookie and CSRF header, which the owning
        services need to revalidate the login session.
        """
        ...

    async def execute(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        proposal_id: UUID,
        expected_version: str | None,
        origin: ProposalOrigin,
    ) -> dict[str, Any]:
        """Perform the action through the owning service and return its JSON receipt.

        Must be idempotent per ``proposal_id`` (keys derive from it): a confirm interrupted
        after the service committed runs ``execute`` again, and the service replays.
        """
        ...


@dataclass(frozen=True)
class PreparedProposal:
    """Executor output for a proposal not yet stored; built with no connection held."""

    kind: ProposalKind
    payload: dict[str, Any]
    content: CardContent
    version: str | None
    origin: ProposalOrigin


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

    async def prepare(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        kind: ProposalKind,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
    ) -> PreparedProposal:
        """Run the executor's gate and card read; call with no connection held."""
        content, version = await self._executor(kind).card(
            request, actor, client_id, payload, origin=origin
        )
        return PreparedProposal(
            kind=kind, payload=payload, content=content, version=version, origin=origin
        )

    async def insert(
        self,
        conn: AsyncConnection[Any],
        actor: ConsoleActor,
        client_id: UUID,
        prepared: PreparedProposal,
        *,
        thread_id: UUID | None,
        origin: ProposalOrigin,
        proposal_id: UUID | None = None,
    ) -> ProposalOut:
        """Store a prepared proposal on the caller's tenant transaction.

        With a caller-derived ``proposal_id`` the insert is idempotent: a second insert of the
        same id (a retry or a concurrent twin) returns the stored proposal and audits nothing.
        """
        if origin != prepared.origin:
            raise ValueError(f"proposal prepared as {prepared.origin} inserted as {origin}")
        card = {
            "kind": prepared.kind,
            "title": prepared.content.title,
            "summary": prepared.content.summary,
            "fields": [f.model_dump(mode="json") for f in prepared.content.fields],
            "expected_version": prepared.version,
        }
        row = await db.q.fetch_one(
            "workspace/proposal_insert.sql",
            (
                proposal_id,
                client_id,
                actor.user_id,
                actor.session_id,
                thread_id,
                prepared.kind,
                Jsonb(prepared.payload),
                payload_sha256(prepared.payload),
                prepared.version,
                Jsonb(card),
                origin,
            ),
            conn=conn,
        )
        if row is None:
            if proposal_id is None:
                raise RuntimeError("proposal insert returned no row")
            return await self.get(conn, actor, client_id, proposal_id)
        await _audit(conn, actor, client_id, "workspace.proposal.created", row)
        return _out(row)

    async def create(
        self,
        pool: AsyncConnectionPool,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        kind: ProposalKind,
        payload: dict[str, Any],
        *,
        thread_id: UUID | None,
        origin: ProposalOrigin,
        proposal_id: UUID | None = None,
    ) -> ProposalOut:
        prepared = await self.prepare(request, actor, client_id, kind, payload, origin=origin)
        async with tx(pool, str(client_id)) as conn:
            return await self.insert(
                conn,
                actor,
                client_id,
                prepared,
                thread_id=thread_id,
                origin=origin,
                proposal_id=proposal_id,
            )

    async def get(
        self, conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID, proposal_id: UUID
    ) -> ProposalOut:
        await _expire_owned(conn, actor, client_id)
        return _out(await _locked(conn, actor, client_id, proposal_id))

    async def confirm(
        self,
        pool: AsyncConnectionPool,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        proposal_id: UUID,
    ) -> ProposalOut:
        """Check, claim, execute, record: each registry step is its own short transaction.

        No registry connection is held while the executor runs. The claim is an exclusive
        lease (``claim_token``, ``claimed_at``; see ``CLAIM_LEASE``): while it is live every
        other confirm gets ``proposal_consumed``. A lease left behind by a confirm that died
        after claiming goes stale and may be retaken; the retaker re-checks expiry and the
        package gate (not the card version: the first run may already have acted) and runs
        the idempotent ``execute`` again, so the service replays its receipt. Revert and
        record both require the caller's own claim token.
        """
        client = str(client_id)
        expired = False
        async with tx(pool, client) as conn:
            row = await _locked(conn, actor, client_id, proposal_id)
            if row["login_session_id"] != actor.session_id:
                raise ProposalError("proposal_session_mismatch")
            if row["status"] == "executed":
                return _out(row)  # replay: the stored receipt, nothing runs again
            if row["status"] not in ("requested", "confirmed"):
                raise ProposalError("proposal_consumed")
            if row["status"] == "confirmed" and row["claim_live"]:
                raise ProposalError("proposal_consumed")  # another confirm is executing
            if payload_sha256(row["payload"]) != row["payload_sha256"]:
                raise ProposalError("proposal_stale")
            if row["is_expired"]:
                await _set_status(conn, client_id, proposal_id, "expired", None)
                expired = True  # committed on block exit, raised below
        if expired:
            raise ProposalError("proposal_expired")
        executor = self._executor(row["kind"])
        payload: dict[str, Any] = row["payload"]
        origin: ProposalOrigin = row["origin"]
        seen_status, seen_token = row["status"], row["claim_token"]
        if seen_status == "requested":
            _, version = await executor.card(request, actor, client_id, payload, origin=origin)
            if version != row["expected_version"]:
                raise ProposalError("proposal_stale")
        else:
            # Retaking a stale claim.
            await executor.gate(request, actor, client_id, origin=origin)
        token = uuid4()
        async with tx(pool, client) as conn:
            row = await _locked(conn, actor, client_id, proposal_id)
            if (
                row["status"] != seen_status
                or row["claim_token"] != seen_token
                or row["claim_live"]
            ):
                raise ProposalError("proposal_consumed")  # a concurrent confirm won
            if row["is_expired"]:
                await _set_status(conn, client_id, proposal_id, "expired", None)
                expired = True
            else:
                row = await _claim(conn, client_id, proposal_id, token)
                await _audit(conn, actor, client_id, "workspace.proposal.confirmed", row)
        if expired:
            raise ProposalError("proposal_expired")
        try:
            receipt = await executor.execute(
                request,
                actor,
                client_id,
                payload,
                proposal_id=proposal_id,
                expected_version=row["expected_version"],
                origin=origin,
            )
        except Exception:
            # The service's own transaction rolled back; release our claim (only ours).
            async with tx(pool, client) as conn:
                current = await _locked(conn, actor, client_id, proposal_id)
                if current["status"] == "confirmed" and current["claim_token"] == token:
                    await _set_status(conn, client_id, proposal_id, "requested", None)
            raise
        async with tx(pool, client) as conn:
            current = await _locked(conn, actor, client_id, proposal_id)
            if current["status"] == "executed":
                return _out(current)  # a retaker of our stale claim recorded it first
            if current["status"] != "confirmed" or current["claim_token"] != token:
                raise ProposalError("proposal_consumed")  # our claim was lost
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


async def _claim(
    conn: AsyncConnection[Any], client_id: UUID, proposal_id: UUID, token: UUID
) -> dict[str, Any]:
    row = await db.q.fetch_one(
        "workspace/proposal_claim.sql", (token, proposal_id, client_id), conn=conn
    )
    if row is None:
        raise RuntimeError(f"proposal {proposal_id} claim returned no row")
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
