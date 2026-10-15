"""Durable ingress worker and checkpoint-retention process (LG03/LG10)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import psycopg
import structlog
from csa.agents.kb.embedder import DefaultKbEmbedder
from csa.agents.kb.ingest_jobs import KbIngestConfig, KbIngestProcessor, kb_ingest_loop
from csa.contracts.envelope import Channel, Envelope
from csa.core import db
from csa.core.langfuse import LangfuseSink
from csa.core.liveness import WORKER_ALIVE_PATH, liveness_loop
from csa.core.pii import redact
from csa.core.settings import get_settings
from csa.core.tenancy import tx
from csa.decide.gemini import GeminiClient
from csa.decide.groq import GroqClient
from csa.decide.jev import JevClient, JevProvider
from csa.graph.coordinator import CoordinatorRuntime, build_runtime, process_turn
from csa.graph.events import PostgresEventSink
from csa.graph.retention import CheckpointPool, retention_loop
from csa.graph.saver import build_checkpoint_pool, build_saver
from csa.jobs.runner import JobRunner, jobs_loop
from csa.notify.notifier import notify
from csa.turn.deps import CAPABILITIES, PostgresTurnDependencies, build_llm_provider
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, ValidationError

logger = structlog.get_logger(__name__)

MAX_INGRESS_ATTEMPTS = 5
INGRESS_REAPER_INTERVAL_SECONDS = 60
INGRESS_STALE_SECONDS = 180
REAPER_BATCH_SIZE = 100


class ClaimedIngress(BaseModel):
    """Typed queue row returned by the ingress claim query."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    client_id: UUID
    endpoint_id: UUID
    trace_id: UUID
    raw: dict[str, Any]
    attempts: int


