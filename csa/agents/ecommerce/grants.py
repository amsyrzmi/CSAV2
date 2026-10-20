"""Internal grant persistence; never resolves credentials or dispatches provider reads."""

from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from csa.agents.ecommerce.apps import require_app
from csa.agents.ecommerce.authorization import TikTokAuthorizationError, TikTokGrantState
from csa.core import db
from csa.core.tenancy import tx


async def create_verified_grant(pool: AsyncConnectionPool, grant: TikTokGrantState) -> None:
    """Persist trusted verifier output only; callers must authenticate its provenance.

    Existing grants are never reactivated or overwritten.
    """
    if grant.state != "active" or grant.revision != 1:
        raise TikTokAuthorizationError("invalid_initial_grant")
    binding = grant.context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        await insert_verified_grant(conn, grant)


async def insert_verified_grant(conn: AsyncConnection[Any], grant: TikTokGrantState) -> None:
    """Insert on a caller-owned transaction for atomic authorization completion."""
    if grant.state != "active" or grant.revision != 1:
        raise TikTokAuthorizationError("invalid_initial_grant")
    binding = grant.context.binding
    await require_app(conn, grant.context.app_id, grant.context.app_revision)
    credential = await db.q.fetch_one(
        "ecommerce/lock_credential.sql",
        (
            binding.client_id,
            binding.record_id,
            binding.shop_id,
            grant.context.credential_generation,
        ),
        conn=conn,
    )
    if credential is None:
        raise TikTokAuthorizationError("grant_create_conflict")
    row = await db.q.fetch_one(
        "ecommerce/create_grant.sql",
        {
            "id": grant.id,
            "client_id": binding.client_id,
            "record_id": binding.record_id,
            "shop_id": binding.shop_id,
            "app_id": grant.context.app_id,
            "app_revision": grant.context.app_revision,
            "credential_generation": grant.context.credential_generation,
            "scopes": sorted(grant.scopes),
            "verified_at": grant.verified_at,
            "expires_at": grant.expires_at,
        },
        conn=conn,
    )
    if row is None:
        raise TikTokAuthorizationError("grant_create_conflict")


async def load_grant(
    pool: AsyncConnectionPool, client_id: UUID, grant_id: UUID
) -> TikTokGrantState:
    """Return durable metadata, not a lease or current credential-generation proof."""
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("ecommerce/get_grant.sql", (client_id, grant_id), conn=conn)
        if row is None:
            raise TikTokAuthorizationError("missing_grant")
        return TikTokGrantState.model_validate(row)


async def invalidate_grant(
    pool: AsyncConnectionPool,
    client_id: UUID,
    grant_id: UUID,
    *,
    expected_revision: int,
    state: Literal["revoked", "disconnected"],
) -> int:
    """CAS terminal transition and audit event commit atomically, with no reactivation."""
    if type(expected_revision) is not int or expected_revision < 1:
        raise TikTokAuthorizationError("invalid_revision")
    if state not in ("revoked", "disconnected"):
        raise TikTokAuthorizationError("invalid_transition")
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one(
            "ecommerce/invalidate_grant.sql",
            (state, client_id, grant_id, expected_revision),
            conn=conn,
        )
        if row is None:
            raise TikTokAuthorizationError("stale_grant")
        return row["revision"]
