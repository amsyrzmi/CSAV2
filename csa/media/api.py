"""The console read route for pictures: one route, the owning thing's own read rule.

`GET /console/v1/media/{asset_id}` returns the picture's bytes when the signed-in person may read
the thing the picture belongs to. The rule is chosen by the picture's kind from `READ_RULES`.
Every refusal (unknown id, another company's id, a role that may not read, a package that is off,
a kind served elsewhere) is the same `404 {"detail": "picture_not_found"}`, so the route never
reveals whether a picture exists. Machine tokens are refused (403) before anything is read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Role
from csa.console.services import require_package
from csa.core.tenancy import tx
from csa.media import service
from csa.media.contracts import PictureKind
from csa.subscriptions.api import human_session

__all__ = ["READ_RULES", "ReadRule", "build_media_router", "can_read"]

STAFF_ROLES = frozenset({Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF})

_NOT_FOUND = "picture_not_found"
_CACHE_CONTROL = "private, no-cache"
_BINARY: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {
            "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
            "image/png": {"schema": {"type": "string", "format": "binary"}},
        }
    }
}


@dataclass(frozen=True)
class ReadRule:
    """Who may read a kind: `roles` (None = any same-company human) and any-of `packages`."""

    roles: frozenset[Role] | None
    packages: frozenset[str]


# A kind with no rule is never served by this route (a training thumbnail is served only by the
# training route, so "readers see only ready items" holds).
READ_RULES: dict[PictureKind, ReadRule | None] = {
    "payment_qr": ReadRule(STAFF_ROLES, frozenset({"subscriptions"})),
    "plan": ReadRule(None, frozenset({"subscriptions"})),
    "product": ReadRule(STAFF_ROLES, frozenset({"orders_products", "marketplaces"})),
    "training_thumbnail": None,
}


def can_read(actor: ConsoleActor, kind: PictureKind) -> bool:
    """The role half of the rule (a platform admin passes it); the package half is checked apart."""
    rule = READ_RULES.get(kind)
    if rule is None:
        return False
    return rule.roles is None or actor.platform_admin or actor.role in rule.roles


def _matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == etag:
            return True
    return False


def build_media_router() -> APIRouter:
    """The picture read route (human sessions only; see the module docstring)."""
    router = APIRouter(
        prefix="/console/v1/media", tags=["Media"], dependencies=[Depends(human_session)]
    )

    @router.get("/{asset_id}", response_class=Response, responses=_BINARY)
    async def read_picture(
        request: Request,
        asset_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        """The picture's bytes (404 `picture_not_found` for anything the viewer may not read).

        `ETag` is the picture's SHA-256 and changes when it is replaced; `If-None-Match` gives 304.
        """
        pool = request.app.state.pool
        actor: ConsoleActor = request.state.console_actor
        async with tx(pool, str(client_id)) as conn:
            stored = await service.get(conn, client_id, asset_id)
        if stored is None or not can_read(actor, stored.kind):
            raise HTTPException(404, _NOT_FOUND)
        rule = READ_RULES[stored.kind]
        assert rule is not None  # can_read is False for a kind without a rule
        try:
            await require_package(pool, client_id, rule.packages)
        except HTTPException as refused:
            if refused.status_code != 403:
                raise
            raise HTTPException(404, _NOT_FOUND) from None
        etag = f'"{stored.sha256}"'
        headers = {
            "ETag": etag,
            "Cache-Control": _CACHE_CONTROL,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        }
        if _matches(if_none_match, etag):
            return Response(status_code=304, headers=headers)
        return Response(content=stored.data, media_type=stored.media_type, headers=headers)

    return router
