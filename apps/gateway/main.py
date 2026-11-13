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
from csa.agents.kb.embedder import DefaultKbEmbedder, KbEmbedder, KbEmbeddingError
from csa.bookings.api import build_bookings_router
from csa.bookings.google.api import build_calendar_router
from csa.bookings.google.factory import build_calendar_push
from csa.channels.ingress import IngressError, TelegramIngress
from csa.channels.marketplace.router import build_marketplace_router
from csa.channels.whatsapp_ingress import WhatsAppIngress
from csa.console.auth import build_identity_router
from csa.console.channels import build_channels_router
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.mailer import Mailer
from csa.console.middleware import ConsoleResponseHeaders
from csa.console.onboarding import build_onboarding_router
from csa.console.router import build_router
from csa.console.tiktok_connect import build_tiktok_connect
from csa.console.voice_sessions import build_voice_media_router
from csa.core.db import GATEWAY_WORKER_MAX_SIZE, build_pool
from csa.core.langfuse import LangfuseSink
from csa.core.logging import configure_logging, logging_health, shutdown_logging
from csa.core.settings import Settings, get_settings
from csa.core.tracing import configure_tracing, instrument_fastapi
from csa.decide.deciders import build_deciders
from csa.decide.groq import GroqClient
from csa.decide.jev import JevClient
from csa.email.api import build_email_router
from csa.email.gmail import GmailClient
from csa.email.service import EmailService
from csa.hr.api import build_hr_router
from csa.hr.assistant import HrAssistant
from csa.hr.service import HrService
from csa.marketplace.reads import live_read_access
from csa.metering.meter import LedgerMeter
from csa.storage.factory import build_stores, verify_stores
from csa.subscriptions.api import build_subscriptions_router
from csa.workspace.executors import build_proposal_registry
from csa.workspace.planner import WorkspacePlanner
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from scripts.migrate import discover_migrations

# Highest auto-applied .sql version shipped in the image (design §9.4). The
# *.py re-encrypt skeleton (0130) is never applied, so it is not counted here.
EXPECTED_SCHEMA_VERSION = "0572"

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


