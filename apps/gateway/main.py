"""Gateway lifecycle, channel ingress and session-aware console (LG01/LG15).

Opens the domain pool and optional isolated identity pool, mounts health,
verified channel ingress and console routes, and attaches OTel instrumentation.

Health contract (design §8.3):

* ``GET /healthz`` — process liveness only; 200 ``{"status": "ok"}`` with NO I/O.
* ``GET /readyz``  — readiness: the DB is reachable (``SELECT 1``, short timeout)
  AND migrations are at the expected version. "At version" is computed as
  **set membership** — every shipped ``db/migrations/*.sql`` version is present
  in ``schema_migrations`` — plus a ``max(applied) == EXPECTED_SCHEMA_VERSION``
  sanity check. Any missing shipped version, a ``max`` mismatch, or an
  unreachable DB returns 503 naming the gap. ``*.py`` migrations never enter the
  ledger, so the guarded ``0130`` re-encrypt skeleton is irrelevant to readiness.
"""  # LG01

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import structlog
from csa.channels.ingress import IngressError, TelegramIngress
from csa.channels.marketplace.router import build_marketplace_router
from csa.console.auth import build_identity_router
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.middleware import ConsoleResponseHeaders
from csa.console.onboarding import build_onboarding_router
from csa.console.router import build_router
from csa.console.voice_sessions import build_voice_media_router
from csa.core.db import GATEWAY_WORKER_MAX_SIZE, build_pool
from csa.core.logging import configure_logging, logging_health, shutdown_logging
from csa.core.settings import get_settings
from csa.core.tracing import configure_tracing, instrument_fastapi
from csa.decide.groq import GroqClient
from csa.email.api import build_email_router
from csa.email.gmail import GmailClient
from csa.email.service import EmailService
from csa.hr.api import build_hr_router
from csa.hr.assistant import HrAssistant
from csa.hr.service import HrService
from csa.workspace.executors import build_proposal_registry
from csa.workspace.planner import WorkspacePlanner
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from scripts.migrate import discover_migrations

