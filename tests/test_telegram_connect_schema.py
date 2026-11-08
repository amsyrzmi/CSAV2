"""Migration 0494: one active Telegram bot per company and per bot, enforced by the database.

Runs against a disposable migrated Postgres with synthetic rows only (made-up bot ids).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.usefixtures("migrated_dsn")]

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
MIGRATION = MIGRATIONS / "0494_telegram_per_client.sql"
BOT_KEY = "channel_credentials_telegram_bot_key"
CLIENT_KEY = "channel_credentials_telegram_client_key"


@pytest.fixture
def owner(pg_container: object) -> Any:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        yield conn


def make_tenant(owner: psycopg.Connection[Any]) -> uuid.UUID:
    client = uuid.uuid4()
    owner.execute(
        "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,'Synthetic tg','t')",
        (client, f"tg-mig-{client}"),
    )
    return client


def make_bot(
    owner: psycopg.Connection[Any],
    client: uuid.UUID,
    bot: str,
    *,
    channel: str = "telegram",
    disconnected: bool = False,
) -> uuid.UUID:
    row = uuid.uuid4()
    owner.execute(
        "INSERT INTO channel_credentials(id,client_id,channel,shop_id,disconnected_at) "
        "VALUES (%s,%s,%s,%s,CASE WHEN %s THEN now() END)",
        (row, client, channel, bot, disconnected),
    )
    return row


def bot_id() -> str:
    return str(uuid.uuid4().int % 10**10 + 10**9)


def test_a_bot_is_active_for_one_company_only(owner: psycopg.Connection[Any]) -> None:
    a, b = make_tenant(owner), make_tenant(owner)
    bot = bot_id()
    make_bot(owner, a, bot)
    with pytest.raises(errors.UniqueViolation) as caught:
        make_bot(owner, b, bot)
    assert caught.value.diag.constraint_name == BOT_KEY


def test_a_company_has_one_active_bot(owner: psycopg.Connection[Any]) -> None:
    a = make_tenant(owner)
    make_bot(owner, a, bot_id())
    with pytest.raises(errors.UniqueViolation) as caught:
        make_bot(owner, a, bot_id())
    assert caught.value.diag.constraint_name == CLIENT_KEY


def test_a_disconnected_bot_frees_both_the_bot_and_the_company(
    owner: psycopg.Connection[Any],
) -> None:
    a, b = make_tenant(owner), make_tenant(owner)
    bot = bot_id()
    first = make_bot(owner, a, bot)
    owner.execute("UPDATE channel_credentials SET disconnected_at = now() WHERE id = %s", (first,))
    make_bot(owner, b, bot)
    make_bot(owner, a, bot_id())


def test_other_channels_are_not_limited(owner: psycopg.Connection[Any]) -> None:
    a = make_tenant(owner)
    make_bot(owner, a, bot_id())
    make_bot(owner, a, f"SYNTH-{uuid.uuid4()}", channel="tiktok_shop")
    make_bot(owner, a, f"SYNTH-{uuid.uuid4()}", channel="tiktok_shop")
    make_bot(owner, a, str(uuid.uuid4().int % 10**12), channel="whatsapp")


def test_the_migration_refuses_when_duplicates_already_exist(
    owner: psycopg.Connection[Any],
) -> None:
    a = make_tenant(owner)
    first, second = make_bot(owner, a, bot_id()), None
    owner.execute(f"DROP INDEX public.{BOT_KEY}")
    owner.execute(f"DROP INDEX public.{CLIENT_KEY}")
    try:
        second = make_bot(owner, a, bot_id())
        with pytest.raises(errors.RaiseException) as caught:
            owner.execute(MIGRATION.read_text())  # type: ignore[arg-type]
        assert str(a) in str(caught.value)
    finally:
        if second is not None:
            owner.execute("DELETE FROM channel_credentials WHERE id = %s", (second,))
        owner.execute("DELETE FROM channel_credentials WHERE id = %s", (first,))
        owner.execute(MIGRATION.read_text())  # type: ignore[arg-type]
    indexes = {
        row[0]
        for row in owner.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'channel_credentials'"
        ).fetchall()
    }
    assert {BOT_KEY, CLIENT_KEY} <= indexes


def test_the_app_role_can_write_what_connect_writes(owner: psycopg.Connection[Any]) -> None:
    wanted = {
        "channel_credentials": ("SELECT", "INSERT", "UPDATE"),
        "webhook_endpoints": ("SELECT", "INSERT", "UPDATE"),
        "staff_notification_targets": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    }
    for table, privileges in wanted.items():
        for privilege in privileges:
            row = owner.execute(
                "SELECT has_table_privilege('csa_app', %s, %s)", (f"public.{table}", privilege)
            ).fetchone()
            assert row is not None and row[0] is True, (table, privilege)
