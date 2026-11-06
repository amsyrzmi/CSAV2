"""Operator command: register, enable, disable on the owner connection; never prints a secret."""

from __future__ import annotations

import asyncio

import psycopg
import pytest
from scripts import tiktok_app

from tests.conftest import requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]


async def run(argv: list[str]) -> int:
    # main() owns its event loop, so it runs off the test's loop.
    return await asyncio.to_thread(tiktok_app.main, argv)


async def row(dsn: str, app_id: str) -> tuple[int, str] | None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cursor = await conn.execute(
            "SELECT revision, state FROM ecommerce_apps WHERE app_id = %s", (app_id,)
        )
        found = await cursor.fetchone()
    return None if found is None else (found[0], found[1])


async def test_script_register_enable_disable_cycle(
    migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIGRATE_DATABASE_URL", migrated_dsn)
    monkeypatch.setenv("TIKTOK_APP_SECRET", "synthetic-app-secret")
    app = ["--app-id", "script_cycle"]

    assert await run(["register", *app]) == 0
    assert "script_cycle: disabled at revision 1" in capsys.readouterr().out
    assert await row(migrated_dsn, "script_cycle") == (1, "disabled")

    assert await run(["enable", *app]) == 0
    assert "script_cycle: active at revision 2" in capsys.readouterr().out
    assert await row(migrated_dsn, "script_cycle") == (2, "active")

    assert await run(["disable", *app]) == 0
    assert "script_cycle: disabled at revision 3" in capsys.readouterr().out
    assert await row(migrated_dsn, "script_cycle") == (3, "disabled")

    captured = capsys.readouterr()
    assert "synthetic-app-secret" not in captured.out + captured.err
    assert migrated_dsn not in captured.out + captured.err


async def test_script_default_app_id_is_the_settings_default(
    migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIGRATE_DATABASE_URL", migrated_dsn)
    monkeypatch.delenv("TIKTOK_APP_ID", raising=False)
    assert await run(["register"]) == 0
    assert "tiktok_shop: disabled at revision 1" in capsys.readouterr().out
    assert await row(migrated_dsn, "tiktok_shop") == (1, "disabled")


async def test_script_refuses_conflicts_and_unknown_apps_with_safe_codes(
    migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIGRATE_DATABASE_URL", migrated_dsn)
    app = ["--app-id", "script_refusals"]
    # Enabling an app nobody registered.
    assert await run(["enable", *app]) == 1
    assert "app_not_registered" in capsys.readouterr().err
    assert await run(["register", *app]) == 0
    # Registering twice.
    assert await run(["register", *app]) == 1
    assert "app_registration_conflict" in capsys.readouterr().err
    # Disabling an app that is already disabled changes nothing.
    assert await run(["disable", *app]) == 0
    assert "script_refusals: disabled at revision 1 (unchanged)" in capsys.readouterr().out
    assert await row(migrated_dsn, "script_refusals") == (1, "disabled")


async def test_script_refuses_without_the_owner_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MIGRATE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://csa_app:synthetic@localhost:1/none")
    assert await run(["register"]) == 2
    assert "MIGRATE_DATABASE_URL" in capsys.readouterr().err
