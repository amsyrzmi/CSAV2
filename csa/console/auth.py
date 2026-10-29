"""Human session HTTP routes, independently mounted from machine bearer routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from csa.console.contracts import (
    AdminUserOut,
    AuditOut,
    ConsoleActor,
    EmailInput,
    InvitationOut,
    InviteIn,
    IssuedTokenOut,
    LoginIn,
    MemberOut,
    MemberPatchIn,
    MeOut,
    PasswordChangeIn,
    TokenAcceptIn,
    TokenIn,
    TokenInfoOut,
    UserPatchIn,
)
from csa.console.identity import CSRF_COOKIE, SESSION_COOKIE, IdentityService, Notify
from csa.console.mail_templates import invitation_mail, reset_mail
from csa.console.mailer import Mailer, MailOutcome
from csa.console.security import digest
from csa.console.services import require_package
from csa.core import db
from csa.core.tenancy import tx


class CsrfOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csrf_token: str


class MessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


def identity_service(request: Request) -> IdentityService:
    service = getattr(request.app.state, "identity_service", None)
    if not isinstance(service, IdentityService):
        raise HTTPException(503, "console identity is not configured")
    return service


async def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


async def _actor(
    request: Request, service: Annotated[IdentityService, Depends(identity_service)]
) -> ConsoleActor:
    return await service.actor_for_request(request)


async def _lifecycle_actor(
    request: Request, service: Annotated[IdentityService, Depends(identity_service)]
) -> ConsoleActor:
    return await service.actor_for_request(request, allow_password_change=True)


async def _mutation(
    request: Request,
    actor: Annotated[ConsoleActor, Depends(_actor)],
    service: Annotated[IdentityService, Depends(identity_service)],
) -> ConsoleActor:
    await service.require_csrf(request, actor)
    return actor


@dataclass(frozen=True)
class InviteFacts:
    business_name: str
    timezone: str
    may_grant_hr: bool


def _mailer(request: Request) -> Mailer | None:
    mailer = getattr(request.app.state, "mailer", None)
    return mailer if isinstance(mailer, Mailer) else None


async def _invite_facts(request: Request, actor: ConsoleActor, client_id: UUID) -> InviteFacts:
    """Read the tenant facts on the app pool; the identity pool may not read clients or HR."""
    async with tx(request.app.state.pool, str(client_id)) as conn:
        # A platform session never carries a tenant HR owner's right to grant presets.
        if not actor.platform_admin:
            await db.q.execute("console/invite_facts_context.sql", (str(actor.user_id),), conn=conn)
        row = await db.q.fetch_one(
            "console/invite_facts.sql", (actor.user_id, client_id), conn=conn
        )
    if row is None:
        raise HTTPException(404, "tenant not found")
    ZoneInfo(row["timezone"])  # Fail before any write, never after the invitation commits.
    return InviteFacts(
        business_name=row["business_name"],
        timezone=row["timezone"],
        may_grant_hr=bool(row["may_grant_hr"]) and not actor.platform_admin,
    )


def _invitation_notify(
    mailer: Mailer | None,
    actor: ConsoleActor,
    facts: InviteFacts,
    *,
    to: str,
    invitee_name: str,
) -> Notify | None:
    if mailer is None:
        return None

    async def notify(link: str, expires_at: datetime) -> MailOutcome:
        return await mailer.send(
            invitation_mail(
                to=to,
                invitee_name=invitee_name,
                inviter_name=actor.display_name,
                business_name=facts.business_name,
                link=link,
                expires_at=expires_at,
                tz=facts.timezone,
            )
        )

    return notify


async def _invite(
    request: Request, body: InviteIn, actor: ConsoleActor, service: IdentityService
) -> IssuedTokenOut:
    # Authorise first, so the package and facts reads never answer for another tenant.
    service.require_member_admin(actor, body.client_id)
    if body.hr_preset is not None:
        try:
            await require_package(request.app.state.pool, body.client_id, frozenset({"hr"}))
        except HTTPException as exc:
            if exc.detail != "package_inactive":
                raise
            raise HTTPException(422, "hr_not_available") from None
    facts = await _invite_facts(request, actor, body.client_id)
    notify = _invitation_notify(
        _mailer(request), actor, facts, to=body.email, invitee_name=body.display_name
    )
    return await service.invite(actor, body, may_grant_hr=facts.may_grant_hr, notify=notify)


Service = Annotated[IdentityService, Depends(identity_service)]
Actor = Annotated[ConsoleActor, Depends(_actor)]
Mutation = Annotated[ConsoleActor, Depends(_mutation)]
LifecycleActor = Annotated[ConsoleActor, Depends(_lifecycle_actor)]


def build_identity_router() -> APIRouter:
    router = APIRouter(
        prefix="/console/v1", tags=["console identity"], dependencies=[Depends(_no_store)]
    )

    @router.get("/auth/csrf", response_model=CsrfOut)
    async def csrf(request: Request, response: Response, service: Service) -> CsrfOut:
        return CsrfOut(csrf_token=await service.csrf(request, response))

    @router.post("/auth/login", response_model=MeOut)
    async def login(body: LoginIn, request: Request, response: Response, service: Service) -> MeOut:
        await service.require_csrf(request)
        actor, raw, csrf_token = await service.login(
            body.email,
            body.password.get_secret_value(),
            request.client.host if request.client else "unknown",
            previous_session=request.cookies.get(SESSION_COOKIE, ""),
        )
        service.set_cookies(response, raw, csrf_token)
        return actor.me(csrf_token, workspace_assistant=service.config.workspace_assistant)

    @router.get("/auth/me", response_model=MeOut)
    async def me(request: Request, actor: LifecycleActor, service: Service) -> MeOut:
        return actor.me(
            request.cookies.get(CSRF_COOKIE, ""),
            workspace_assistant=service.config.workspace_assistant,
        )

    @router.post("/auth/logout", status_code=204)
    async def logout(
        request: Request, response: Response, actor: LifecycleActor, service: Service
    ) -> None:
        await service.require_csrf(request, actor)
        await service.logout(actor)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=service.config.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        response.delete_cookie(
            CSRF_COOKIE, path="/", secure=service.config.secure_cookies, samesite="lax"
        )

    @router.post("/auth/password/change", response_model=MeOut)
    async def password_change(
        body: PasswordChangeIn,
        request: Request,
        response: Response,
        actor: LifecycleActor,
        service: Service,
    ) -> MeOut:
        await service.require_csrf(request, actor)
        await service._throttle(
            actor.email,
            request.client.host if request.client else "unknown",
            purpose="password-change",
        )
        changed, raw, csrf_token = await service.change_password(
            actor, body.current_password.get_secret_value(), body.new_password.get_secret_value()
        )
        service.set_cookies(response, raw, csrf_token)
        return changed.me(csrf_token, workspace_assistant=service.config.workspace_assistant)

    @router.post("/auth/password/reset", response_model=MessageOut, status_code=202)
    async def password_reset(body: EmailInput, request: Request, service: Service) -> MessageOut:
        await service.require_csrf(request)
        await service._throttle(
            body.email, request.client.host if request.client else "unknown", purpose="reset"
        )
        # Delivery is deliberately manual in M1: this public endpoint reveals no account
        # existence and creates no credential an unauthenticated caller can retrieve.
        return MessageOut(message="Contact your administrator for a password reset.")

    @router.post("/invites/inspect", response_model=TokenInfoOut)
    async def inspect_invite(body: TokenIn, request: Request, service: Service) -> TokenInfoOut:
        await service.require_csrf(request)
        await service._throttle(
            digest(body.token.get_secret_value()),
            request.client.host if request.client else "unknown",
            purpose="token-inspect",
        )
        return await service.inspect_token(body.token.get_secret_value())

    @router.post("/invites/accept", response_model=MeOut)
    async def accept_invite(
        body: TokenAcceptIn, request: Request, response: Response, service: Service
    ) -> MeOut:
        await service.require_csrf(request)
        await service._throttle(
            digest(body.token.get_secret_value()),
            request.client.host if request.client else "unknown",
            purpose="token",
        )
        actor, raw, csrf_token = await service.accept_token(
            body.token.get_secret_value(),
            body.password.get_secret_value(),
            body.display_name,
            previous_session=request.cookies.get(SESSION_COOKIE, ""),
        )
        service.set_cookies(response, raw, csrf_token)
        return actor.me(csrf_token, workspace_assistant=service.config.workspace_assistant)

    @router.post("/admin/invites", response_model=IssuedTokenOut, status_code=201)
    async def admin_invite(
        body: InviteIn, request: Request, actor: Mutation, service: Service
    ) -> IssuedTokenOut:
        service.require_platform(actor)
        return await _invite(request, body, actor, service)

    @router.post("/team/invites", response_model=IssuedTokenOut, status_code=201)
    async def team_invite(
        body: InviteIn, request: Request, actor: Mutation, service: Service
    ) -> IssuedTokenOut:
        return await _invite(request, body, actor, service)

    @router.get("/admin/invites", response_model=list[InvitationOut])
    async def invitation_list(
        actor: Actor,
        service: Service,
        client_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[Any]:
        return await service.invitations(actor, client_id, limit)

    @router.post("/admin/invites/{token_id}/renew", response_model=IssuedTokenOut, status_code=201)
    async def invitation_renew(
        token_id: UUID, request: Request, actor: Mutation, service: Service
    ) -> IssuedTokenOut:
        mailer = _mailer(request)
        if mailer is None:
            return await service.renew_invitation(actor, token_id)
        recipient = await service.invitation_recipient(actor, token_id)
        assert recipient.client_id is not None
        facts = await _invite_facts(request, actor, recipient.client_id)
        notify = _invitation_notify(
            mailer, actor, facts, to=recipient.email, invitee_name=recipient.display_name
        )
        return await service.renew_invitation(actor, token_id, notify=notify)

    @router.delete("/admin/invites/{token_id}", status_code=204)
    async def revoke_invite(token_id: UUID, actor: Mutation, service: Service) -> None:
        await service.revoke_token(actor, token_id)

    @router.get("/team", response_model=list[MemberOut])
    async def team(actor: Actor, service: Service, client_id: UUID) -> list[Any]:
        return await service.members(actor, client_id)

    @router.patch("/team/{user_id}", status_code=204)
    async def member_patch(
        user_id: UUID, body: MemberPatchIn, actor: Mutation, service: Service, client_id: UUID
    ) -> None:
        await service.patch_member(actor, client_id, user_id, body)

    @router.get("/admin/users", response_model=list[AdminUserOut])
    async def users(
        actor: Actor, service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 50
    ) -> list[Any]:
        return await service.users(actor, limit)

    @router.patch("/admin/users/{user_id}", status_code=204)
    async def user_patch(
        user_id: UUID, body: UserPatchIn, actor: Mutation, service: Service
    ) -> None:
        await service.set_user_enabled(actor, user_id, body.is_enabled)

    @router.post("/admin/users/{user_id}/reset", response_model=IssuedTokenOut, status_code=201)
    async def reset_issue(
        user_id: UUID, request: Request, actor: Mutation, service: Service
    ) -> IssuedTokenOut:
        mailer = _mailer(request)
        if mailer is None:
            return await service.issue_reset(actor, user_id)
        recipient = await service.reset_recipient(actor, user_id)

        async def notify(link: str, expires_at: datetime) -> MailOutcome:
            return await mailer.send(
                reset_mail(
                    to=recipient.email,
                    name=recipient.display_name,
                    link=link,
                    expires_at=expires_at,
                )
            )

        return await service.issue_reset(actor, user_id, notify=notify)

    @router.post("/admin/users/{user_id}/revoke-sessions", status_code=204)
    async def revoke_sessions(user_id: UUID, actor: Mutation, service: Service) -> None:
        await service.revoke_user_sessions(actor, user_id)

    @router.get("/admin/audit", response_model=list[AuditOut])
    async def audit(
        actor: Actor,
        service: Service,
        client_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[Any]:
        return await service.audit(actor, limit, client_id)

    return router