class IngressWorker:
    """Claim, serialize, echo, and finalize durable ingress events."""

    def __init__(
        self,
        owner_pool: AsyncConnectionPool,
        app_pool: AsyncConnectionPool,
        *,
        lock_conninfo: str,
        coordinator: CoordinatorRuntime | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._owner_pool = owner_pool
        self._app_pool = app_pool
        self._lock_conninfo = lock_conninfo
        self._next_reap_at = 0.0

    async def do_work(self) -> bool:
        """Run one queue action and periodically reclaim abandoned events."""
        now = time.monotonic()
        reaped = 0
        if now >= self._next_reap_at:
            reaped = await self.reap()
            self._next_reap_at = now + INGRESS_REAPER_INTERVAL_SECONDS
        return await self.process_once() or reaped > 0

    async def process_once(self) -> bool:
        """Claim and process one event; return whether queue work was found."""
        async with self._owner_pool.connection() as conn:
            row = await db.q.fetch_one("worker/claim_ingress.sql", conn=conn)
        if row is None:
            return False
        item = ClaimedIngress.model_validate(row)

        try:
            envelope = self._parse_envelope(item)
        except (KeyError, TypeError, ValidationError, ValueError) as exc:
            await self._record_failure(item, exc)
            logger.error(
                "ingress_envelope_invalid",
                event_id=str(item.id),
                trace_id=str(item.trace_id),
                error_type=type(exc).__name__,
                attempt=item.attempts,
            )
            return True

        lock_key = (
            f"{item.client_id}:{envelope.channel.value}:{envelope.conversation.channel_thread}"
        )
        lock_conn = await psycopg.AsyncConnection.connect(
            self._lock_conninfo,
            autocommit=True,
        )
        try:
            async with lock_conn.cursor() as cur:
                await cur.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
                row = await cur.fetchone()
                acquired = bool(row and row[0])
            if not acquired:
                await self._requeue_locked(item)
                return True

            try:
                await self._complete_turn(item, envelope)
            except Exception as exc:
                await self._record_failure(item, exc)
                logger.error(
                    "ingress_turn_failed",
                    event_id=str(item.id),
                    trace_id=str(item.trace_id),
                    error_type=type(exc).__name__,
                    attempt=item.attempts,
                )
        finally:
            try:
                async with lock_conn.cursor() as cur:
                    await cur.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (lock_key,),
                    )
            finally:
                await lock_conn.close()
        return True

    async def _complete_turn(self, item: ClaimedIngress, envelope: Envelope) -> None:
        if envelope.client_id != item.client_id or envelope.trace_id != item.trace_id:
            raise ValueError("ingress envelope tenant or trace does not match its queue row")
        if envelope.channel != Channel.TELEGRAM:
            raise ValueError(f"no worker turn handler for channel {envelope.channel.value}")
        if envelope.conversation.id is None:
            raise ValueError("ingress envelope has no conversation id")

        if self._coordinator is not None:
            await self._run_coordinator_turn(item, envelope)
            return

        async with tx(self._app_pool, str(item.client_id)) as conn:
            if envelope.callback is None:
                await self._echo_message(conn, item, envelope)
            completed = await db.q.fetch_one(
                "worker/complete_ingress.sql",
                (item.id, item.client_id),
                conn=conn,
            )
            if completed is None:
                raise RuntimeError("claimed ingress event is no longer dispatched")

    async def _run_coordinator_turn(self, item: ClaimedIngress, envelope: Envelope) -> None:
        runtime = self._coordinator
        if runtime is None:
            raise RuntimeError("coordinator runtime is not configured")
        if envelope.callback is None:
            async with tx(self._app_pool, str(item.client_id)) as conn:
                await self._persist_inbound(conn, item, envelope)
        await process_turn(
            envelope,
            runtime.deps,
            checkpointer=runtime.checkpointer,
            graph=runtime.graph,
        )
        async with tx(self._app_pool, str(item.client_id)) as conn:
            completed = await db.q.fetch_one(
                "worker/complete_ingress.sql",
                (item.id, item.client_id),
                conn=conn,
            )
            if completed is None:
                raise RuntimeError("claimed ingress event is no longer dispatched")

    async def _persist_inbound(
        self,
        conn: psycopg.AsyncConnection[Any],
        item: ClaimedIngress,
        envelope: Envelope,
    ) -> None:
        conversation_id = envelope.conversation.id
        contact_id = envelope.contact.id
        if conversation_id is None or contact_id is None:
            raise ValueError("ingress envelope has no resolved contact or conversation")

        inbound_key = envelope.idempotency_key
        body = envelope.message.body_redacted
        attachments = [item.model_dump(mode="json") for item in envelope.message.attachments]
        inbound = await db.q.fetch_one(
            "worker/insert_message.sql",
            (
                item.client_id,
                conversation_id,
                "inbound",
                "contact",
                body,
                body,
                Jsonb(attachments),
                bool(attachments),
                inbound_key,
                item.trace_id,
            ),
            conn=conn,
        )
        if inbound is None:
            inbound = await db.q.fetch_one(
                "worker/get_message.sql",
                (item.client_id, inbound_key),
                conn=conn,
            )
            if inbound is None or inbound["conversation_id"] != conversation_id:
                raise RuntimeError("inbound message idempotency key conflicts with another turn")
        else:
            incremented = await db.q.fetch_one(
                "worker/increment_turn_count.sql",
                (conversation_id, item.client_id),
                conn=conn,
            )
            if incremented is None:
                raise RuntimeError("conversation disappeared while processing its turn")

    async def _echo_message(
        self,
        conn: psycopg.AsyncConnection[Any],
        item: ClaimedIngress,
        envelope: Envelope,
    ) -> None:
        await self._persist_inbound(conn, item, envelope)
        conversation_id = envelope.conversation.id
        body = envelope.message.body_redacted
        attachments = [a.model_dump(mode="json") for a in envelope.message.attachments]
        inbound_key = envelope.idempotency_key

        if body.strip():
            reply = f"Echo: {body.strip()}"
        elif attachments:
            reply = "Echo: attachment received."
        else:
            reply = "Echo: message received."
        if len(reply) > 4096:
            reply = f"{reply[:4093]}..."

        outbound_key = f"{inbound_key}:echo"
        outbound = await db.q.fetch_one(
            "worker/insert_message.sql",
            (
                item.client_id,
                conversation_id,
                "outbound",
                "assistant",
                reply,
                redact(reply),
                Jsonb([]),
                False,
                outbound_key,
                item.trace_id,
            ),
            conn=conn,
        )
        if outbound is None:
            outbound = await db.q.fetch_one(
                "worker/get_message.sql",
                (item.client_id, outbound_key),
                conn=conn,
            )
            if outbound is None or outbound["conversation_id"] != conversation_id:
                raise RuntimeError("outbound message idempotency key conflicts with another turn")

        await db.q.fetch_one(
            "worker/insert_delivery.sql",
            (
                item.client_id,
                item.endpoint_id,
                item.trace_id,
                conversation_id,
                outbound["id"],
            ),
            conn=conn,
        )

    async def _requeue_locked(self, item: ClaimedIngress) -> None:
        async with self._owner_pool.connection() as conn:
            updated = await db.q.fetch_one(
                "worker/requeue_locked_ingress.sql",
                (item.id, item.client_id),
                conn=conn,
            )
            if updated is None:
                raise RuntimeError("could not requeue ingress event after lock contention")

    async def _record_failure(self, item: ClaimedIngress, error: Exception) -> None:
        dead = item.attempts >= MAX_INGRESS_ATTEMPTS
        retry_at = datetime.now(UTC) + timedelta(seconds=min(300, 2 ** max(item.attempts - 1, 0)))
        error_type = type(error).__name__
        async with self._owner_pool.connection() as conn:
            async with conn.transaction():
                updated = await db.q.fetch_one(
                    "worker/fail_ingress.sql",
                    (
                        "dead" if dead else "failed",
                        retry_at,
                        error_type,
                        item.id,
                        item.client_id,
                    ),
                    conn=conn,
                )
                if updated is None:
                    raise RuntimeError("could not persist failed ingress event")
                if dead:
                    await db.q.execute(
                        "worker/insert_dlq.sql",
                        (
                            item.client_id,
                            item.trace_id,
                            "turn",
                            Jsonb(item.raw),
                            Jsonb({"error_type": error_type, "retryable": False}),
                        ),
                        conn=conn,
                    )
                    await notify(
                        conn,
                        client_id=item.client_id,
                        event="dlq",
                        priority="high",
                        payload={
                            "stage": "turn",
                            "reason": error_type,
                            "trace_id": str(item.trace_id),
                        },
                        dedupe_key=f"turn:{item.id}",
                    )

    async def reap(self) -> int:
        """Requeue stale/eligible failed turns and dead-letter exhausted events."""
        reaped = 0
        async with self._owner_pool.connection() as conn:
            async with conn.transaction():
                candidates = await db.q.fetch_all(
                    "worker/reap_candidates.sql",
                    (INGRESS_STALE_SECONDS, REAPER_BATCH_SIZE),
                    conn=conn,
                )
                for item in candidates:
                    if item["attempts"] >= MAX_INGRESS_ATTEMPTS:
                        if item["raw"] is None:
                            raise RuntimeError(
                                "cannot dead-letter ingress without its raw envelope"
                            )
                        updated = await db.q.fetch_one(
                            "worker/dead_ingress.sql",
                            (
                                item["last_error"] or "maximum ingress attempts exceeded",
                                item["id"],
                                item["client_id"],
                                item["status"],
                            ),
                            conn=conn,
                        )
                        if updated is None:
                            continue
                        await db.q.execute(
                            "worker/insert_dlq.sql",
                            (
                                item["client_id"],
                                item["trace_id"],
                                "turn",
                                Jsonb(item["raw"]),
                                Jsonb(
                                    {
                                        "error_type": item["last_error"]
                                        or "maximum ingress attempts exceeded",
                                        "retryable": False,
                                    }
                                ),
                            ),
                            conn=conn,
                        )
                        await notify(
                            conn,
                            client_id=item["client_id"],
                            event="dlq",
                            priority="high",
                            payload={
                                "stage": "turn",
                                "reason": item["last_error"] or "maximum ingress attempts exceeded",
                                "trace_id": str(item["trace_id"]),
                            },
                            dedupe_key=f"turn:{item['id']}",
                        )
                    else:
                        updated = await db.q.fetch_one(
                            "worker/requeue_ingress.sql",
                            (
                                "stale ingress lease reclaimed"
                                if item["status"] == "dispatched"
                                else item["last_error"],
                                item["id"],
                                item["client_id"],
                                item["status"],
                            ),
                            conn=conn,
                        )
                        if updated is None:
                            continue
                    reaped += 1
        return reaped

    @staticmethod
    def _parse_envelope(item: ClaimedIngress) -> Envelope:
        raw_envelope = item.raw.get("envelope")
        if not isinstance(raw_envelope, dict):
            raise ValueError("ingress event has no valid envelope")
        return Envelope.model_validate(raw_envelope)


