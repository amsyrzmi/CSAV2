"""A failed turn's customer text: a localized holding message, and still the staff record."""

from __future__ import annotations

import base64
import uuid
from typing import Any

import psycopg
import pytest
from csa.contracts.envelope import Envelope
from csa.contracts.state import Context
from csa.core import crypto, settings
from csa.turn.deps import PostgresTurnDependencies
from csa.turn.texts import holding_text

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_coordinator import make_envelope
from tests.test_order_agent import World, world  # noqa: F401

pytestmark = [requires_docker, pytest.mark.asyncio]

_KEY = "base64:" + base64.b64encode(b"\x07" * 32).decode("ascii")


@pytest.fixture(autouse=True)
def _crypto_key(
    monkeypatch: pytest.MonkeyPatch, migrated_dsn: str, app_dsn: str, pg_container: object
) -> None:
    for name, value in {
        "ENV": "ci",
        "DATABASE_URL": app_dsn,
        "LG_DATABASE_URL": owner_dsn_for(pg_container),
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": _KEY,
    }.items():
        monkeypatch.setenv(name, value)
    settings.get_settings.cache_clear()


def _endpoint(world: World) -> uuid.UUID:  # noqa: F811
    credential_id = uuid.uuid4()
    endpoint_id = uuid.uuid4()
    token = crypto.encrypt(
        "12345:dev-test-token",
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token) "
            "VALUES (%s,%s,'telegram',%s)",
            (credential_id, world.client_id, token),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s,%s,%s,%s)",
            (endpoint_id, world.client_id, uuid.uuid4().hex, credential_id),
        )
    return endpoint_id


def _envelope(world: World, endpoint_id: uuid.UUID, body: str, language: str) -> Envelope:  # noqa: F811
    """A turn of the world's own conversation, with its ingress row (the delivery needs it)."""
    trace_id = uuid.uuid4()
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ingress_events (client_id, endpoint_id, idempotency_key, payload_hash, "
            "trace_id) VALUES (%s,%s,%s,'h',%s)",
            (world.client_id, endpoint_id, f"k-{trace_id}", trace_id),
        )
    base = make_envelope(body)
    return base.model_copy(
        update={
            "trace_id": trace_id,
            "client_id": world.client_id,
            "contact": base.contact.model_copy(update={"id": world.contact_id}),
            "conversation": base.conversation.model_copy(update={"id": world.conversation_id}),
            "message": base.message.model_copy(
                update={"body_raw": body, "body_redacted": body, "language": language}
            ),
        }
    )


async def _fail(world: World, envelope: Envelope) -> Any:  # noqa: F811
    deps = PostgresTurnDependencies(world.pool)
    return await deps.fail_safe(envelope, Context(), "signals:GroqError")


@pytest.mark.parametrize(
    ("body", "language", "expected"),
    [
        ("hello", "ms", "ms"),  # the envelope's language
        ("nak tanya boleh x", "und", "ms"),  # unknown: the detector reads Malay words
        ("where is my parcel", "und", "en"),  # unknown, and not Malay: English
    ],
)
async def test_fail_safe_text_is_localized(
    world: World,  # noqa: F811
    body: str,
    language: str,
    expected: Any,
) -> None:
    envelope = _envelope(world, _endpoint(world), body, language)
    message = await _fail(world, envelope)
    assert message.body == holding_text(expected)
    assert world.q(
        "SELECT body FROM messages WHERE client_id=%s AND direction='outbound'", world.client_id
    ) == [(holding_text(expected),)]
    # It promises nothing a person will do.
    assert "follow up" not in message.body.lower()


async def test_fail_safe_still_writes_the_escalation_row(world: World) -> None:  # noqa: F811
    envelope = _envelope(world, _endpoint(world), "where is my parcel", "en")
    await _fail(world, envelope)
    assert world.q(
        "SELECT reason, priority FROM escalations WHERE client_id=%s AND trace_id=%s",
        world.client_id,
        envelope.trace_id,
    ) == [("fail_safe:signals:GroqError", "high")]
    assert world.q(
        "SELECT count(*) FROM outbound_deliveries WHERE client_id=%s", world.client_id
    ) == [(1,)]
