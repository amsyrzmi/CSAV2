"""Synthetic media, real session revocation, atomic queueing and tenant persistence."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.console.voice_sessions import build_voice_media_router
from csa.core import db
from csa.core.tenancy import tx
from csa.voice.contracts import (
    CreateVoiceSession,
    FinalUtterance,
    VoiceSessionConfig,
    VoiceSessionRecord,
)
from csa.voice.security import media_signature, valid_media_signature
from csa.voice.service import VoiceSessionService
from livekit import api
from psycopg.errors import ForeignKeyViolation, InsufficientPrivilege, LockNotAvailable
from pydantic import SecretStr, ValidationError

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]
MEDIA_SECRET = "synthetic-media-secret-" + "x" * 32
CONFIG = VoiceSessionConfig(
    server_url="wss://voice.example.test",
    api_key=SecretStr("synthetic-livekit-key"),
    api_secret=SecretStr("synthetic-livekit-secret-" + "x" * 32),
    media_secret=SecretStr(MEDIA_SECRET),
)


def configure(world: SessionWorld) -> None:
    world.app.state.voice_sessions_config = CONFIG
    world.app.include_router(build_voice_media_router())


async def create(
    world: SessionWorld, client: httpx.AsyncClient | None = None, *, tenant: str | None = None
):
    client, tenant = client or world.admin, tenant or world.tenants[0]
    thread = await world.mutate(
        client, f"/console/v1/chat/conversations?client_id={tenant}", {"request_key": str(uuid4())}
    )
    assert thread.status_code == 201, thread.text
    body = {
        "request_key": str(uuid4()),
        "conversation_id": thread.json()["id"],
        "language": "ms",
        "consent": True,
    }
    path = f"/console/v1/chat/voice/sessions?client_id={tenant}"
    response = await world.mutate(client, path, body)
    assert response.status_code == 200, response.text
    return response.json(), path, body


async def media(
    world: SessionWorld,
    session_id: str,
    utterance: str,
    body: dict,
    *,
    timestamp: str | None = None,
    signature: str | None = None,
    state: bool = False,
):
    path = f"/internal/voice/sessions/{session_id}/" + (
        "state" if state else f"utterances/{utterance}"
    )
    raw = json.dumps(body).encode()
    timestamp = timestamp or str(int(time.time()))
    headers = {
        "x-voice-timestamp": timestamp,
        "x-voice-signature": signature
        if signature is not None
        else media_signature(MEDIA_SECRET, path, timestamp, raw),
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app), base_url="https://media.example.test"
    ) as client:
        return await client.post(path, content=raw, headers=headers)


def test_mac_binds_path_timestamp_and_full_payload() -> None:
    path, now, raw = "/internal/voice/sessions/s/utterances/u", str(int(time.time())), b"synthetic"
    signature = media_signature(MEDIA_SECRET, path, now, raw)
    assert valid_media_signature(MEDIA_SECRET, path, now, raw, signature)
    for changed in (path + "other", path.replace("sessions/s", "sessions/other")):
        assert not valid_media_signature(MEDIA_SECRET, changed, now, raw, signature)
    assert not valid_media_signature(MEDIA_SECRET, path, now, b"changed", signature)
    assert not valid_media_signature(MEDIA_SECRET, path, str(int(now) - 60), raw, signature)
    assert not valid_media_signature(MEDIA_SECRET, path, "invalid", raw, signature)
    assert not valid_media_signature(MEDIA_SECRET, path, now, raw, "é" * 64)


def test_consent_and_final_markers_are_explicit_booleans() -> None:
    for invalid in (False, 1, "true"):
        with pytest.raises(ValidationError):
            CreateVoiceSession.model_validate(
                {"request_key": uuid4(), "conversation_id": uuid4(), "consent": invalid}
            )
        with pytest.raises(ValidationError):
            FinalUtterance.model_validate(
                {"client_id": uuid4(), "transcript": "Synthetic", "final": invalid}
            )


def test_connection_has_only_room_audio_grants_and_clipped_expiry() -> None:
    now, session_id = datetime.now(UTC), uuid4()
    record = VoiceSessionRecord(
        id=session_id,
        conversation_id=uuid4(),
        request_key=uuid4(),
        client_id=uuid4(),
        user_id=uuid4(),
        login_session_id=uuid4(),
        profile="sandbox",
        language="ms",
        status="active",
        created_at=now,
        expires_at=now + timedelta(seconds=20),
    )
    result = VoiceSessionService(CONFIG).connection(record)
    claims = api.TokenVerifier(
        CONFIG.api_key.get_secret_value(), CONFIG.api_secret.get_secret_value()
    ).verify(result.token)
    assert claims.video is not None
    assert claims.video.room == f"csa-voice-{session_id}"
    assert claims.video.room_join is True
    assert claims.video.can_publish_sources == ["microphone"]
    assert claims.video.can_publish_data is False
    assert claims.video.can_update_own_metadata is False
    assert claims.video.can_manage_agent_session is False
    assert (
        not claims.video.room_admin
        and not claims.video.room_create
        and not claims.video.room_record
    )
    assert result.expires_at == record.expires_at
    for private in (str(record.user_id), CONFIG.api_secret.get_secret_value(), MEDIA_SECRET):
        assert private not in result.model_dump_json()


@requires_docker
@pytest.mark.asyncio
async def test_session_replay_scoped_connection_and_atomic_utterance(sessions: SessionWorld):
    world = sessions
    configure(world)
    session, path, body = await create(world)
    assert (
        await media(world, session["id"], "", {"client_id": world.tenants[0]}, state=True)
    ).json()["status"] == "active"
    assert (await world.mutate(world.admin, path, body)).json() == session
    assert (await world.mutate(world.admin, path, {**body, "language": "en"})).status_code == 409
    base = f"/console/v1/chat/voice/sessions/{session['id']}"
    connection = await world.mutate(world.admin, base + f"/connection?client_id={world.tenants[0]}")
    assert connection.status_code == 200, connection.text
    assert connection.json()["room"] == f"csa-voice-{session['id']}"
    assert "login_session_id" not in json.dumps(session)
    utterance = str(uuid4())
    final = {"client_id": world.tenants[0], "transcript": "Email demo@example.test", "final": True}
    replies = await asyncio.gather(
        *(media(world, session["id"], utterance, final) for _ in range(3))
    )
    assert [r.status_code for r in replies] == [200] * 3
    assert sorted(r.json()["duplicate"] for r in replies) == [False, True, True]
    receipt = replies[0].json()
    assert (
        await media(world, session["id"], utterance, {**final, "transcript": "different"})
    ).status_code == 409
    turns = await world.q(
        "SELECT body,trace_id FROM console_chat_turns WHERE conversation_id=%s",
        session["conversation_id"],
    )
    assert len(turns) == 1 and "demo@example.test" not in turns[0]["body"]
    assert str(turns[0]["trace_id"]) == receipt["trace_id"]
    assert (
        len(await world.q("SELECT id FROM ingress_events WHERE trace_id=%s", receipt["trace_id"]))
        == 1
    )
    read = await world.admin.get(base + f"/utterances/{utterance}?client_id={world.tenants[0]}")
    assert read.status_code == 200 and read.json()["request_key"] == receipt["request_key"]
    for field in ("fingerprint", "user_id", "login_session_id", "transcript", "client_id"):
        assert field not in read.json()
    assert (
        len(
            await world.q(
                "SELECT action FROM console_audit WHERE action='voice.utterance.accepted'"
            )
        )
        == 1
    )


@requires_docker
@pytest.mark.asyncio
async def test_authority_csrf_owner_login_and_media_authentication(sessions: SessionWorld):
    world = sessions
    configure(world)
    session, path, body = await create(world)
    assert (await world.admin.post(path, json=body)).status_code == 403
    reader, _ = await world.member(role="read_only")
    assert (await world.mutate(reader, path, body)).status_code == 403
    staff, _ = await world.member()
    base = f"/console/v1/chat/voice/sessions/{session['id']}?client_id={world.tenants[0]}"
    assert (await staff.get(base)).status_code == 404
    assert (
        await world.admin.get(base.replace(world.tenants[0], world.tenants[1]))
    ).status_code == 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app), base_url="https://console.example.test"
    ) as anonymous:
        assert (await anonymous.get(base)).status_code == 401
        assert (
            await anonymous.get(
                base,
                headers={
                    "Authorization": "Bearer synthetic-read-only-machine-token-123456",
                    "X-Client-Id": world.tenants[0],
                },
            )
        ).status_code == 403
    fresh_login = await world.browser()
    from tests.test_console_sessions import ADMIN_PASSWORD

    assert (
        await world.mutate(
            fresh_login,
            "/console/v1/auth/login",
            {"email": "synthetic-admin@example.test", "password": ADMIN_PASSWORD},
        )
    ).status_code == 200
    assert (await fresh_login.get(base)).status_code == 404
    final = {"client_id": world.tenants[0], "transcript": "Synthetic question", "final": True}
    assert (
        await media(world, session["id"], str(uuid4()), final, signature="invalid")
    ).status_code == 401
    assert (
        await media(world, session["id"], str(uuid4()), final, timestamp=str(int(time.time()) - 60))
    ).status_code == 401
    assert (
        await media(world, session["id"], str(uuid4()), {**final, "final": False})
    ).status_code == 422
    assert (
        await media(world, session["id"], str(uuid4()), {**final, "client_id": world.tenants[1]})
    ).status_code == 404
    assert not await world.q(
        "SELECT id FROM console_chat_turns WHERE conversation_id=%s", session["conversation_id"]
    )


@requires_docker
@pytest.mark.asyncio
async def test_expiry_end_logout_and_membership_revoke(sessions: SessionWorld):
    world = sessions
    configure(world)
    session, _, _ = await create(world)
    final = {"client_id": world.tenants[0], "transcript": "Synthetic question", "final": True}
    base = f"/console/v1/chat/voice/sessions/{session['id']}"
    suffix = f"?client_id={world.tenants[0]}"
    assert (await world.mutate(world.admin, base + "/end" + suffix)).json()["status"] == "ended"
    assert (await world.mutate(world.admin, base + "/end" + suffix)).json()["status"] == "ended"
    assert (await media(world, session["id"], str(uuid4()), final)).status_code == 409
    assert (
        await media(world, session["id"], "", {"client_id": world.tenants[0]}, state=True)
    ).status_code == 409
    session, _, _ = await create(world)
    await world.q(
        "UPDATE console_voice_sessions SET created_at=now()-interval '20 minutes', "
        "expires_at=now()-interval '10 minutes' WHERE id=%s",
        session["id"],
    )
    assert (
        await world.admin.get(f"/console/v1/chat/voice/sessions/{session['id']}" + suffix)
    ).json()["status"] == "expired"
    assert (await media(world, session["id"], str(uuid4()), final)).status_code == 409
    staff, member = await world.member()
    session, _, _ = await create(world, staff)
    await world.q(
        "UPDATE console_memberships SET role='read_only' WHERE user_id=%s", member["user"]["id"]
    )
    assert (await media(world, session["id"], str(uuid4()), final)).status_code == 403
    session, _, _ = await create(world)
    assert (await world.mutate(world.admin, "/console/v1/auth/logout")).status_code == 204
    assert (await media(world, session["id"], str(uuid4()), final)).status_code == 403


@requires_docker
@pytest.mark.asyncio
async def test_receipt_failure_rolls_back_queue_and_tenant_rows_are_hidden(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
):
    world = sessions
    configure(world)
    session, _, _ = await create(world)
    original = db.q.fetch_one
    before_ingress = await world.q("SELECT count(*) AS count FROM ingress_events")

    async def fail(path, *args, **kwargs):
        if path == "console/voice_utterance_insert.sql":
            raise RuntimeError("synthetic receipt failure")
        return await original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(db.q, "fetch_one", fail)
        with pytest.raises(RuntimeError, match="synthetic receipt failure"):
            await media(
                world,
                session["id"],
                str(uuid4()),
                {"client_id": world.tenants[0], "transcript": "Synthetic", "final": True},
            )
    assert not await world.q(
        "SELECT id FROM console_chat_turns WHERE conversation_id=%s", session["conversation_id"]
    )
    assert not await world.q("SELECT session_id FROM console_voice_utterances")
    assert await world.q("SELECT count(*) AS count FROM ingress_events") == before_ingress
    assert not await world.q(
        "SELECT action FROM console_audit "
        "WHERE action IN ('chat.queued','voice.utterance.accepted')"
    )
    async with tx(world.pool, world.tenants[1]) as conn:
        assert (
            await db.q.fetch_one(
                "console/voice_session_get.sql", (world.tenants[0], UUID(session["id"])), conn=conn
            )
            is None
        )


@requires_docker
@pytest.mark.asyncio
async def test_voice_composite_foreign_keys_and_role_privileges(sessions: SessionWorld):
    world = sessions
    configure(world)
    session, path, body = await create(world)
    duplicates = await asyncio.gather(*(world.mutate(world.admin, path, body) for _ in range(2)))
    assert all(item.json()["id"] == session["id"] for item in duplicates)
    with pytest.raises(ForeignKeyViolation):
        await world.q(
            "INSERT INTO console_voice_sessions "
            "(id,client_id,user_id,login_session_id,conversation_id,request_key,"
            "language,expires_at) "
            "SELECT %s,%s,user_id,login_session_id,conversation_id,%s,language,expires_at "
            "FROM console_voice_sessions WHERE id=%s",
            uuid4(),
            world.tenants[1],
            uuid4(),
            session["id"],
        )
    async with world.pool.connection() as conn:
        assert (
            await db.q.fetch_one(
                "console/voice_session_get.sql", (world.tenants[0], session["id"]), conn=conn
            )
            is None
        )
    utterance = str(uuid4())
    final = {"client_id": world.tenants[0], "transcript": "Synthetic question", "final": True}
    assert (await media(world, session["id"], utterance, final)).status_code == 200
    with pytest.raises(ForeignKeyViolation):
        await world.q(
            "UPDATE console_voice_utterances SET trace_id=%s WHERE session_id=%s",
            uuid4(),
            session["id"],
        )
    async with tx(world.pool, world.tenants[0]) as conn:
        with pytest.raises(InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("UPDATE console_voice_utterances SET fingerprint='invalid'")
    privilege = await world.q(
        "SELECT has_table_privilege('csa_ro','console_voice_sessions','SELECT') AS allowed"
    )
    assert privilege == [{"allowed": False}]


@requires_docker
@pytest.mark.asyncio
async def test_revocation_waits_for_the_accepted_turn_transaction(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
):
    world = sessions
    configure(world)
    session, _, _ = await create(world)
    stored = await world.q(
        "SELECT login_session_id FROM console_voice_sessions WHERE id=%s", session["id"]
    )
    login = stored[0]["login_session_id"]
    entered, release = asyncio.Event(), asyncio.Event()
    original = db.q.execute

    async def hold(path, params=None, **kwargs):
        if (
            path == "console/audit.sql"
            and params is not None
            and params[2] == "voice.utterance.accepted"
        ):
            entered.set()
            await release.wait()
        return await original(path, params, **kwargs)

    monkeypatch.setattr(db.q, "execute", hold)
    utterance = str(uuid4())
    final = {"client_id": world.tenants[0], "transcript": "Synthetic question", "final": True}
    accepted = asyncio.create_task(media(world, session["id"], utterance, final))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        async with await psycopg.AsyncConnection.connect(world.owner_dsn, autocommit=True) as conn:
            await conn.execute("SET lock_timeout='100ms'")
            with pytest.raises(LockNotAvailable):
                await conn.execute(
                    "UPDATE console_sessions SET revoked_at=now() WHERE id=%s", (login,)
                )
    finally:
        release.set()
    assert (await accepted).status_code == 200
    await world.q("UPDATE console_sessions SET revoked_at=now() WHERE id=%s", login)
    assert (await media(world, session["id"], utterance, final)).status_code == 403
    assert (
        len(
            await world.q(
                "SELECT id FROM console_chat_turns WHERE conversation_id=%s",
                session["conversation_id"],
            )
        )
        == 1
    )
