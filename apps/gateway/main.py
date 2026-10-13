"""Gateway process entrypoint (FastAPI) — LG01 §8.3.

Thin skeleton: builds settings/logging/tracing, opens the DB pool on startup,
mounts the two health endpoints, and attaches OTel FastAPI instrumentation.
No business routes land until later phases.

Health contract (design §8.3):

* ``GET /healthz`` — process liveness only; 200 ``{"status": "ok"}`` with NO I/O.
* ``GET /readyz``  — readiness: the DB is reachable (``SELECT 1``, short timeout)
  AND migrations are at the expected version. "At version" is computed as
  **set membership** — every shipped ``db/migrations/*.sql`` version is present
  in ``schema_migrations`` — plus a ``max(applied) == EXPECTED_SCHEMA_VERSION``
  (``0140``) sanity check. Any missing shipped version, a ``max`` mismatch, or an
  unreachable DB returns 503 naming the gap. ``*.py`` migrations never enter the
  ledger, so the guarded ``0130`` re-encrypt skeleton is irrelevant to readiness.
"""  # LG01

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from csa.channels.ingress import IngressError, TelegramIngress
from csa.console.router import build_router
from csa.core.db import GATEWAY_WORKER_MAX_SIZE, build_pool
from csa.core.logging import configure_logging
from csa.core.settings import get_settings
from csa.core.tracing import configure_tracing, instrument_fastapi
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from scripts.migrate import discover_migrations

# Highest auto-applied .sql version shipped in the image (design §9.4). The
# *.py re-encrypt skeleton (0130) is never applied, so it is not counted here.
EXPECTED_SCHEMA_VERSION = "0220"

# Short per-statement timeout for the readiness DB probe (milliseconds).
_READYZ_STATEMENT_TIMEOUT_MS = 2000
_MAX_INGRESS_BODY_BYTES = 1_000_000
logger = structlog.get_logger(__name__)


def shipped_versions() -> set[str]:
    """Return the set of auto-applied ``.sql`` migration versions in the image.

    Derived from the ``db/migrations/*.sql`` filenames (the migration runner's
    own discovery), so it cannot drift from what the image actually ships. The
    guarded ``0130_reencrypt_credentials.py`` is a ``*.py`` file and is excluded.
    """
    return {version for version, _ in discover_migrations()}


async def check_readiness(pool: AsyncConnectionPool) -> tuple[bool, str]:
    """Return ``(ready, reason)`` for the ``/readyz`` probe.

    Ready iff the DB answers ``SELECT 1`` within a short timeout, every shipped
    ``.sql`` version is present in ``schema_migrations``, and the maximum applied
    version equals :data:`EXPECTED_SCHEMA_VERSION`.
    """
    expected = shipped_versions()
    try:
        async with pool.connection() as conn:
            # SET cannot bind a server-side parameter; compose the literal safely.
            await conn.execute(
                sql.SQL("SET statement_timeout = {}").format(
                    sql.Literal(_READYZ_STATEMENT_TIMEOUT_MS)
                )
            )
            await conn.execute("SELECT 1")
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT version FROM schema_migrations")
                applied = {str(row["version"]) for row in await cur.fetchall()}
    except Exception as exc:  # noqa: BLE001 - any DB failure means not-ready
        return False, f"database unreachable: {exc.__class__.__name__}"

    missing = expected - applied
    if missing:
        return False, f"missing migrations: {', '.join(sorted(missing))}"

    max_applied = max(applied) if applied else None
    if max_applied != EXPECTED_SCHEMA_VERSION:
        return False, (
            f"max applied version {max_applied!r} != expected {EXPECTED_SCHEMA_VERSION!r}"
        )
    return True, "ok"


def create_app() -> FastAPI:
    """Build the FastAPI app with health routes and OTel instrumentation.

    The DB pool is opened on startup and closed on shutdown. ``/healthz`` does no
    I/O; ``/readyz`` borrows the pool to probe the DB and the migration ledger.
    """
    settings = get_settings()
    configure_logging(env=settings.ENV)
    configure_tracing(
        exporter=settings.OTEL_EXPORTER,
        otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = build_pool(
            str(settings.DATABASE_URL),
            min_size=GATEWAY_WORKER_MAX_SIZE,
            max_size=GATEWAY_WORKER_MAX_SIZE,
            autocommit=True,
        )
        try:
            await pool.open(wait=True)
            app.state.pool = pool
            app.state.telegram_ingress = TelegramIngress(pool)
            yield
        finally:
            await pool.close()

    app = FastAPI(title="csa-gateway", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: no I/O, always 200 while the process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Readiness: DB reachable + migrations at the expected version."""
        ready, reason = await check_readiness(app.state.pool)
        status_code = 200 if ready else 503
        return JSONResponse(
            status_code=status_code,
            content={"status": "ready" if ready else "not_ready", "reason": reason},
        )

    @app.post("/hooks/{public_id}")
    async def telegram_webhook(public_id: str, request: Request) -> JSONResponse:
        """Verify and durably enqueue one Telegram update, then acknowledge it."""
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            return JSONResponse(status_code=415, content={"detail": "expected application/json"})

        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > _MAX_INGRESS_BODY_BYTES:
                return JSONResponse(
                    status_code=413, content={"detail": "request body is too large"}
                )
            chunks.append(chunk)

        try:
            receipt = await app.state.telegram_ingress.receive(
                public_id,
                request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
                b"".join(chunks),
            )
        except IngressError as exc:
            if exc.status_code >= 500:
                logger.error(
                    "telegram_ingress_rejected",
                    public_id=public_id,
                    status_code=exc.status_code,
                    detail=exc.detail,
                )
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        return JSONResponse(
            status_code=200,
            content={
                "ok": receipt.accepted,
                "duplicate": receipt.duplicate,
                "trace_id": str(receipt.trace_id),
            },
        )

    if settings.CONSOLE_API_TOKEN is not None:
        app.include_router(build_router(settings.CONSOLE_API_TOKEN.get_secret_value()))

    instrument_fastapi(app)
    return app


# ``uvicorn apps.gateway.main:app`` resolves this lazily (design §9.1): the app
# (and thus Settings) is built only when the ASGI server actually imports
# ``app``, so importing this module for its helpers — e.g. in tests — does not
# require the full runtime env to be present.
def __getattr__(name: str) -> object:
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