# Highest auto-applied .sql version shipped in the image (design §9.4). The
# *.py re-encrypt skeleton (0130) is never applied, so it is not counted here.
EXPECTED_SCHEMA_VERSION = "0460"

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
    """Build the gateway with explicit domain/identity lifecycle and readiness.

    ``/healthz`` does no I/O. ``/readyz`` verifies domain migrations and, when
    configured, the identity pool's availability and least-privilege role.
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
        identity_pool = (
            build_pool(str(settings.IDENTITY_DATABASE_URL), min_size=1, max_size=4, autocommit=True)
            if settings.IDENTITY_DATABASE_URL is not None
            else None
        )
        hr_http: httpx.AsyncClient | None = None
        email_http: httpx.AsyncClient | None = None
        workspace_http: httpx.AsyncClient | None = None
        try:
            await pool.open(wait=True)
            app.state.pool = pool
            app.state.voice_endpoint = (
                str(settings.VOICE_STT_ENDPOINT) if settings.VOICE_STT_ENDPOINT else None
            )
            voice_key = settings.voice_transcription_key()
            app.state.voice_api_key = voice_key.get_secret_value() if voice_key else None
            app.state.voice_model = settings.VOICE_STT_MODEL
            app.state.voice_sessions_config = settings.voice_session_config()
            app.state.telegram_ingress = TelegramIngress(pool)
            # Executors resolve hr_service/email_service from app.state at call time.
            app.state.proposal_registry = build_proposal_registry()
            if identity_pool is not None:
                await identity_pool.open(wait=True)
                identity = IdentityService(
                    identity_pool,
                    IdentityConfig(
                        origins=(str(settings.CONSOLE_ALLOWED_ORIGIN),),
                        secure_cookies=settings.CONSOLE_COOKIE_SECURE,
                        session_idle_seconds=settings.CONSOLE_SESSION_IDLE_SECONDS,
                        session_absolute_seconds=settings.CONSOLE_SESSION_ABSOLUTE_SECONDS,
                        workspace_assistant=settings.WORKSPACE_ASSISTANT_ENABLED,
                    ),
                )
                await identity.check_ready()
                app.state.identity_service = identity
                hr = HrService(pool, session_idle_seconds=settings.CONSOLE_SESSION_IDLE_SECONDS)
                await hr.check_ready()
                app.state.hr_service = hr
                if settings.HR_ASSISTANT_ENABLED:
                    hr_http = httpx.AsyncClient()
                    app.state.hr_assistant = HrAssistant(
                        hr,
                        GroqClient(
                            settings.GROQ_API_KEY.get_secret_value(), settings.GROQ_MODEL, hr_http
                        ),
                    )
                if settings.EMAIL_ASSISTANT_ENABLED:
                    assert settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
                    assert settings.GOOGLE_OAUTH_REDIRECT_URI
                    email_http = httpx.AsyncClient(trust_env=False)
                    email = EmailService(
                        pool,
                        gmail=GmailClient(
                            settings.GOOGLE_CLIENT_ID,
                            settings.GOOGLE_CLIENT_SECRET,
                            settings.GOOGLE_OAUTH_REDIRECT_URI,
                            email_http,
                        ),
                        model=GroqClient(
                            settings.GROQ_API_KEY.get_secret_value(),
                            settings.GROQ_MODEL,
                            email_http,
                        ),
                        session_idle_seconds=settings.CONSOLE_SESSION_IDLE_SECONDS,
                    )
                    await email.check_ready()
                    app.state.email_service = email
                if settings.WORKSPACE_ASSISTANT_ENABLED:
                    workspace_http = httpx.AsyncClient()
                    app.state.workspace_planner = WorkspacePlanner(
                        GroqClient(
                            settings.GROQ_API_KEY.get_secret_value(),
                            settings.GROQ_MODEL,
                            workspace_http,
                        )
                    )
            elif (
                settings.HR_ASSISTANT_ENABLED
                or settings.EMAIL_ASSISTANT_ENABLED
                or settings.WORKSPACE_ASSISTANT_ENABLED
            ):
                raise RuntimeError("HR, email and workspace assistants require console identity")
            yield
        finally:
            try:
                if hr_http is not None:
                    await hr_http.aclose()
                if email_http is not None:
                    await email_http.aclose()
                if workspace_http is not None:
                    await workspace_http.aclose()
            finally:
                try:
                    if identity_pool is not None:
                        await identity_pool.close()
                finally:
                    try:
                        await pool.close()
                    finally:
                        shutdown_logging()

    app = FastAPI(
        title="csa-gateway",
        lifespan=lifespan,
        docs_url="/docs" if settings.ENV != "prod" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.ENV != "prod" else None,
    )
    app.add_middleware(ConsoleResponseHeaders)
    app.include_router(build_voice_media_router())

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith("/console/"):
            # Validation errors must never echo passwords or invitation/reset secrets.
            return JSONResponse(
                status_code=422,
                content={
                    "detail": [
                        {key: error[key] for key in ("loc", "msg", "type")}
                        for error in exc.errors()
                    ]
                },
            )
        return await request_validation_exception_handler(request, exc)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: no I/O, always 200 while the process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Readiness: DB reachable + migrations at the expected version."""
        ready, reason = logging_health()
        if ready:
            ready, reason = await check_readiness(app.state.pool)
        if ready and settings.IDENTITY_DATABASE_URL is not None:
            try:
                await app.state.identity_service.check_ready()
            except Exception as exc:
                ready, reason = False, f"identity database unavailable: {exc.__class__.__name__}"
        if ready and settings.IDENTITY_DATABASE_URL is not None:
            try:
                await app.state.hr_service.check_ready()
            except Exception as exc:
                ready, reason = False, f"HR database unavailable: {exc.__class__.__name__}"
        if ready and settings.EMAIL_ASSISTANT_ENABLED:
            try:
                await app.state.email_service.check_ready()
            except Exception as exc:
                ready, reason = False, f"email database unavailable: {exc.__class__.__name__}"
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
                "proxied": receipt.proxied,
            },
        )

    # Human session routes exist independently of the optional machine token.
    app.include_router(build_identity_router())
    app.include_router(build_onboarding_router())
    app.include_router(
        build_router(
            settings.CONSOLE_API_TOKEN.get_secret_value() if settings.CONSOLE_API_TOKEN else None,
            machine_client_ids=frozenset(
                UUID(value) for value in settings.CONSOLE_MACHINE_CLIENT_IDS
            ),
            kb_model=settings.KB_EMBED_MODEL_LABEL
            if settings.TEI_ENDPOINT is not None
            else ("hash-384" if settings.ENV != "prod" else None),
            kb_tei_configured=settings.TEI_ENDPOINT is not None,
            kb_upload_root=settings.KB_UPLOAD_ROOT,
        )
    )

    app.include_router(build_marketplace_router())
    app.include_router(build_hr_router())
    app.include_router(build_email_router())

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
