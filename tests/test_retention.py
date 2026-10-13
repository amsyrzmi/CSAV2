"""Checkpoint retention skeleton tests (LG01 §7.3).

Proves the design's connection split: the open-approval exclusion MUST be read
as OWNER ``csa`` (RLS would zero it on the GUC-less ``csa_app`` checkpoint
pool). The test:

* seeds an old idle thread and an old thread that has an open pending approval;
* asserts the open-approval thread is RETAINED when the approvals read runs via
  the owner path, and the plain-idle thread is selected for deletion;
* asserts a GUC-less ``csa_app`` read of ``public.approvals`` returns zero rows
  (documenting WHY the owner connection is required);
* runs ``run_retention_once`` end-to-end and checks the right thread's
  checkpoints are gone and the retained thread's remain.
"""  # LG01

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest
from csa.core import db
from csa.graph import retention
from csa.graph.saver import build_checkpoint_pool

from tests.conftest import CSA_APP_PASSWORD, owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]

_GREGORIAN_EPOCH = dt.datetime(1582, 10, 15, tzinfo=dt.UTC)

CLIENT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _row(value: object) -> dict[str, object]:
    """Assert a fetched row is present and narrow it to a dict."""
    assert value is not None
    return value  # type: ignore[return-value]


def _uuidv6_at(when: dt.datetime) -> str:
    """Build a UUIDv6 whose embedded timestamp is ``when`` (for seeding)."""
    intervals = round((when - _GREGORIAN_EPOCH).total_seconds() * 1e7)
    time_high = (intervals >> 28) & 0xFFFFFFFF
    time_mid = (intervals >> 12) & 0xFFFF
    time_low = intervals & 0x0FFF
    node = uuid.uuid4().int & 0xFFFFFFFFFFFF
    clock_seq = 0x3FFF
    i = (
        (time_high << 96)
        | (time_mid << 80)
        | (0x6 << 76)  # version 6
        | (time_low << 64)
        | (0x2 << 62)  # RFC 4122 variant
        | (clock_seq << 48)
        | node
    )
    return str(uuid.UUID(int=i))


def _app_lg_dsn(container: object) -> str:
    host = container.get_container_host_ip()  # type: ignore[attr-defined]
    port = container.get_exposed_port(5432)  # type: ignore[attr-defined]
    return f"postgresql://csa_app:{CSA_APP_PASSWORD}@{host}:{port}/csa"


def _seed_checkpoint(owner_dsn: str, thread_id: str, checkpoint_id: str) -> None:
    """Insert a minimal checkpoint row for ``thread_id`` as owner csa."""
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute("SET search_path TO lg")
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
            "checkpoint, metadata) VALUES (%s, '', %s, '{}'::jsonb, '{}'::jsonb)",
            (thread_id, checkpoint_id),
        )


def _seed_approval(owner_dsn: str, trace_id: str) -> None:
    """Insert an open pending approval whose trace_id == thread_id."""
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) "
            "VALUES (%s, 'ret-a', 'ret-a', 'lg01-test') ON CONFLICT (id) DO NOTHING",
            (CLIENT_A,),
        )
        expires = dt.datetime.now(dt.UTC) + dt.timedelta(days=7)
        conn.execute(
            "INSERT INTO approvals (client_id, conversation_id, trace_id, draft, "
            "reason, status, expires_at) VALUES (%s, %s, %s, 'd', 'r', 'pending', %s)",
            (CLIENT_A, str(uuid.uuid4()), trace_id, expires),
        )


async def test_retention_excludes_open_approval(lg_setup_dsn: str, pg_container: object) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    now = dt.datetime.now(dt.UTC)
    old = now - dt.timedelta(days=30)

    idle_thread = str(uuid.uuid4())  # old, no approval -> delete
    approval_thread = str(uuid.uuid4())  # old, open approval -> retain

    _seed_checkpoint(owner_dsn, idle_thread, _uuidv6_at(old))
    _seed_checkpoint(owner_dsn, approval_thread, _uuidv6_at(old))
    _seed_approval(owner_dsn, approval_thread)

    # The open-approval set, read as OWNER csa, must include the approval thread.
    open_ids = await retention.open_approval_trace_ids(owner_dsn)
    assert approval_thread in open_ids
    assert idle_thread not in open_ids

    pool = build_checkpoint_pool(_app_lg_dsn(pg_container))
    await pool.open(wait=True)
    try:
        deleted = await retention.run_retention_once(pool, owner_dsn, retention_days=14, now=now)
        assert idle_thread in deleted
        assert approval_thread not in deleted

        # The idle thread's checkpoints are gone; the retained thread's remain.
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s",
                    (idle_thread,),
                )
                assert _row(await cur.fetchone())["n"] == 0
                await cur.execute(
                    "SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s",
                    (approval_thread,),
                )
                assert _row(await cur.fetchone())["n"] == 1
    finally:
        await pool.close()


async def test_guc_less_csa_app_reads_zero_approvals(
    lg_setup_dsn: str, pg_container: object
) -> None:
    """Document WHY retention reads approvals as owner, not via csa_app.

    A GUC-less ``csa_app`` connection (what the checkpoint pool is) sees zero
    rows of RLS-enabled ``public.approvals``, which would silently defeat the
    open-approval exclusion.
    """
    owner_dsn = owner_dsn_for(pg_container)
    trace_id = str(uuid.uuid4())
    _seed_approval(owner_dsn, trace_id)

    # Owner sees it...
    owner_ids = await retention.open_approval_trace_ids(owner_dsn)
    assert trace_id in owner_ids

    # ...but a GUC-less csa_app read returns zero rows.
    pool = db.build_pool(_app_lg_dsn(pg_container), min_size=1, max_size=2)
    await pool.open(wait=True)
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) AS n FROM public.approvals WHERE status = 'pending'"
                )
                assert _row(await cur.fetchone())["n"] == 0
    finally:
        await pool.close()
