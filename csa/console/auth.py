"""Human session HTTP routes, independently mounted from machine bearer routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

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
from csa.console.identity import CSRF_COOKIE, SESSION_COOKIE, IdentityService
from csa.console.security import digest


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
        return actor.me(csrf_token)

    @router.get("/auth/me", response_model=MeOut)
    async def me(request: Request, actor: LifecycleActor) -> MeOut:
        return actor.me(request.cookies.get(CSRF_COOKIE, ""))

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
        return changed.me(csrf_token)

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
        return actor.me(csrf_token)

    @router.post("/admin/invites", response_model=IssuedTokenOut, status_code=201)
    async def admin_invite(body: InviteIn, actor: Mutation, service: Service) -> IssuedTokenOut:
        service.require_platform(actor)
        return await service.invite(actor, body)

    @router.post("/team/invites", response_model=IssuedTokenOut, status_code=201)
    async def team_invite(body: InviteIn, actor: Mutation, service: Service) -> IssuedTokenOut:
        return await service.invite(actor, body)

    @router.get("/admin/invites", response_model=list[InvitationOut])
    async def invitation_list(
        actor: Actor,
        service: Service,
        client_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[Any]:
        return await service.invitations(actor, client_id, limit)

    @router.post("/admin/invites/{token_id}/renew", response_model=IssuedTokenOut, status_code=201)
    async def invitation_renew(token_id: UUID, actor: Mutation, service: Service) -> IssuedTokenOut:
        return await service.renew_invitation(actor, token_id)

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
    async def reset_issue(user_id: UUID, actor: Mutation, service: Service) -> IssuedTokenOut:
        return await service.issue_reset(actor, user_id)

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