def _kb_embedder(settings: Settings) -> KbEmbedder | None:
    """The embedder the worker uses (TEI when ``TEI_ENDPOINT`` is set, the hash embedder in dev
    and ci). None when prod has no TEI: the knowledge base source for drafts is then left off."""
    try:
        return DefaultKbEmbedder.from_settings(settings)
    except KbEmbeddingError:
        logger.warning("kb_embedder_unavailable", code="tei_endpoint_missing")
        return None


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
    # One set of stores serves the KB routes and the startup check.
    stores = build_stores(settings)
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
        training_http: httpx.AsyncClient | None = None
        calendar_http: httpx.AsyncClient | None = None
        jev_http: httpx.AsyncClient | None = None
        langfuse_sink: LangfuseSink | None = None
        try:
            # A bad bucket or root stops startup; there is no fallback to local disk.
            await verify_stores(stores)
            await pool.open(wait=True)
            app.state.pool = pool
            # Langfuse (OTLP), only with both keys: one generation per model call, sliced by
            # client, agent and job.
            if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
                langfuse_sink = LangfuseSink(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    base_url=settings.LANGFUSE_BASE_URL or "https://cloud.langfuse.com",
                )
            # One ledger meter for the gateway: every Groq client below books its token usage to
            # it, and mirrors each call to Langfuse when the sink is configured.
            meter = LedgerMeter(pool, mirror=langfuse_sink)
            # Read-only knowledge base search for batch drafts; None leaves that source off.
            app.state.kb_embedder = _kb_embedder(settings)
            # One Jev client for every decider, only with a key; without one every area runs on
            # the LLM or rule and nothing errors.
            jev_client: JevClient | None = None
            if settings.TYPESAFE_API_KEY is not None:
                jev_http = httpx.AsyncClient()
                jev_client = JevClient(settings.TYPESAFE_API_KEY.get_secret_value(), jev_http)
            deciders = build_deciders(settings, jev_client, pool)
            app.state.jev_client = jev_client
            app.state.email_decider = deciders.email
            app.state.routing_decider = deciders.routing
            app.state.inbound_decider = deciders.inbound
            # TikTok Shop connect routes; they answer tiktok_unavailable while TikTok is off.
            # The first-sync queue and Disconnect are the real csa.marketplace functions.
            app.state.tiktok_connect = build_tiktok_connect(settings, pool)
            # Live TikTok stock reads for the workspace assistant (None while TikTok is off).
            app.state.marketplace_live = live_read_access(settings, pool)
            app.state.voice_endpoint = (
                str(settings.VOICE_STT_ENDPOINT) if settings.VOICE_STT_ENDPOINT else None
            )
            voice_key = settings.voice_transcription_key()
            app.state.voice_api_key = voice_key.get_secret_value() if voice_key else None
            app.state.voice_model = settings.VOICE_STT_MODEL
            app.state.voice_sessions_config = settings.voice_session_config()
            app.state.telegram_ingress = TelegramIngress(pool)
            if settings.whatsapp_enabled:
                app_secret = settings.WHATSAPP_APP_SECRET
                verify_token = settings.WHATSAPP_VERIFY_TOKEN
                if app_secret is not None and verify_token is not None:
                    app.state.whatsapp_ingress = WhatsAppIngress(
                        pool,
                        app_secret=app_secret.get_secret_value(),
                        verify_token=verify_token.get_secret_value(),
                    )
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
                # Disabled unless the flag and both relay secrets are set; then links are manual.
                app.state.mailer = Mailer.from_settings(settings)
                if settings.BOOKINGS_GOOGLE_CALENDAR_ENABLED:
                    calendar_http = httpx.AsyncClient(trust_env=False)
                    calendar_push = build_calendar_push(settings, pool, calendar_http)
                    assert calendar_push is not None
                    app.state.calendar_connections = calendar_push.connections
                    app.state.calendar_push = calendar_push
                hr = HrService(pool, session_idle_seconds=settings.CONSOLE_SESSION_IDLE_SECONDS)
                await hr.check_ready()
                app.state.hr_service = hr
                # The training library's one-click PDF summary; without it the summary POST
                # answers training_assistant_unavailable.
                training_http = httpx.AsyncClient()
                app.state.training_model = GroqClient(
                    settings.GROQ_API_KEY.get_secret_value(),
                    settings.GROQ_MODEL,
                    training_http,
                    str(settings.GROQ_CHAT_URL),
                    meter=meter,
                )
                if settings.HR_ASSISTANT_ENABLED:
                    hr_http = httpx.AsyncClient()
                    app.state.hr_assistant = HrAssistant(
                        hr,
                        GroqClient(
                            settings.GROQ_API_KEY.get_secret_value(),
                            settings.GROQ_MODEL,
                            hr_http,
                            str(settings.GROQ_CHAT_URL),
                            meter=meter,
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
                            str(settings.GROQ_CHAT_URL),
                            meter=meter,
                        ),
                        session_idle_seconds=settings.CONSOLE_SESSION_IDLE_SECONDS,
                        # None unless the inbound injection area runs Jev (needs a key).
                        inbound_decider=deciders.inbound,
                        # The assistant's batch drafter only, on its own client when a model is
                        # set; otherwise batch drafts use the Email tab's model above.
                        batch_model=(
                            GroqClient(
                                settings.GROQ_API_KEY.get_secret_value(),
                                settings.ASSISTANT_DRAFT_MODEL,
                                email_http,  # closed with the email client at shutdown
                                str(settings.GROQ_CHAT_URL),
                                meter=meter,
                            )
                            if settings.ASSISTANT_DRAFT_MODEL
                            else None
                        ),
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
                            str(settings.GROQ_CHAT_URL),
                            meter=meter,
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
                if training_http is not None:
                    await training_http.aclose()
                if calendar_http is not None:
                    await calendar_http.aclose()
                if jev_http is not None:
                    await jev_http.aclose()
            finally:
                try:
                    if identity_pool is not None:
                        await identity_pool.close()
                finally:
                    try:
                        await pool.close()
                    finally:
                        try:
                            # Bounded (a few seconds) even while Langfuse is down; after the
                            # pools, and before logging stops so its warnings are written.
                            if langfuse_sink is not None:
                                await langfuse_sink.aclose()
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
            },
        )

    @app.get("/channels/whatsapp")
    async def whatsapp_verify(request: Request) -> Response:
        """Answer Meta's webhook subscription handshake."""
        ingress = getattr(request.app.state, "whatsapp_ingress", None)
        if not settings.whatsapp_enabled or ingress is None:
            return JSONResponse(status_code=404, content={"detail": "not found"})
        params = request.query_params
        answer = ingress.challenge(
            params.get("hub.mode"), params.get("hub.verify_token"), params.get("hub.challenge")
        )
        if answer is None:
            return JSONResponse(status_code=403, content={"detail": "verification failed"})
        return PlainTextResponse(answer)

    @app.post("/channels/whatsapp")
    async def whatsapp_webhook(request: Request) -> JSONResponse:
        """Verify and durably enqueue one WhatsApp delivery, then acknowledge it."""
        ingress = getattr(request.app.state, "whatsapp_ingress", None)
        if not settings.whatsapp_enabled or ingress is None:
            return JSONResponse(status_code=404, content={"detail": "not found"})
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
            receipts = await ingress.receive(
                request.headers.get("X-Hub-Signature-256"), b"".join(chunks)
            )
        except IngressError as exc:
            if exc.status_code >= 500:
                logger.error(
                    "whatsapp_ingress_rejected",
                    status_code=exc.status_code,
                    detail=exc.detail,
                )
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        duplicates = sum(1 for receipt in receipts if receipt.duplicate)
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "accepted": len(receipts) - duplicates,
                "duplicates": duplicates,
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
            kb_store=stores.kb,
            training_store=stores.training,
        )
    )

    # --- lane registrations: one line per lane; add your one line directly under your
    # lane's comment; no other edits here ---
    app.include_router(build_marketplace_router())
    app.include_router(build_hr_router(hr_store=stores.hr))
    app.include_router(build_email_router())
    # lane A (customer-facing): WhatsApp router registers here
    app.include_router(build_channels_router())
    # lane B (console): subscriptions router registers here
    app.include_router(build_subscriptions_router())
    app.include_router(build_bookings_router())
    app.include_router(build_calendar_router())
    # --- end lane registrations ---

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
