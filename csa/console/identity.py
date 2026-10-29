"""Identity-only asynchronous persistence and verified human session authorization.

The pool must log in as csa_identity. It never accesses domain tables. Domain
onboarding belongs to the tenant-scoped app pool, not this module.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, NoReturn
from urllib.parse import quote
from uuid import UUID

import psycopg
from fastapi import HTTPException, Request, Response
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from csa.console.contracts import (
    ConsoleActor,
    InviteIn,
    IssuedTokenOut,
    MemberPatchIn,
    MembershipOut,
    Role,
    TokenInfoOut,
)
from csa.console.mailer import MailOutcome
from csa.console.records import (
    MembershipRecord,
    SessionRecord,
    TokenDetails,
    TokenRecord,
    UserRecord,
)
from csa.console.security import digest, hash_password, token, verify_password
from csa.core.db import q

SESSION_COOKIE = "csa_session"
CSRF_COOKIE = "csa_csrf"
_FORGED_HEADERS = ("x-client-id", "x-actor-id", "x-actor-role", "x-user-id", "x-role")


class IdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    origins: tuple[str, ...] = Field(min_length=1)
    secure_cookies: bool = True
    session_idle_seconds: int = Field(default=1800, ge=60, le=86400)
    session_absolute_seconds: int = Field(default=43200, ge=60, le=604800)
    token_seconds: int = Field(default=172800, ge=60, le=604800)
    login_attempts: int = Field(default=5, ge=1, le=20)
    login_window_seconds: int = Field(default=900, ge=1, le=3600)
    # Mirrors WORKSPACE_ASSISTANT_ENABLED so /auth/me can tell the console to show the dock.
    workspace_assistant: bool = False


# Called with the raw link and its expiry after the token transaction commits.
Notify = Callable[[str, datetime], Awaitable[MailOutcome]]
Delivery = Literal["manual", "email"]


class Recipient(BaseModel):
    """Who a renewed invitation or a reset is mailed to; read before the mutation."""

    model_config = ConfigDict(frozen=True)
    client_id: UUID | None
    email: str
    display_name: str


class _ApproverRecheckFailed(Exception):
    """The approver changed between the first check and the success transaction."""


class IdentityService:
    def __init__(self, pool: AsyncConnectionPool, config: IdentityConfig):
        self.pool = pool
        self.config = config

    async def _one(self, conn: psycopg.AsyncConnection[Any], name: str, params: Any = None) -> Any:
        row = await q.fetch_one(f"console/identity/{name}.sql", params, conn=conn)
        model = {
            "user_email": UserRecord,
            "user_id": UserRecord,
            "create_user": UserRecord,
            "session_lookup": SessionRecord,
            "membership": MembershipRecord,
            "token_id": TokenRecord,
            "token_lookup": TokenDetails,
        }.get(name)
        if row is not None and model is not None:
            return model.model_validate(row).model_dump()
        return row

    async def _exec(
        self, conn: psycopg.AsyncConnection[Any], name: str, params: Any = None
    ) -> None:
        await q.execute(f"console/identity/{name}.sql", params, conn=conn)

    async def _context(
        self,
        conn: psycopg.AsyncConnection[Any],
        user_id: UUID | None,
        client_id: UUID | None = None,
    ) -> None:
        await self._exec(conn, "context", (str(user_id or ""), str(client_id or "")))

    async def _audit(
        self,
        conn: psycopg.AsyncConnection[Any],
        actor_id: UUID | None,
        action: str,
        target_id: UUID | str,
        client_id: UUID | None = None,
        **details: Any,
    ) -> None:
        await self._exec(
            conn, "audit", (actor_id, client_id, action, str(target_id), Jsonb(details))
        )

    def link(self, raw: str) -> str:
        """The console's existing link form; the token stays in the fragment, out of server logs."""
        return f"{self.config.origins[0]}/invite#token={quote(raw, safe='')}"

    async def _deliver(
        self,
        notify: Notify | None,
        raw: str,
        row: dict[str, Any],
        actor_id: UUID,
        action: str,
        target_id: UUID,
        client_id: UUID | None = None,
        **details: Any,
    ) -> IssuedTokenOut:
        """Send after commit, then audit the outcome; a failed send only means a manual link.

        The audit row needs the outcome, so it is written in its own transaction once the send
        has finished. The user and token are never rolled back or recreated by a mail failure.
        """
        delivery: Delivery = "manual"
        if notify is not None and await notify(self.link(raw), row["expires_at"]) == "sent":
            delivery = "email"
        async with self.pool.connection() as conn, conn.transaction():
            await self._context(conn, actor_id, client_id)
            await self._audit(
                conn, actor_id, action, target_id, client_id, **details, delivery=delivery
            )
        return IssuedTokenOut(
            id=row["id"],
            token=None if delivery == "email" else raw,
            expires_at=row["expires_at"],
            delivery=delivery,
        )

    async def check_ready(self) -> None:
        async with self.pool.connection() as conn:
            row = await self._one(conn, "readiness")
        checks = (
            "domain_isolated",
            "audit_immutable",
            "role_safe",
            "functions_isolated",
            "membership_isolated",
            "identity_access",
        )
        failed = [name for name in checks if not row or not row[name]]
        if not row or row["role"] != "csa_identity" or failed:
            raise RuntimeError("identity pool requires isolated csa_identity: " + ", ".join(failed))

    async def bootstrap(self, email: str, password: str, display_name: str) -> UUID:
        """Create the first platform admin only; the CLI requires deliberate consent."""
        from csa.console.contracts import EmailInput

        email = EmailInput(email=email).email
        if not 1 <= len(display_name) <= 120:
            raise ValueError("display name must contain between 1 and 120 characters")
        password_hash = await hash_password(password)
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            if (await self._one(conn, "bootstrap_count"))["count"]:
                raise ValueError("a platform administrator already exists; bootstrap is closed")
            user = await self._one(
                conn, "create_user", (email, display_name, password_hash, True, True)
            )
            await self._audit(conn, user["id"], "identity.bootstrap", user["id"])
            return user["id"]

    async def add_platform_admin(
        self,
        approver_email: str,
        approver_password: str,
        email: str,
        display_name: str,
        password: str,
        *,
        ip: str = "operator-cli",
    ) -> UUID:
        """Add a platform admin, approved by an existing one who re-proves their password."""
        from csa.console.contracts import EmailInput

        approver_email = EmailInput(email=approver_email).email
        email = EmailInput(email=email).email
        if not 1 <= len(display_name) <= 120:
            raise ValueError("display name must contain between 1 and 120 characters")
        await self._throttle(approver_email, ip, purpose="admin_add")
        async with self.pool.connection() as conn:
            approver = await self._one(conn, "user_email", (approver_email,))
        valid = await verify_password(
            approver_password, approver["password_hash"] if approver else None
        )
        if not (
            approver
            and valid
            and approver["is_enabled"]
            and approver["is_platform_admin"]
            and not approver["must_change_password"]
        ):
            await self._refuse_admin_add(approver_email)
        password_hash = await hash_password(password)
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await self._exec(conn, "lock_admin")
                current = await self._one(conn, "user_id", (approver["id"],))
                if not (
                    current
                    and current["is_enabled"]
                    and current["is_platform_admin"]
                    and not current["must_change_password"]
                    and current["password_hash"] == approver["password_hash"]
                ):
                    raise _ApproverRecheckFailed
                if await self._one(conn, "user_email", (email,)):
                    raise ValueError("a user with that email already exists")
                user = await self._one(
                    conn, "create_user", (email, display_name, password_hash, True, True)
                )
                await self._audit(
                    conn,
                    approver["id"],
                    "identity.platform_admin_added",
                    user["id"],
                    approver_email=approver_email,
                )
                return user["id"]
        except _ApproverRecheckFailed:
            # The transaction has rolled back and released its connection (the operator
            # script's pool holds one), so audit on a fresh one.
            await self._refuse_admin_add(approver_email)

    async def _refuse_admin_add(self, approver_email: str) -> NoReturn:
        # Callers must hold no pooled connection: the one-connection operator pool would stall.
        async with self.pool.connection() as conn, conn.transaction():
            await self._audit(
                conn, None, "identity.platform_admin_add_refused", digest(approver_email)
            )
        raise PermissionError(
            "approver must be an enabled platform administrator with a valid password"
        )

    async def resolve(self, raw_token: str, *, allow_password_change: bool = False) -> ConsoleActor:
        if not raw_token or len(raw_token) > 256:
            raise HTTPException(401, "authentication required")
        async with self.pool.connection() as conn, conn.transaction():
            row = await self._one(
                conn, "session_lookup", (digest(raw_token), self.config.session_idle_seconds)
            )
            if not row or not row["is_enabled"]:
                raise HTTPException(401, "authentication required")
            await self._context(conn, row["user_id"], row["client_id"])
            rows = await q.fetch_all(
                "console/identity/memberships.sql", (row["user_id"],), conn=conn
            )
            memberships = [MembershipOut.model_validate(item) for item in rows]
            role = (
                Role.PLATFORM_ADMIN
                if row["is_platform_admin"]
                else next(
                    (Role(m.role) for m in memberships if m.client_id == row["client_id"]), None
                )
            )
            if row["client_id"] is not None and role is None:
                raise HTTPException(401, "authentication required")
            actor = ConsoleActor(
                user_id=row["user_id"],
                email=row["email"],
                display_name=row["display_name"],
                platform_admin=row["is_platform_admin"],
                must_change_password=row["must_change_password"],
                session_id=row["session_id"],
                client_id=row["client_id"],
                role=role,
                memberships=memberships,
                csrf_hash=row["csrf_hash"],
            )
            if actor.must_change_password and not allow_password_change:
                raise HTTPException(403, "password_change_required")
            await self._exec(conn, "session_touch", (actor.session_id,))
            return actor

    async def actor_for_request(
        self, request: Request, *, allow_password_change: bool = False
    ) -> ConsoleActor:
        if any(name in request.headers for name in _FORGED_HEADERS):
            raise HTTPException(403, "browser identity headers are forbidden")
        return await self.resolve(
            request.cookies.get(SESSION_COOKIE, ""), allow_password_change=allow_password_change
        )

    async def require_csrf(self, request: Request, actor: ConsoleActor | None = None) -> None:
        origin = request.headers.get("origin", "")
        if origin not in self.config.origins:
            raise HTTPException(403, "invalid request origin")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        supplied = request.headers.get("x-csrf-token", "")
        if (
            not cookie
            or len(cookie) > 256
            or len(supplied) > 256
            or not hmac.compare_digest(cookie.encode("utf-8"), supplied.encode("utf-8"))
        ):
            raise HTTPException(403, "invalid csrf token")
        if actor is not None and not hmac.compare_digest(digest(supplied), actor.csrf_hash):
            raise HTTPException(403, "invalid csrf token")

    def set_cookies(self, response: Response, raw_session: str, csrf: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            raw_session,
            httponly=True,
            secure=self.config.secure_cookies,
            samesite="lax",
            path="/",
            max_age=self.config.session_absolute_seconds,
        )
        self.set_csrf_cookie(response, csrf)
        response.headers["Cache-Control"] = "no-store"

    def set_csrf_cookie(self, response: Response, csrf: str) -> None:
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            httponly=False,
            secure=self.config.secure_cookies,
            samesite="lax",
            path="/",
            max_age=self.config.session_absolute_seconds,
        )
        response.headers["Cache-Control"] = "no-store"

    async def csrf(self, request: Request, response: Response) -> str:
        raw = token()
        actor = None
        if request.cookies.get(SESSION_COOKIE):
            try:
                actor = await self.actor_for_request(request, allow_password_change=True)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                response.delete_cookie(
                    SESSION_COOKIE,
                    path="/",
                    secure=self.config.secure_cookies,
                    httponly=True,
                    samesite="lax",
                )
        if actor is not None:
            existing = request.cookies.get(CSRF_COOKIE, "")
            if existing and hmac.compare_digest(digest(existing), actor.csrf_hash):
                raw = existing
            else:
                async with self.pool.connection() as conn, conn.transaction():
                    await self._exec(conn, "csrf_update", (digest(raw), actor.session_id))
        self.set_csrf_cookie(response, raw)
        return raw

    async def _throttle(self, email: str, ip: str, *, purpose: str = "login") -> None:
        blocked = False
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "login_cleanup", (self.config.login_window_seconds * 2,))
            for bucket, limit in (
                (f"{purpose}:email:{email}", self.config.login_attempts),
                (f"{purpose}:ip:{ip}", self.config.login_attempts * 10),
            ):
                row = await self._one(
                    conn,
                    "login_attempt",
                    (
                        digest(bucket),
                        self.config.login_window_seconds,
                        self.config.login_window_seconds,
                    ),
                )
                blocked = blocked or row["attempts"] > limit
        if blocked:
            raise HTTPException(
                429,
                "too many attempts",
                headers={"Retry-After": str(self.config.login_window_seconds)},
            )

    async def _create_session(
        self, conn: psycopg.AsyncConnection[Any], user_id: UUID, platform: bool
    ) -> tuple[str, str]:
        await self._context(conn, user_id)
        memberships = await q.fetch_all("console/identity/memberships.sql", (user_id,), conn=conn)
        client_id = None if platform or not memberships else memberships[0]["client_id"]
        raw, csrf = token(), token()
        await self._one(
            conn,
            "session_create",
            (user_id, digest(raw), digest(csrf), client_id, self.config.session_absolute_seconds),
        )
        return raw, csrf

    async def login(
        self, email: str, password: str, ip: str, *, previous_session: str = ""
    ) -> tuple[ConsoleActor, str, str]:
        await self._throttle(email, ip)
        async with self.pool.connection() as conn:
            user = await self._one(conn, "user_email", (email,))
        valid = await verify_password(password, user["password_hash"] if user else None)
        failed = not user or not valid or not user["is_enabled"]
        raw, csrf = "", ""
        async with self.pool.connection() as conn, conn.transaction():
            if not failed:
                current = await self._one(conn, "user_id", (user["id"],))
                failed = (
                    not current["is_enabled"] or current["password_hash"] != user["password_hash"]
                )
            if failed:
                await self._audit(conn, None, "identity.login_failed", digest(email))
            else:
                if previous_session:
                    await self._exec(conn, "session_revoke_token", (digest(previous_session),))
                raw, csrf = await self._create_session(conn, user["id"], user["is_platform_admin"])
                await self._exec(conn, "login_clear", (digest(f"login:email:{email}"),))
                await self._audit(conn, user["id"], "identity.login", user["id"])
        if failed:
            raise HTTPException(401, "invalid email or password")
        return await self.resolve(raw, allow_password_change=True), raw, csrf

    async def logout(self, actor: ConsoleActor) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "session_revoke", (actor.session_id,))
            await self._audit(conn, actor.user_id, "identity.logout", actor.user_id)

    async def change_password(
        self, actor: ConsoleActor, current: str, new: str
    ) -> tuple[ConsoleActor, str, str]:
        async with self.pool.connection() as conn:
            user = await self._one(conn, "user_id", (actor.user_id,))
        valid = await verify_password(current, user["password_hash"])
        if hmac.compare_digest(current.encode("utf-8"), new.encode("utf-8")):
            raise HTTPException(422, "new password must differ from the current password")
        if not valid:
            raise HTTPException(401, "invalid current password")
        password_hash = await hash_password(new)
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            locked = await self._one(conn, "user_id", (actor.user_id,))
            if not locked["is_enabled"] or locked["password_hash"] != user["password_hash"]:
                raise HTTPException(401, "authentication required")
            await self._exec(conn, "password_update", (password_hash, actor.user_id))
            await self._exec(conn, "sessions_revoke", (actor.user_id,))
            await self._exec(conn, "user_tokens_revoke", (actor.user_id,))
            raw, csrf = await self._create_session(conn, actor.user_id, actor.platform_admin)
            await self._audit(conn, actor.user_id, "identity.password_changed", actor.user_id)
        return await self.resolve(raw), raw, csrf

    @staticmethod
    def require_platform(actor: ConsoleActor) -> None:
        if not actor.platform_admin:
            raise HTTPException(403, "platform administrator required")

    @staticmethod
    def require_member_admin(actor: ConsoleActor, client_id: UUID) -> None:
        if actor.platform_admin:
            return
        if actor.client_id != client_id or actor.role != Role.TENANT_ADMIN:
            raise HTTPException(403, "tenant administrator required")

    async def invite(
        self,
        actor: ConsoleActor,
        data: InviteIn,
        *,
        may_grant_hr: bool = False,
        notify: Notify | None = None,
    ) -> IssuedTokenOut:
        self.require_member_admin(actor, data.client_id)
        # Any inviting admin may choose none or Staff; the other presets need the tenant's HR owner.
        if data.hr_preset not in (None, "staff") and not may_grant_hr:
            raise HTTPException(403, "hr_preset_requires_hr_owner")
        raw = token()
        try:
            async with self.pool.connection() as conn, conn.transaction():
                await self._exec(conn, "lock_admin")
                await self._context(conn, actor.user_id, data.client_id)
                existing = await self._one(conn, "user_email", (data.email,))
                # Never let a tenant administrator replace another account's password.
                if existing:
                    raise HTTPException(409, "account already exists; use member administration")
                user = await self._one(
                    conn, "create_user", (data.email, data.display_name, None, False, False)
                )
                result = await self._one(
                    conn,
                    "token_create",
                    (
                        user["id"],
                        data.client_id,
                        data.role,
                        "invite",
                        digest(raw),
                        self.config.token_seconds,
                        actor.user_id,
                        data.hr_preset,
                        actor.user_id if data.hr_preset else None,
                    ),
                )
        except psycopg.errors.ForeignKeyViolation as exc:
            raise HTTPException(404, "tenant not found") from exc
        return await self._deliver(
            notify,
            raw,
            result,
            actor.user_id,
            "identity.invited",
            user["id"],
            data.client_id,
            role=data.role,
        )

    async def invitations(
        self, actor: ConsoleActor, client_id: UUID | None, limit: int
    ) -> list[Any]:
        if client_id is None:
            self.require_platform(actor)
        else:
            self.require_member_admin(actor, client_id)
        async with self.pool.connection() as conn, conn.transaction():
            await self._context(conn, actor.user_id, client_id)
            return await q.fetch_all(
                "console/identity/token_list.sql", (client_id, limit), conn=conn
            )

    async def invitation_recipient(self, actor: ConsoleActor, token_id: UUID) -> Recipient:
        async with self.pool.connection() as conn, conn.transaction():
            old = await self._one(conn, "token_id", (token_id,))
            if not old or old["kind"] != "invite" or old["consumed_at"] is not None:
                raise HTTPException(404, "pending invitation not found")
            self.require_member_admin(actor, old["client_id"])
            user = await self._one(conn, "user_id", (old["user_id"],))
        return Recipient(
            client_id=old["client_id"], email=user["email"], display_name=user["display_name"]
        )

    async def reset_recipient(self, actor: ConsoleActor, user_id: UUID) -> Recipient:
        self.require_platform(actor)
        async with self.pool.connection() as conn, conn.transaction():
            user = await self._one(conn, "user_id", (user_id,))
        if not user or not user["is_enabled"]:
            raise HTTPException(404, "user not found")
        return Recipient(client_id=None, email=user["email"], display_name=user["display_name"])

    async def renew_invitation(
        self, actor: ConsoleActor, token_id: UUID, *, notify: Notify | None = None
    ) -> IssuedTokenOut:
        raw = token()
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            old = await self._one(conn, "token_id", (token_id,))
            if not old or old["kind"] != "invite" or old["consumed_at"] is not None:
                raise HTTPException(404, "pending invitation not found")
            self.require_member_admin(actor, old["client_id"])
            user = await self._one(conn, "user_id", (old["user_id"],))
            if user["password_hash"] is not None or not user["is_enabled"]:
                raise HTTPException(409, "invitation account is unavailable")
            await self._context(conn, actor.user_id, old["client_id"])
            await self._exec(conn, "user_tokens_revoke", (old["user_id"],))
            row = await self._one(
                conn,
                "token_create",
                (
                    old["user_id"],
                    old["client_id"],
                    old["role"],
                    "invite",
                    digest(raw),
                    self.config.token_seconds,
                    actor.user_id,
                    # The chooser stays whoever picked it, not whoever renews.
                    old["hr_preset"],
                    old["hr_preset_chosen_by"],
                ),
            )
        return await self._deliver(
            notify,
            raw,
            row,
            actor.user_id,
            "identity.invitation_renewed",
            row["id"],
            old["client_id"],
            previous_token_id=str(token_id),
        )

    async def inspect_token(self, raw: str) -> TokenInfoOut:
        async with self.pool.connection() as conn, conn.transaction():
            row = await self._one(conn, "token_lookup", (digest(raw),))
            if not row or not row["is_enabled"]:
                raise HTTPException(400, "invalid or expired token")
            return TokenInfoOut.model_validate({key: row[key] for key in TokenInfoOut.model_fields})

    async def accept_token(
        self,
        raw_token: str,
        password: str,
        display_name: str | None = None,
        *,
        previous_session: str = "",
    ) -> tuple[ConsoleActor, str, str]:
        password_hash = await hash_password(password)
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            row = await self._one(conn, "token_lookup", (digest(raw_token),))
            if not row or not row["is_enabled"]:
                raise HTTPException(400, "invalid or expired token")
            await self._context(conn, row["user_id"], row["client_id"])
            if row["kind"] == "invite":
                if row["password_hash"] is not None or row["is_platform_admin"]:
                    raise HTTPException(400, "invalid invitation")
                await self._exec(
                    conn, "member_insert", (row["user_id"], row["client_id"], row["role"])
                )
            await self._exec(conn, "password_update", (password_hash, row["user_id"]))
            if display_name:
                await self._exec(conn, "display_name_update", (display_name, row["user_id"]))
            await self._exec(conn, "token_consume", (row["id"],))
            await self._exec(conn, "user_tokens_revoke", (row["user_id"],))
            await self._exec(conn, "sessions_revoke", (row["user_id"],))
            if previous_session:
                await self._exec(conn, "session_revoke_token", (digest(previous_session),))
            raw, csrf = await self._create_session(conn, row["user_id"], row["is_platform_admin"])
            await self._context(conn, row["user_id"], row["client_id"])
            await self._audit(
                conn,
                row["user_id"],
                f"identity.{row['kind']}_accepted",
                row["user_id"],
                row["client_id"],
            )
        return await self.resolve(raw), raw, csrf

    async def issue_reset(
        self, actor: ConsoleActor, user_id: UUID, *, notify: Notify | None = None
    ) -> IssuedTokenOut:
        self.require_platform(actor)
        raw = token()
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            user = await self._one(conn, "user_id", (user_id,))
            if not user or not user["is_enabled"]:
                raise HTTPException(404, "user not found")
            if user["password_hash"] is None:
                raise HTTPException(409, "accept or renew the invitation before resetting password")
            await self._exec(conn, "user_tokens_revoke", (user_id,))
            await self._exec(conn, "sessions_revoke", (user_id,))
            await self._exec(conn, "force_password", (user_id,))
            row = await self._one(
                conn,
                "token_create",
                (
                    user_id,
                    None,
                    None,
                    "reset",
                    digest(raw),
                    self.config.token_seconds,
                    actor.user_id,
                    None,
                    None,
                ),
            )
        return await self._deliver(
            notify, raw, row, actor.user_id, "identity.reset_issued", user_id
        )

    async def revoke_token(self, actor: ConsoleActor, token_id: UUID) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            row = await self._one(conn, "token_id", (token_id,))
            if not row:
                raise HTTPException(404, "token not found")
            if row["kind"] == "reset":
                self.require_platform(actor)
            else:
                self.require_member_admin(actor, row["client_id"])
            await self._context(conn, actor.user_id, row["client_id"])
            await self._exec(conn, "token_revoke", (token_id,))
            await self._audit(
                conn, actor.user_id, "identity.token_revoked", token_id, row["client_id"]
            )

    async def members(self, actor: ConsoleActor, client_id: UUID) -> list[Any]:
        self.require_member_admin(actor, client_id)
        async with self.pool.connection() as conn, conn.transaction():
            await self._context(conn, actor.user_id, client_id)
            return await q.fetch_all("console/identity/member_list.sql", (client_id,), conn=conn)

    async def patch_member(
        self, actor: ConsoleActor, client_id: UUID, user_id: UUID, data: MemberPatchIn
    ) -> None:
        self.require_member_admin(actor, client_id)
        if data.role is None and data.is_enabled is None:
            raise HTTPException(422, "a member change is required")
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            await self._context(conn, actor.user_id, client_id)
            row = await self._one(conn, "membership", (user_id, client_id))
            if not row:
                raise HTTPException(404, "member not found")
            removing_admin = data.is_enabled is False or (
                data.role is not None and data.role != "tenant_admin"
            )
            if row["role"] == "tenant_admin" and row["is_enabled"] and removing_admin:
                count = await self._one(conn, "member_count", (client_id,))
                if count["count"] <= 1:
                    raise HTTPException(409, "cannot remove the last tenant administrator")
            await self._exec(conn, "member_patch", (data.role, data.is_enabled, user_id, client_id))
            await self._exec(conn, "member_sessions_revoke", (user_id, client_id))
            await self._audit(
                conn,
                actor.user_id,
                "identity.member_changed",
                user_id,
                client_id,
                before={"role": row["role"], "is_enabled": row["is_enabled"]},
                after=data.model_dump(exclude_none=True),
            )

    async def users(self, actor: ConsoleActor, limit: int) -> list[Any]:
        self.require_platform(actor)
        async with self.pool.connection() as conn:
            return await q.fetch_all("console/identity/user_list.sql", (limit,), conn=conn)

    async def set_user_enabled(self, actor: ConsoleActor, user_id: UUID, enabled: bool) -> None:
        self.require_platform(actor)
        async with self.pool.connection() as conn, conn.transaction():
            await self._exec(conn, "lock_admin")
            user = await self._one(conn, "user_id", (user_id,))
            if not user:
                raise HTTPException(404, "user not found")
            if not enabled and user["is_enabled"]:
                if (
                    user["is_platform_admin"]
                    and (await self._one(conn, "platform_admin_count"))["count"] <= 1
                ):
                    raise HTTPException(409, "cannot disable the last platform administrator")
                await self._context(conn, user_id)
                memberships = await q.fetch_all(
                    "console/identity/memberships.sql", (user_id,), conn=conn
                )
                for membership in memberships:
                    if membership["role"] == "tenant_admin":
                        await self._context(conn, actor.user_id, membership["client_id"])
                        if (await self._one(conn, "member_count", (membership["client_id"],)))[
                            "count"
                        ] <= 1:
                            raise HTTPException(409, "cannot disable the last tenant administrator")
            await self._exec(conn, "user_patch", (enabled, user_id))
            if not enabled:
                await self._exec(conn, "sessions_revoke", (user_id,))
                await self._exec(conn, "user_tokens_revoke", (user_id,))
            await self._context(conn, actor.user_id)
            await self._audit(
                conn, actor.user_id, "identity.user_enabled_changed", user_id, is_enabled=enabled
            )

    async def revoke_user_sessions(self, actor: ConsoleActor, user_id: UUID) -> None:
        self.require_platform(actor)
        async with self.pool.connection() as conn, conn.transaction():
            if not await self._one(conn, "user_id", (user_id,)):
                raise HTTPException(404, "user not found")
            await self._exec(conn, "sessions_revoke", (user_id,))
            await self._audit(conn, actor.user_id, "identity.sessions_revoked", user_id)

    async def audit(self, actor: ConsoleActor, limit: int, client_id: UUID | None) -> list[Any]:
        self.require_platform(actor)
        async with self.pool.connection() as conn, conn.transaction():
            await self._context(conn, actor.user_id, client_id)
            return await q.fetch_all("console/identity/audit_list.sql", (limit,), conn=conn)
