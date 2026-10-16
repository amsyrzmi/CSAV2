from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from scripts.verify_reset_target import validate_dsn, verify

from tests.conftest import requires_docker


@pytest.mark.parametrize(
    "dsn",
    [
        "host=127.0.0.1 port=5433 dbname=csa user=csa",
        "host=127.0.0.1 port=5432 dbname=csa user=csa",
        "host=127.0.0.1 dbname=csa user=csa",
        "host=example.test port=5434 dbname=csa user=csa",
        "host=127.0.0.1 port=5434 dbname=csa user=csa_app",
        "host=127.0.0.1 hostaddr=192.0.2.1 port=5434 dbname=csa user=csa",
        "host=127.0.0.1 port=5434 dbname=other user=csa",
        "host=127.0.0.1 port=5434 dbname=csa user=csa service=other",
    ],
)
def test_reset_rejects_nondev_targets(dsn: str) -> None:
    with pytest.raises(ValueError, match="Reset requires"):
        validate_dsn(dsn)


async def test_missing_psql_has_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = AsyncMock(side_effect=FileNotFoundError)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="no container fallback"):
        await verify("host=127.0.0.1 port=5434 dbname=csa user=csa", ["missing-psql"])
    assert spawn.await_count == 1
    assert spawn.call_args.args[0] == "missing-psql"


async def test_failed_psql_prevents_python_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import psycopg

    process = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"private")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    connect = AsyncMock()
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    with pytest.raises(RuntimeError, match="no reset performed"):
        await verify("host=127.0.0.1 port=5434 dbname=csa user=csa", ["psql"])
    connect.assert_not_called()


@pytest.mark.parametrize("matches", [True, False])
async def test_database_fingerprint_check(monkeypatch: pytest.MonkeyPatch, matches: bool) -> None:
    from types import SimpleNamespace

    import psycopg

    identity = b'{"system_identifier":"123","database":"csa"}'
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(identity, b"")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    conn = AsyncMock()
    conn.__aenter__.return_value = conn
    cursor = AsyncMock()
    cursor.fetchone.return_value = (
        identity.decode() if matches else '{"system_identifier":"456","database":"csa"}',
    )
    conn.execute.return_value = cursor
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", AsyncMock(return_value=conn))
    if matches:
        await verify("host=127.0.0.1 port=5434 dbname=csa user=csa", ["psql"])
    else:
        with pytest.raises(RuntimeError, match="targets differ"):
            await verify("host=127.0.0.1 port=5434 dbname=csa user=csa", ["psql"])


async def test_probe_timeout_kills_and_reaps_only_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    async def communicate() -> tuple[bytes, bytes]:
        if not process.kill.called:
            raise TimeoutError
        return b"", b""

    process = SimpleNamespace(kill=Mock(), communicate=AsyncMock(side_effect=communicate))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError, match="timed out; no reset"):
        await verify("host=127.0.0.1 port=5434 dbname=csa user=csa", ["psql"])
    process.kill.assert_called_once_with()
    assert process.communicate.await_count == 2


@requires_docker
async def test_make_refuses_mismatched_database_before_ddl(
    pg_container: object,
) -> None:
    import os
    import shlex
    from pathlib import Path

    import psycopg
    from psycopg.conninfo import make_conninfo

    from tests.conftest import _owner_dsn

    dsn = _owner_dsn(pg_container)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute("CREATE TABLE reset_sentinel (id int)")
        await conn.execute("INSERT INTO reset_sentinel VALUES (1)")
        await conn.execute("CREATE DATABASE wrong_reset_target")
    wrong_dsn = make_conninfo(dsn, dbname="wrong_reset_target")
    async with await psycopg.AsyncConnection.connect(wrong_dsn) as conn:
        await conn.execute("CREATE TABLE reset_sentinel (id int)")
        await conn.execute("INSERT INTO reset_sentinel VALUES (2)")
    container_id = pg_container.get_wrapped_container().id  # type: ignore[attr-defined]
    psql = ["docker", "exec", "-i", container_id, "psql", "-U", "csa", "-d", "wrong_reset_target"]
    process = await asyncio.create_subprocess_exec(
        "make",
        "db-reset",
        f"DEV_OWNER_DSN={dsn}",
        f"PSQL={shlex.join(psql)}",
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ENV": "ci", "NO_LOCAL_ENV": "1"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode != 0
    assert b"database targets differ" in output
    for target, expected in ((dsn, 1), (wrong_dsn, 2)):
        async with await psycopg.AsyncConnection.connect(target) as conn:
            assert await (await conn.execute("SELECT id FROM reset_sentinel")).fetchone() == (
                expected,
            )
