"""Run only KB ingestion for one explicitly selected isolated-dev tenant."""

from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from csa.agents.kb.embedder import DefaultKbEmbedder
from csa.agents.kb.ingest_jobs import KbIngestConfig, KbIngestProcessor
from csa.core import db
from csa.core.settings import Settings, get_settings
from psycopg.conninfo import conninfo_to_dict


def validate(settings: Settings) -> None:
    params = conninfo_to_dict(str(settings.DATABASE_URL))
    expected = {"host": "127.0.0.1", "port": "5434", "dbname": "csa", "user": "csa_app"}
    if settings.ENV != "dev" or any(params.get(key) != value for key, value in expected.items()):
        raise ValueError("KB dev worker requires csa_app on isolated 127.0.0.1:5434/csa")
    if not settings.KB_INGEST_ENABLED or settings.KB_UPLOAD_ROOT is None:
        raise ValueError("KB dev worker requires KB_INGEST_ENABLED and KB_UPLOAD_ROOT")
    root = settings.KB_UPLOAD_ROOT
    if not root.is_absolute() or root.resolve() in {
        Path("/"),
        Path.home(),
        Path(__file__).resolve().parents[1],
    }:
        raise ValueError("KB dev worker requires a dedicated absolute upload directory")
    if settings.TEI_ENDPOINT is not None:
        endpoint = urlsplit(str(settings.TEI_ENDPOINT))
        if (
            endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "localhost"}
            or endpoint.username is not None
            or endpoint.password is not None
        ):
            raise ValueError("KB dev worker permits only credential-free loopback HTTP TEI")


async def run(client_id: UUID) -> None:
    settings = get_settings()
    validate(settings)
    assert settings.KB_UPLOAD_ROOT is not None
    if not settings.KB_UPLOAD_ROOT.is_dir():
        raise ValueError("KB_UPLOAD_ROOT must already exist")
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    pool = db.build_pool(str(settings.DATABASE_URL), min_size=1, max_size=2, autocommit=True)
    async with pool:
        processor = KbIngestProcessor(
            pool,
            pool,
            embedder=DefaultKbEmbedder.from_settings(settings),
            config=KbIngestConfig(
                upload_root=settings.KB_UPLOAD_ROOT,
                max_files=settings.KB_MAX_FILES_PER_JOB,
                max_file_bytes=settings.KB_MAX_FILE_BYTES,
            ),
        )
        print(f"KB-only dev worker ready: tenant={client_id}", flush=True)
        while not stopped.is_set():
            job = await processor.claim(client_id)
            if job is not None:
                await processor.process(job)
                continue
            try:
                await asyncio.wait_for(stopped.wait(), timeout=settings.KB_INGEST_POLL_SECONDS)
            except TimeoutError:
                continue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", type=UUID, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.client_id))


if __name__ == "__main__":
    main()
