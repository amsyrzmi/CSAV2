"""Authenticated voice sessions and separately authenticated media callbacks."""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from csa.console.access import tenant
from csa.console.auth import _no_store
from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import human
from csa.core.tenancy import tx
from csa.voice.contracts import (
    CreateVoiceSession,
    FinalUtterance,
    MediaSessionLookup,
    VoiceConnection,
    VoiceContract,
    VoiceReceipt,
    VoiceSession,
    VoiceSessionConfig,
)
from csa.voice.security import valid_media_signature
from csa.voice.service import VoiceSessionService, public_session


def service(request: Request) -> VoiceSessionService:
    config = getattr(request.app.state, "voice_sessions_config", None)
    if config is None:
        raise HTTPException(503, "interactive voice is not configured")
    if not isinstance(config, VoiceSessionConfig):
        raise RuntimeError("invalid interactive voice configuration")
    return VoiceSessionService(config)


Actor = Annotated[ConsoleActor, Depends(human)]
Tenant = Annotated[UUID, Depends(tenant)]
Service = Annotated[VoiceSessionService, Depends(service)]


def build_voice_sessions_router() -> APIRouter:
    router = APIRouter(
        prefix="/chat/voice/sessions", dependencies=[Depends(human), Depends(_no_store)]
    )

    @router.post("", response_model=VoiceSession)
    async def create(
        body: CreateVoiceSession, request: Request, actor: Actor, client_id: Tenant, voice: Service
    ):
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await voice.create(conn, client_id, actor, body)

    @router.get("/{session_id}", response_model=VoiceSession)
    async def get(
        session_id: UUID, request: Request, actor: Actor, client_id: Tenant, voice: Service
    ):
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return public_session(await voice.lock(conn, client_id, session_id, actor))

    @router.post("/{session_id}/end", response_model=VoiceSession)
    async def end(
        session_id: UUID, request: Request, actor: Actor, client_id: Tenant, voice: Service
    ):
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await voice.end(conn, client_id, session_id, actor)

    @router.post("/{session_id}/connection", response_model=VoiceConnection)
    async def connection(
        session_id: UUID, request: Request, actor: Actor, client_id: Tenant, voice: Service
    ):
        async with tx(request.app.state.pool, str(client_id)) as conn:
            record = await voice.lock(conn, client_id, session_id, actor, active=True)
            return voice.connection(record)

    @router.get("/{session_id}/utterances/{utterance_id}", response_model=VoiceReceipt)
    async def receipt(
        session_id: UUID,
        utterance_id: UUID,
        request: Request,
        actor: Actor,
        client_id: Tenant,
        voice: Service,
    ):
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await voice.lock(conn, client_id, session_id, actor)
            record = await voice.receipt(conn, client_id, session_id, utterance_id)
            if record is None:
                raise HTTPException(404, "voice utterance not found")
            return VoiceReceipt(
                **{name: getattr(record, name) for name in VoiceReceipt.model_fields}
            )

    return router


async def media_payload[Payload: VoiceContract](
    request: Request, voice: VoiceSessionService, model: type[Payload]
) -> Payload:
    if request.cookies or request.headers.get("authorization"):
        raise HTTPException(403, "media authentication required")
    data = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 32768:
                    raise HTTPException(413, "voice message is too large")
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "voice message upload timed out") from None
    if not valid_media_signature(
        voice.config.media_secret.get_secret_value(),
        request.url.path,
        request.headers.get("x-voice-timestamp", ""),
        bytes(data),
        request.headers.get("x-voice-signature", ""),
    ):
        raise HTTPException(401, "invalid media authentication")
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise HTTPException(422, "invalid voice media message") from None


def build_voice_media_router() -> APIRouter:
    router = APIRouter(prefix="/internal/voice", dependencies=[Depends(_no_store)])

    @router.post("/sessions/{session_id}/state", response_model=VoiceSession)
    async def state(session_id: UUID, request: Request, voice: Service):
        body = await media_payload(request, voice, MediaSessionLookup)
        async with tx(request.app.state.pool, str(body.client_id)) as conn:
            return public_session(await voice.lock(conn, body.client_id, session_id, active=True))

    @router.post("/sessions/{session_id}/utterances/{utterance_id}", response_model=VoiceReceipt)
    async def finalize(session_id: UUID, utterance_id: UUID, request: Request, voice: Service):
        body = await media_payload(request, voice, FinalUtterance)
        async with tx(request.app.state.pool, str(body.client_id)) as conn:
            return await voice.finalize(conn, session_id, utterance_id, body)

    return router
