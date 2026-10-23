"""Bounded recorded speech to reviewable text, private to a sandbox participant."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

from csa.console.access import tenant
from csa.console.chat import _thread
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.kb_uploads import audit, human
from csa.core import db
from csa.core.tenancy import tx
from csa.speech import MAX_AUDIO_BYTES, MIMES, SpeechTranscriber, TranscriptionError


class VoiceConfig(Contract):
    enabled: bool
    max_bytes: int = MAX_AUDIO_BYTES


class VoiceJob(Contract):
    id: UUID
    request_key: UUID
    status: Literal["processing", "completed", "failed", "timeout", "expired"]
    transcript: str | None = None
    error_code: str | None = None


def build_voice_router() -> APIRouter:
    router = APIRouter(prefix="/chat", dependencies=[Depends(human)])

    @router.get("/voice", response_model=VoiceConfig)
    async def config(request: Request) -> VoiceConfig:
        return VoiceConfig(enabled=getattr(request.app.state, "voice_endpoint", None) is not None)

    async def fetch(
        request: Request, client_id: UUID, actor: ConsoleActor, conversation_id: UUID, key: UUID
    ) -> VoiceJob:
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await _thread(conn, client_id, actor, conversation_id)
            await db.q.execute("console/voice_expire.sql", (client_id, actor.user_id), conn=conn)
            row = await db.q.fetch_one(
                "console/voice_get.sql", (client_id, actor.user_id, conversation_id, key), conn=conn
            )
        if row is None:
            raise HTTPException(404, "recording not found")
        return VoiceJob.model_validate({k: row[k] for k in VoiceJob.model_fields})

    @router.get("/conversations/{conversation_id}/voice/{key}", response_model=VoiceJob)
    async def status(
        conversation_id: UUID,
        key: UUID,
        request: Request,
        actor: Annotated[ConsoleActor, Depends(human)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> VoiceJob:
        return await fetch(request, client_id, actor, conversation_id, key)

    @router.post("/conversations/{conversation_id}/voice/{key}", response_model=VoiceJob)
    async def submit(
        conversation_id: UUID,
        key: UUID,
        request: Request,
        actor: Annotated[ConsoleActor, Depends(human)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> VoiceJob:
        if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}:
            raise HTTPException(403, "write permission required")
        endpoint = getattr(request.app.state, "voice_endpoint", None)
        if endpoint is None:
            raise HTTPException(503, "recorded voice is not configured")
        mime = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if mime not in MIMES:
            raise HTTPException(415, "unsupported recording format")
        async with tx(request.app.state.pool, str(client_id)) as conn:
            thread = await _thread(conn, client_id, actor, conversation_id)
            if thread["archived_at"] is not None:
                raise HTTPException(409, "sandbox conversation is archived")
        audio = bytearray()
        try:
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
                        raise HTTPException(413, "recording exceeds 8 MB")
                    audio.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, "recording upload timed out") from None
        if not audio:
            raise HTTPException(422, "recording is empty")
        fingerprint = hashlib.sha256(mime.encode() + b"\0" + audio).hexdigest()
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await db.q.execute("console/voice_expire.sql", (client_id, actor.user_id), conn=conn)
            inserted = await db.q.fetch_one(
                "console/voice_insert.sql",
                (uuid4(), client_id, actor.user_id, conversation_id, key, fingerprint),
                conn=conn,
            )
            if inserted is not None:
                await audit(conn, actor, client_id, "voice.submitted", str(inserted["id"]))
            if inserted is None:
                existing = await db.q.fetch_one(
                    "console/voice_get.sql",
                    (client_id, actor.user_id, conversation_id, key),
                    conn=conn,
                )
                if existing is None or existing["fingerprint"] != fingerprint:
                    raise HTTPException(409, "recording key already used for different audio")
        if inserted is None:
            return await fetch(request, client_id, actor, conversation_id, key)
        outcome, text, error = "failed", None, "transcription_failed"
        try:
            speech = SpeechTranscriber(
                endpoint,
                request.app.state.voice_model,
                getattr(request.app.state, "voice_api_key", None),
            )
            text = await speech.transcribe(bytes(audio), mime)
            outcome, error = "completed", None
        except TranscriptionError as exc:
            outcome = "timeout" if exc.code == "transcription_timeout" else "failed"
            text, error = None, exc.code
        finally:
            audio.clear()
        async with tx(request.app.state.pool, str(client_id)) as conn:
            finished = await db.q.fetch_one(
                "console/voice_finish.sql",
                (outcome, text, error, inserted["id"], client_id),
                conn=conn,
            )
            if finished is not None:
                await audit(conn, actor, client_id, "voice." + outcome, str(inserted["id"]))
        return await fetch(request, client_id, actor, conversation_id, key)

    return router
