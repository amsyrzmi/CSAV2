"""Session-bound media authority and atomic handoff to the ordinary CSA queue."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from livekit import api
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.console.chat import SendTurn, _owned_thread, enqueue_sandbox_turn
from csa.console.contracts import ConsoleActor
from csa.core.db import q
from csa.voice.contracts import (
    CreateVoiceSession,
    FinalUtterance,
    VoiceAuthority,
    VoiceConnection,
    VoiceReceipt,
    VoiceReceiptRecord,
    VoiceSession,
    VoiceSessionConfig,
    VoiceSessionRecord,
)


def public_session(record: VoiceSessionRecord) -> VoiceSession:
    fields = {name: getattr(record, name) for name in VoiceSession.model_fields}
    if record.status == "active" and record.expires_at <= datetime.now(UTC):
        fields["status"] = "expired"
    return VoiceSession.model_validate(fields)


async def _audit(conn: AsyncConnection[Any], user: UUID, tenant: UUID, action: str, target: UUID):
    await q.execute("console/audit.sql", (user, tenant, action, str(target), Jsonb({})), conn=conn)


class VoiceSessionService:
    def __init__(self, config: VoiceSessionConfig):
        self.config = config

    async def _actor(
        self, conn: AsyncConnection[Any], login: UUID, user: UUID, tenant: UUID
    ) -> datetime:
        row = await q.fetch_one(
            "console/voice_actor_lock.sql",
            (login, user, tenant, self.config.session_idle_seconds),
            conn=conn,
            model=VoiceAuthority,
        )
        if row is None or row.expires_at is None:
            raise HTTPException(403, "voice session authority is unavailable")
        return row.expires_at

    async def create(
        self,
        conn: AsyncConnection[Any],
        tenant: UUID,
        actor: ConsoleActor,
        body: CreateVoiceSession,
    ) -> VoiceSession:
        await q.execute(
            "console/voice_session_lock.sql",
            (f"voice:{tenant}:{actor.user_id}:{actor.session_id}:{body.request_key}",),
            conn=conn,
        )
        old = await q.fetch_one(
            "console/voice_session_by_key.sql",
            (tenant, actor.user_id, actor.session_id, body.request_key),
            conn=conn,
            model=VoiceSessionRecord,
        )
        authority_expiry = await self._actor(conn, actor.session_id, actor.user_id, tenant)
        if old is not None:
            if old.conversation_id != body.conversation_id or old.language != body.language:
                raise HTTPException(409, "voice session key was used with different settings")
            return public_session(old)
        thread = await _owned_thread(conn, tenant, actor.user_id, body.conversation_id)
        if thread["archived_at"] is not None:
            raise HTTPException(409, "sandbox conversation is archived")
        record = await q.fetch_one(
            "console/voice_session_insert.sql",
            (
                uuid4(),
                tenant,
                actor.user_id,
                actor.session_id,
                body.conversation_id,
                body.request_key,
                body.language,
                authority_expiry,
            ),
            conn=conn,
            model=VoiceSessionRecord,
        )
        if record is None:
            raise RuntimeError("voice session was not persisted")
        await _audit(conn, actor.user_id, tenant, "voice.session.created", record.id)
        return public_session(record)

    async def lock(
        self,
        conn: AsyncConnection[Any],
        tenant: UUID,
        session_id: UUID,
        actor: ConsoleActor | None = None,
        *,
        active: bool = False,
    ) -> VoiceSessionRecord:
        record = await q.fetch_one(
            "console/voice_session_get.sql",
            (tenant, session_id),
            conn=conn,
            model=VoiceSessionRecord,
        )
        if record is None or (
            actor is not None
            and (record.user_id != actor.user_id or record.login_session_id != actor.session_id)
        ):
            raise HTTPException(404, "voice session not found")
        await self._actor(conn, record.login_session_id, record.user_id, tenant)
        if active:
            if public_session(record).status != "active":
                raise HTTPException(409, "voice session is no longer active")
            thread = await _owned_thread(conn, tenant, record.user_id, record.conversation_id)
            if thread["archived_at"] is not None:
                raise HTTPException(409, "sandbox conversation is archived")
        return record

    async def end(
        self, conn: AsyncConnection[Any], tenant: UUID, session_id: UUID, actor: ConsoleActor
    ):
        record = await self.lock(conn, tenant, session_id, actor)
        changed = await q.fetch_one(
            "console/voice_session_end.sql", (tenant, session_id), conn=conn
        )
        if changed:
            await _audit(conn, record.user_id, tenant, "voice.session.ended", session_id)
        return public_session(record.model_copy(update={"status": "ended"}))

    async def receipt(
        self, conn: AsyncConnection[Any], tenant: UUID, session_id: UUID, utterance_id: UUID
    ) -> VoiceReceiptRecord | None:
        return await q.fetch_one(
            "console/voice_utterance_get.sql",
            (tenant, session_id, utterance_id),
            conn=conn,
            model=VoiceReceiptRecord,
        )

    async def finalize(
        self, conn: AsyncConnection[Any], session_id: UUID, utterance_id: UUID, body: FinalUtterance
    ) -> VoiceReceipt:
        record = await self.lock(conn, body.client_id, session_id, active=True)
        fingerprint = hashlib.sha256(body.transcript.encode()).hexdigest()
        old = await self.receipt(conn, body.client_id, session_id, utterance_id)
        if old is not None:
            if old.fingerprint != fingerprint:
                raise HTTPException(409, "utterance already accepted with different text")
            return VoiceReceipt(
                **{
                    name: getattr(old, name)
                    for name in VoiceReceipt.model_fields
                    if name != "duplicate"
                },
                duplicate=True,
            )
        request_key = uuid5(session_id, str(utterance_id))
        turn = await enqueue_sandbox_turn(
            conn,
            body.client_id,
            record.user_id,
            record.conversation_id,
            SendTurn(request_key=request_key, body=body.transcript),
        )
        receipt = await q.fetch_one(
            "console/voice_utterance_insert.sql",
            (
                session_id,
                utterance_id,
                body.client_id,
                record.user_id,
                record.conversation_id,
                request_key,
                fingerprint,
                turn.id,
                turn.trace_id,
            ),
            conn=conn,
            model=VoiceReceiptRecord,
        )
        if receipt is None:
            raise RuntimeError("voice utterance receipt was not persisted")
        await _audit(conn, record.user_id, body.client_id, "voice.utterance.accepted", utterance_id)
        return VoiceReceipt(**{name: getattr(receipt, name) for name in VoiceReceipt.model_fields})

    def connection(self, record: VoiceSessionRecord) -> VoiceConnection:
        now = datetime.now(UTC)
        expires = min(now + timedelta(seconds=60), record.expires_at)
        if record.status != "active" or expires <= now:
            raise HTTPException(409, "voice session is no longer active")
        room, participant = f"csa-voice-{record.id}", f"voice-user-{record.id}"
        token = (
            api.AccessToken(
                self.config.api_key.get_secret_value(), self.config.api_secret.get_secret_value()
            )
            .with_identity(participant)
            .with_ttl(expires - now)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=False,
                    can_publish_sources=["microphone"],
                    can_update_own_metadata=False,
                    can_manage_agent_session=False,
                )
            )
            .with_room_config(
                api.RoomConfiguration(
                    max_participants=2,
                    empty_timeout=30,
                    departure_timeout=10,
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name="csa-voice",
                            metadata=json.dumps(
                                {
                                    "session_id": str(record.id),
                                    "client_id": str(record.client_id),
                                }
                            ),
                        )
                    ],
                )
            )
            .to_jwt()
        )
        return VoiceConnection(
            server_url=self.config.server_url,
            room=room,
            participant_identity=participant,
            token=token,
            expires_at=expires,
        )
