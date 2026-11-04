"""Schema contract for migration 0500: contacts.telegram_username."""

from __future__ import annotations

import uuid

import pytest
from psycopg import errors

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]
pytestmark = [requires_docker, pytest.mark.asyncio]


async def _contact(world: SessionWorld, username: str | None) -> None:
    await world.q(
        "INSERT INTO contacts (id, client_id, telegram_username) VALUES (%s, %s, %s)",
        uuid.uuid4(),
        world.tenants[0],
        username,
    )


async def test_0500_column_exists_is_nullable_and_checked(sessions: SessionWorld) -> None:
    [column] = await sessions.q(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='contacts' "
        "AND column_name='telegram_username'"
    )
    assert column == {"data_type": "text", "is_nullable": "YES"}
    await _contact(sessions, None)
    await _contact(sessions, "aisyah_r")
    for bad in ("ab", "has space", "a-b-c-d", "a" * 33, ""):
        with pytest.raises(errors.CheckViolation):
            await _contact(sessions, bad)
