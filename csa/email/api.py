"""Authenticated staff email assistant API, mounted by the configured gateway.

Mirrors the HR router: bounded request bodies, content-free error mapping,
human sessions only, CSRF on writes, explicit tenant for platform administrators.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import SecretStr

from csa.console.contracts import ConsoleActor
from csa.console.identity import SESSION_COOKIE, IdentityService
from csa.console.security import digest
from csa.console.services import require_package
from csa.decide.completion import CompletionError
from csa.email.gmail import GmailError
from csa.email.models import (
    SEND_OUTCOME_UNKNOWN_MESSAGE,
    AttentionPage,
    ComposeRequest,
    ConnectOut,
    DraftOut,
    DraftPage,
    DraftRequest,
    DraftUpdate,
    EmailContext,
    EmailPolicyError,
    EmailSession,
    Receipt,
    ReplyRequest,
    SendLimitReached,
    SendRequest,
    SendResult,
    SettingsIn,
    ThreadDetail,
    ThreadPage,
    is_employee_ref,
)
from csa.email.service import EmailService

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}
_FORBIDDEN_HEADERS = ("x-employee-id", "x-manager-id", "x-hr-role", "x-hr-grants", "x-client-owner")
_CALLBACK_QUERY = {
    "client_id",
    "code",
    "state",
    "scope",
    "authuser",
    "prompt",
    "hd",
    "error",
    "iss",
}
# Google sends RFC 9207 `iss` with the authorization response; only its own issuer is accepted.
_GOOGLE_ISSUER = "https://accounts.google.com"
_MAX_BODY = 32768


def email_http_error(exc: EmailPolicyError | GmailError) -> HTTPException:
    """Content-free HTTP form of an email refusal, shared with workspace proposals."""
    code = exc.code
    if isinstance(exc, SendLimitReached):
        return HTTPException(429, code, headers={"Retry-After": str(exc.retry_after)})
    if isinstance(exc, GmailError):
        status = 502 if code in {"provider_unreachable", "provider_invalid_response"} else 409
    elif code == "send_outcome_unknown":
        status = 502
    elif code == "session_unavailable":
        status = 401
    elif code in {"permission_denied", "csrf_required"}:
        status = 403
    elif code in {"recipient_unresolved", "recipients_invalid"}:
        status = 422
    elif code == "resource_unavailable":
        status = 404
    elif code in {"provider_not_configured", "model_not_configured"}:
        status = 503
    else:
        status = 409
    return HTTPException(status, code)


def email_error_content(exc: EmailPolicyError | GmailError) -> dict[str, Any]:
    """Response body for a refusal: the code, plus the wait or the user message when one applies."""
    content: dict[str, Any] = {"detail": exc.code}
    if isinstance(exc, SendLimitReached):
        content["retry_after"] = exc.retry_after
    elif exc.code == "send_outcome_unknown":
        content["message"] = SEND_OUTCOME_UNKNOWN_MESSAGE
    return content


def _uses_directory(*refs: list[str]) -> bool:
    return any(is_employee_ref(ref) for group in refs for ref in group)


def email_session_from(
    request: Request, actor: ConsoleActor, tenant_id: UUID, *, mutation: bool
) -> EmailSession:
    """Session proof for an authenticated actor; EmailService revalidates every field.

    Callers have already authenticated the actor and, for mutations, checked CSRF.
    """
    if actor.platform_admin:
        role = next((m.role for m in actor.memberships if m.client_id == tenant_id), None)
    else:
        role = actor.role.value if actor.role is not None else None
    return EmailSession(
        session_id=actor.session_id,
        subject_id=actor.user_id,
        tenant_id=tenant_id,
        role=role,  # type: ignore[arg-type]
        token_hash=SecretStr(digest(request.cookies.get(SESSION_COOKIE, ""))),
        csrf_hash=SecretStr(digest(request.headers.get("x-csrf-token", ""))) if mutation else None,
    )


class EmailRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                data = bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > _MAX_BODY:
                        raise HTTPException(413, "email_request_too_large")
                request._body = bytes(data)
                response = await handler(request)
            except RequestValidationError as exc:
                response = JSONResponse(
                    status_code=422,
                    content={"detail": "invalid_email_input", "invalid_fields": len(exc.errors())},
                )
            except (EmailPolicyError, GmailError) as exc:
                error = email_http_error(exc)
                response = JSONResponse(
                    status_code=error.status_code,
                    content=email_error_content(exc),
                    headers=error.headers,
                )
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
                )
            except (
                psycopg.errors.DeadlockDetected,
                psycopg.errors.SerializationFailure,
                psycopg.errors.LockNotAvailable,
            ):
                response = JSONResponse(status_code=409, content={"detail": "email_retry_required"})
            except psycopg.Error:
                response = JSONResponse(
                    status_code=503, content={"detail": "email_database_unavailable"}
                )
            except TimeoutError:
                response = JSONResponse(
                    status_code=503, content={"detail": "email_timeout_check_saved_state"}
                )
            except CompletionError:
                response = JSONResponse(
                    status_code=503, content={"detail": "email_model_unavailable"}
                )
            response.headers.update(_NO_STORE)
            return response

        return guarded


async def _resolve(request: Request, client_id: UUID | None, allowed: set[str]) -> EmailSession:
    keys = [key for key, _ in request.query_params.multi_items()]
    if any(key not in allowed for key in keys) or len(keys) != len(set(keys)):
        raise HTTPException(400, "invalid_email_query")
    if request.headers.get("authorization"):
        raise HTTPException(401, "human_session_required")
    if any(key in request.headers for key in _FORBIDDEN_HEADERS):
        raise HTTPException(403, "browser_identity_headers_forbidden")
    identity = getattr(request.app.state, "identity_service", None)
    if not isinstance(identity, IdentityService):
        raise HTTPException(503, "identity_unavailable")
    actor = await identity.actor_for_request(request)
    if actor.platform_admin:
        if client_id is None:
            raise HTTPException(400, "explicit_tenant_required")
        tenant_id = client_id
    else:
        if actor.client_id is None or (client_id is not None and client_id != actor.client_id):
            raise HTTPException(403, "tenant_not_authorized")
        tenant_id = actor.client_id
    await require_package(request.app.state.pool, tenant_id, frozenset({"staff_email"}))
    mutation = request.method not in {"GET", "HEAD", "OPTIONS"}
    session = email_session_from(request, actor, tenant_id, mutation=mutation)
    if mutation:
        if session.role == "read_only":
            raise HTTPException(403, "read_only_session")
        await identity.require_csrf(request, actor)
    return session


async def _session(
    request: Request, client_id: Annotated[UUID | None, Query()] = None
) -> EmailSession:
    return await _resolve(request, client_id, {"client_id"})


async def _callback_session(
    request: Request, client_id: Annotated[UUID | None, Query()] = None
) -> EmailSession:
    return await _resolve(request, client_id, _CALLBACK_QUERY)


def _service(request: Request) -> EmailService:
    service = getattr(request.app.state, "email_service", None)
    if not isinstance(service, EmailService):
        raise HTTPException(503, "email_assistant_not_configured")
    return service


Session = Annotated[EmailSession, Depends(_session)]
Service = Annotated[EmailService, Depends(_service)]
RequestKey = Annotated[UUID, Header(alias="Idempotency-Key")]


def build_email_router() -> APIRouter:
    router = APIRouter(prefix="/console/v1/email", tags=["Email"], route_class=EmailRoute)

    @router.get("/context", response_model=EmailContext)
    async def context(session: Session, service: Service) -> EmailContext:
        return await service.context(session)

    @router.put("/settings", response_model=Receipt)
    async def settings(
        body: SettingsIn, session: Session, service: Service, request_key: RequestKey
    ) -> Receipt:
        return await service.set_enabled(session, body.enabled, request_key)

    @router.post("/mailbox/connect", response_model=ConnectOut)
    async def connect(session: Session, service: Service) -> ConnectOut:
        return await service.connect_start(session)

    @router.get("/oauth/callback", include_in_schema=True)
    async def callback(
        request: Request,
        service: Service,
        session: Annotated[EmailSession, Depends(_callback_session)],
        code: Annotated[str | None, Query(max_length=2048)] = None,
        state: Annotated[str | None, Query(max_length=256)] = None,
        error: Annotated[str | None, Query(max_length=64)] = None,
        iss: Annotated[str | None, Query(max_length=256)] = None,
    ) -> Response:
        target = "/email" if request.query_params.get("client_id") else "/portal/email"
        params: dict[str, str] = {}
        if request.query_params.get("client_id"):
            params["client_id"] = str(session.tenant_id)
        if iss is not None and iss != _GOOGLE_ISSUER:
            params["error"] = "oauth_issuer_invalid"
        elif error or not code or not state:
            params["error"] = "provider_denied" if error else "oauth_state_invalid"
        else:
            try:
                await service.connect_callback(session, code=code, state=state)
                params["connected"] = "1"
            except (EmailPolicyError, GmailError) as exc:
                params["error"] = exc.code
        response = RedirectResponse(f"{target}?{urlencode(params)}", status_code=303)
        response.headers.update(_NO_STORE)
        return response

    @router.post("/mailbox/disconnect", response_model=Receipt)
    async def disconnect(session: Session, service: Service, request_key: RequestKey) -> Receipt:
        return await service.disconnect(session, request_key)

    @router.post("/sync", response_model=Receipt)
    async def sync(session: Session, service: Service, request_key: RequestKey) -> Receipt:
        return await service.sync(session, request_key)

    @router.post("/sync/more", response_model=Receipt)
    async def sync_more(session: Session, service: Service, request_key: RequestKey) -> Receipt:
        return await service.sync_more(session, request_key)

    @router.get("/attention", response_model=AttentionPage)
    async def attention(session: Session, service: Service) -> AttentionPage:
        return await service.attention(session)

    @router.get("/threads", response_model=ThreadPage)
    async def threads(session: Session, service: Service) -> ThreadPage:
        return await service.threads(session)

    @router.get("/threads/{thread_id}", response_model=ThreadDetail)
    async def thread(thread_id: str, session: Session, service: Service) -> ThreadDetail:
        if not 1 <= len(thread_id) <= 256:
            raise HTTPException(404, "resource_unavailable")
        return await service.thread(session, thread_id)

    @router.post("/threads/{thread_id}/analyse", response_model=ThreadDetail)
    async def analyse(
        thread_id: str, session: Session, service: Service, request_key: RequestKey
    ) -> ThreadDetail:
        return await service.analyse(session, thread_id, request_key)

    @router.post("/threads/{thread_id}/draft", response_model=DraftOut, status_code=201)
    async def draft(
        thread_id: str,
        body: DraftRequest,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> DraftOut:
        return await service.draft(session, thread_id, body, request_key)

    @router.post("/threads/{thread_id}/reply", response_model=DraftOut, status_code=201)
    async def reply(
        request: Request,
        thread_id: str,
        body: ReplyRequest,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> DraftOut:
        if not 1 <= len(thread_id) <= 256:
            raise HTTPException(404, "resource_unavailable")
        if _uses_directory(body.cc, body.bcc):
            await require_package(request.app.state.pool, session.tenant_id, frozenset({"hr"}))
        return await service.reply(session, thread_id, body, request_key)

    @router.post("/compose", response_model=DraftOut, status_code=201)
    async def compose(
        request: Request,
        body: ComposeRequest,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> DraftOut:
        if body.recipient_employee_id is not None or _uses_directory(body.cc, body.bcc):
            # Directory-resolved recipients come from HR records; gate before any write.
            await require_package(request.app.state.pool, session.tenant_id, frozenset({"hr"}))
        return await service.compose(session, body, request_key)

    @router.get("/drafts", response_model=DraftPage)
    async def drafts(session: Session, service: Service) -> DraftPage:
        return await service.drafts(session)

    @router.get("/drafts/{draft_id}", response_model=DraftOut)
    async def draft_by_id(draft_id: UUID, session: Session, service: Service) -> DraftOut:
        """One of the caller's own drafts, read from the store only (no Gmail call).

        Scoped to the caller's own mailbox, as sending is: anyone else's draft is 404.
        """
        return await service.draft_by_id(session, draft_id)

    @router.put("/drafts/{draft_id}", response_model=DraftOut)
    async def update_draft(
        draft_id: UUID,
        body: DraftUpdate,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> DraftOut:
        return await service.update_draft(session, draft_id, body, request_key)

    @router.post("/drafts/{draft_id}/delete", response_model=Receipt)
    async def delete_draft(
        draft_id: UUID, session: Session, service: Service, request_key: RequestKey
    ) -> Receipt:
        return await service.delete_draft(session, draft_id, request_key)

    @router.post("/drafts/{draft_id}/export", response_model=DraftOut)
    async def export_draft(
        draft_id: UUID, session: Session, service: Service, request_key: RequestKey
    ) -> DraftOut:
        return await service.export_draft(session, draft_id, request_key)

    @router.post("/drafts/{draft_id}/send", response_model=SendResult)
    async def send_draft(
        draft_id: UUID,
        body: SendRequest,
        session: Session,
        service: Service,
        request_key: RequestKey,
    ) -> SendResult:
        return await service.send_draft(session, draft_id, body.version, request_key)

    @router.post("/drafts/{draft_id}/release", response_model=DraftOut)
    async def release_send(
        draft_id: UUID, session: Session, service: Service, request_key: RequestKey
    ) -> DraftOut:
        return await service.release_send(session, draft_id, request_key)

    return router