def register_retention_task(
    checkpoint_pool: CheckpointPool,
    owner_dsn: str,
    *,
    retention_days: int,
) -> asyncio.Task[None]:
    """Register periodic checkpoint retention on the worker process."""
    return asyncio.create_task(
        retention_loop(
            checkpoint_pool,
            owner_dsn,
            retention_days=retention_days,
        )
    )


async def main() -> None:  # pragma: no cover - process entrypoint
    """Start queue/reaper, checkpoint retention, and the liveness heartbeat."""
    settings = get_settings()
    owner_dsn = str(settings.LG_DATABASE_URL)
    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(str(settings.DATABASE_URL), max_size=8)
    checkpoint_pool = build_checkpoint_pool(owner_dsn)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    await checkpoint_pool.open(wait=True)
    llm_http = httpx.AsyncClient()
    coordinator: CoordinatorRuntime | None = None
    if settings.TURN_ENGINE == "coordinator":
        compose_fallback = (
            GeminiClient(
                settings.GOOGLE_AI_API_KEY.get_secret_value(),
                settings.GOOGLE_AI_MODEL,
                llm_http,
            )
            if settings.GOOGLE_AI_API_KEY is not None
            else None
        )
        llm = build_llm_provider(
            GroqClient(
                settings.GROQ_API_KEY.get_secret_value(),
                settings.GROQ_MODEL,
                llm_http,
            ),
            compose_fallback=compose_fallback,
        )
        jev = (
            JevProvider(
                JevClient(settings.TYPESAFE_API_KEY.get_secret_value(), llm_http),
                capabilities=CAPABILITIES,
            )
            if settings.TYPESAFE_API_KEY is not None
            else None
        )
        langfuse_sink = (
            LangfuseSink(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                base_url=settings.LANGFUSE_BASE_URL or "https://cloud.langfuse.com",
                http_client=llm_http,
            )
            if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY
            else None
        )
        coordinator = build_runtime(
            PostgresTurnDependencies(app_pool, llm, jev),
            build_saver(checkpoint_pool),
            PostgresEventSink(app_pool, langfuse_sink=langfuse_sink),
        )
    worker = IngressWorker(
        owner_pool,
        app_pool,
        lock_conninfo=owner_dsn,
        coordinator=coordinator,
    )
    retention = register_retention_task(
        checkpoint_pool,
        owner_dsn,
        retention_days=settings.CHECKPOINT_RETENTION_DAYS,
    )
    job_runner = JobRunner(
        owner_pool,
        app_pool,
        lock_conninfo=owner_dsn,
    )
    jobs_task = asyncio.create_task(jobs_loop(job_runner))
    background = [jobs_task, retention]
    if settings.KB_INGEST_ENABLED:
        assert settings.KB_UPLOAD_ROOT is not None
        kb_processor = KbIngestProcessor(
            owner_pool,
            app_pool,
            embedder=DefaultKbEmbedder.from_settings(settings),
            config=KbIngestConfig(
                upload_root=settings.KB_UPLOAD_ROOT,
                max_files=settings.KB_MAX_FILES_PER_JOB,
                max_file_bytes=settings.KB_MAX_FILE_BYTES,
            ),
        )
        background.append(
            asyncio.create_task(
                kb_ingest_loop(kb_processor, poll_seconds=settings.KB_INGEST_POLL_SECONDS)
            )
        )
    try:
        await liveness_loop(WORKER_ALIVE_PATH, do_work=worker.do_work)
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await llm_http.aclose()
        await checkpoint_pool.close()
        await app_pool.close()
        await owner_pool.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
