"""Recorded speech: real isolated ownership/job persistence, synthetic HTTP audio."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from csa.console.voice import MAX_AUDIO_BYTES

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]
pytestmark = [requires_docker, pytest.mark.asyncio]
AUDIO = b"synthetic-audio-not-real-speech"


async def setup(world: SessionWorld) -> tuple[str, str]:
    world.app.state.voice_endpoint = "https://speech.example.test/transcriptions"
    world.app.state.voice_model = "synthetic-whisper"
    world.app.state.voice_api_key = "synthetic-secret"
    response = await world.mutate(
        world.admin,
        f"/console/v1/chat/conversations?client_id={world.tenants[0]}",
        {"request_key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    conversation = response.json()["id"]
    return (
        conversation,
        f"/console/v1/chat/conversations/{conversation}/voice/{uuid4()}?client_id={world.tenants[0]}",
    )


def provider(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "csa.speech.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


async def upload(world: SessionWorld, path: str, body: bytes = AUDIO) -> httpx.Response:
    return await world.admin.post(
        path,
        content=body,
        headers={**world.headers(world.admin), "Content-Type": "audio/webm;codecs=opus"},
    )


async def test_review_dedupe_conflict_and_expiry(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = sessions
    conversation, path = await setup(world)
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-secret"
        assert AUDIO in request.content
        return httpx.Response(200, json={"text": "Synthetic opening hours?"})

    provider(monkeypatch, handle)
    first = await upload(world, path)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert first.json()["transcript"] == "Synthetic opening hours?"
    assert (await upload(world, path)).json() == first.json()
    assert len(calls) == 1
    assert (await upload(world, path, b"different audio")).status_code == 409
    assert (await world.admin.get(path)).json() == first.json()
    # A transcript is only a draft, never a chat turn or delivery.
    assert not await world.q(
        "SELECT id FROM console_chat_turns WHERE conversation_id=%s", conversation
    )
    await world.q(
        "UPDATE console_voice_jobs SET created_at=now()-interval '2 hours' WHERE id=%s",
        first.json()["id"],
    )
    expired = (await world.admin.get(path)).json()
    assert expired["status"] == "expired" and expired["transcript"] is None
    stored = await world.q(
        "SELECT transcript FROM console_voice_jobs WHERE id=%s", first.json()["id"]
    )
    assert stored == [{"transcript": None}]


async def test_authority_and_csrf(sessions: SessionWorld) -> None:
    world = sessions
    _, path = await setup(world)
    anonymous = await world.browser()
    staff, _ = await world.member()
    reader, _ = await world.member(role="read_only")
    other, _ = await world.member(tenant=world.tenants[1])
    assert (await anonymous.post(path)).status_code == 401
    assert (await world.admin.post(path, content=AUDIO)).status_code == 403
    for client in [staff, other]:
        result = await client.post(
            path, content=AUDIO, headers={**world.headers(client), "Content-Type": "audio/webm"}
        )
        assert result.status_code in {403, 404}
    assert (
        await reader.post(path, content=AUDIO, headers=world.headers(reader))
    ).status_code == 403
    assert (await staff.get(path)).status_code == 404
    assert (
        await world.admin.post(
            path.replace(world.tenants[0], world.tenants[1]),
            content=AUDIO,
            headers={**world.headers(world.admin), "Content-Type": "audio/webm"},
        )
    ).status_code == 404


async def test_limits_disabled_and_stale_processing(sessions: SessionWorld) -> None:
    world = sessions
    _, path = await setup(world)
    assert (await upload(world, path, b"")).status_code == 422
    assert (await upload(world, path, b"x" * (MAX_AUDIO_BYTES + 1))).status_code == 413
    assert (
        await world.admin.post(
            path,
            content=AUDIO,
            headers={**world.headers(world.admin), "Content-Type": "text/plain"},
        )
    ).status_code == 415
    world.app.state.voice_endpoint = None
    assert not (await world.admin.get("/console/v1/chat/voice")).json()["enabled"]
    assert (await upload(world, path)).status_code == 503


@pytest.mark.parametrize(
    "result,expected", [("timeout", "timeout"), ("malformed", "failed"), ("error", "failed")]
)
async def test_provider_failures_are_durable(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch, result: str, expected: str
) -> None:
    world = sessions
    _, path = await setup(world)
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if result == "timeout":
            raise httpx.ReadTimeout("synthetic secret must never be returned")
        if result == "malformed":
            return httpx.Response(200, json={"text": " "})
        return httpx.Response(500, text="synthetic upstream private payload")

    provider(monkeypatch, handle)
    response = await upload(world, path)
    assert response.status_code == 200
    assert response.json()["status"] == expected
    assert "synthetic" not in response.text
    assert (await upload(world, path)).json() == response.json()
    assert len(calls) == 1


async def test_concurrent_replay_stale_job_and_rls(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from csa.core import db
    from csa.core.tenancy import tx

    world = sessions
    _, path = await setup(world)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"text": "Synthetic draft"})

    provider(monkeypatch, handle)
    first = asyncio.create_task(upload(world, path))
    await asyncio.wait_for(entered.wait(), timeout=10)
    try:
        second = await upload(world, path)
        assert second.json()["status"] == "processing"
        assert len(calls) == 1
    finally:
        release.set()
    result = await first
    job = result.json()
    async with tx(world.pool, world.tenants[1]) as conn:
        hidden = await db.q.fetch_one(
            "console/voice_get.sql",
            (world.tenants[0], world.admin_id, path.split("/")[5], job["request_key"]),
            conn=conn,
        )
    assert hidden is None
    await world.q(
        "UPDATE console_voice_jobs SET status='processing', transcript=NULL, "
        "created_at=now()-interval '2 minutes' WHERE id=%s",
        job["id"],
    )
    assert (await world.admin.get(path)).json()["status"] == "timeout"
