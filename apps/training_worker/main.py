"""The training worker: processes uploaded training videos, and sweeps uploads and usage rows.

Run as ``python -m apps.training_worker.main``, its own compose service in the same image. It is a
single instance per deployment: one process, one job at a time, and the claim's "no other job of
the company is processing" rule relies on there being only one. Never scale it out.

With ``TRAINING_VIDEO_ENABLED`` off it opens nothing and only touches its liveness file, so compose
can run it before the switch. With it on it refuses to start without ``VOICE_STT_ENDPOINT`` or the
training store, builds and checks only the training store (its key may be scoped to ``training/``;
the KB and HR stores are never built), opens a small owner pool (to list active companies) and a
small app pool (everything else, inside each company's transaction), and runs the job loop, the
hourly sweep and the liveness heartbeat until SIGTERM or SIGINT.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

import httpx
import structlog
from csa.agents.kb.embedder import DefaultKbEmbedder
from csa.core import db
from csa.core.liveness import TRAINING_WORKER_ALIVE_PATH, liveness_loop
from csa.core.settings import Settings, get_settings
from csa.core.shutdown import run_until_signalled
from csa.decide.groq import GroqClient
from csa.speech import SpeechTranscriber
from csa.storage.base import ObjectStore
from csa.storage.factory import Stores, build_stores, verify_stores
from csa.storage.multipart import MultipartStore
from csa.training import limits
from csa.training.video_jobs import Claim, JobHandle, VideoProcessor
from csa.training.video_summary import SummaryStage
from csa.training.video_transcribe import TranscribeStage

logger = structlog.get_logger(__name__)

POLL_SECONDS = 5.0
"""Wait between job polls when no company had a job due."""

POOL_SIZE = 2
"""Connections in each pool: the job loop and the sweep each hold at most one at a time."""


class WorkerStages:
    """The worker's stages: transcribing and the summary, each in its own module."""

    def __init__(self, transcribing: TranscribeStage, summarising: SummaryStage) -> None:
        self.transcribing = transcribing
        self.summarising = summarising

    async def transcribe(self, claim: Claim, job: JobHandle) -> Literal["summarising", "ready"]:
        return await self.transcribing.transcribe(claim, job)

    async def summarise(self, claim: Claim, job: JobHandle) -> None:
        await self.summarising.summarise(claim, job)


async def prepare_training_store(settings: Settings) -> ObjectStore:
    """Build and check the training store only; refuse to start without one that takes pieces."""
    store = build_stores(settings).training
    if store is None:
        raise RuntimeError("TRAINING_VIDEO_ENABLED needs the training store")
    if not isinstance(store, MultipartStore):
        raise RuntimeError("the training store cannot take video uploads")
    await verify_stores(Stores(kb=None, hr=None, training=store))
    return store


async def jobs_loop(processor: VideoProcessor, *, poll_seconds: float = POLL_SECONDS) -> None:
    """Run jobs until cancelled; sleep only when no company had one due."""
    while True:
        try:
            handled = await processor.run_once()
        except Exception as failure:
            logger.error("training_jobs_loop_error", outcome=type(failure).__name__)
            handled = 0
        if not handled:
            await asyncio.sleep(poll_seconds)


async def sweep_loop(
    processor: VideoProcessor, *, every_seconds: float = limits.SWEEP_EVERY_SECONDS
) -> None:
    """Sweep every company once an hour until cancelled."""
    while True:
        try:
            report = await processor.sweep_once()
            logger.info(
                "training_sweep",
                abandoned=report.abandoned,
                removed=report.removed,
                aborted=report.aborted,
                pruned=report.pruned,
                outcome="done",
            )
        except Exception as failure:
            logger.error("training_sweep_loop_error", outcome=type(failure).__name__)
        await asyncio.sleep(every_seconds)


def watch(tasks: Sequence[asyncio.Task[Any]]) -> Callable[[], Awaitable[bool]]:
    """The heartbeat's check: a loop that has stopped ends the process instead of hiding."""

    async def check() -> bool:
        if any(task.done() for task in tasks):
            raise RuntimeError("a training worker loop stopped")
        return False

    return check


async def _nothing() -> None:
    return None


async def main() -> None:
    """Start the training worker (or only its heartbeat while video is off)."""
    settings = get_settings()
    if not settings.TRAINING_VIDEO_ENABLED:
        logger.info("training_worker_idle", outcome="video_off")
        await run_until_signalled(liveness_loop(TRAINING_WORKER_ALIVE_PATH), _nothing)
        return
    if settings.VOICE_STT_ENDPOINT is None:
        raise RuntimeError("TRAINING_VIDEO_ENABLED needs VOICE_STT_ENDPOINT")
    # A bad bucket or root stops the worker before it opens pools or starts any loop.
    store = await prepare_training_store(settings)
    owner_pool = db.build_pool(str(settings.LG_DATABASE_URL), max_size=POOL_SIZE)
    app_pool = db.build_pool(str(settings.DATABASE_URL), max_size=POOL_SIZE)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    http = httpx.AsyncClient()
    speech_key = settings.voice_transcription_key()
    if not isinstance(store, MultipartStore):  # prepare_training_store refused it already
        raise RuntimeError("the training store cannot take video uploads")
    stages = WorkerStages(
        TranscribeStage(
            store,
            SpeechTranscriber(
                str(settings.VOICE_STT_ENDPOINT),
                settings.VOICE_STT_MODEL,
                speech_key.get_secret_value() if speech_key else None,
            ),
            DefaultKbEmbedder.from_settings(settings),
        ),
        SummaryStage(
            GroqClient(
                settings.GROQ_API_KEY.get_secret_value(),
                settings.GROQ_MODEL,
                http,
                str(settings.GROQ_CHAT_URL),
            )
        ),
    )
    processor = VideoProcessor(owner_pool, app_pool, store, stages)
    background = [
        asyncio.create_task(jobs_loop(processor)),
        asyncio.create_task(sweep_loop(processor)),
    ]

    async def shut_down() -> None:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await http.aclose()
        await app_pool.close()
        await owner_pool.close()

    logger.info("training_worker_started", outcome="running")
    await run_until_signalled(
        liveness_loop(TRAINING_WORKER_ALIVE_PATH, do_work=watch(background)), shut_down
    )


if __name__ == "__main__":  # pragma: no cover
    # A stop (SIGTERM) cancels `main` after its shutdown has run; that is a clean exit.
    with contextlib.suppress(asyncio.CancelledError):
        asyncio.run(main())
