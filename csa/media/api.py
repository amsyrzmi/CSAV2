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
from csa.media.http import etag, etag_matches, picture_headers
from csa.subscriptions.api import human_session

__all__ = ["READ_RULES", "ReadRule", "build_media_router", "can_read"]

STAFF_ROLES = frozenset({Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF})

_NOT_FOUND = "picture_not_found"
_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The picture's bytes.",
        "content": {
            "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
            "image/png": {"schema": {"type": "string", "format": "binary"}},
        },
    },
    304: {"description": "Not modified: `If-None-Match` names the current `ETag`."},
    404: {
        "description": "`picture_not_found`: unknown, another company's, or not readable.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["detail"],
                    "properties": {"detail": {"type": "string", "enum": [_NOT_FOUND]}},
                }
            }
        },
    },
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


def build_media_router() -> APIRouter:
    """The picture read route (human sessions only; see the module docstring)."""
    router = APIRouter(
        prefix="/console/v1/media", tags=["Media"], dependencies=[Depends(human_session)]
    )

    @router.get("/{asset_id}", response_class=Response, responses=_RESPONSES)
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
        # The rule and the ETag comparison run on the metadata; the bytes are read only for a 200.
        async with tx(pool, str(client_id)) as conn:
            meta = await service.get_meta(conn, client_id, asset_id)
        if meta is None or not can_read(actor, meta.kind):
            raise HTTPException(404, _NOT_FOUND)
        rule = READ_RULES[meta.kind]
        assert rule is not None  # can_read is False for a kind without a rule
        try:
            await require_package(pool, client_id, rule.packages)
        except HTTPException as refused:
            if refused.status_code != 403:
                raise
            raise HTTPException(404, _NOT_FOUND) from None
        if etag_matches(if_none_match, etag(meta.sha256)):
            return Response(status_code=304, headers=picture_headers(meta.sha256))
        async with tx(pool, str(client_id)) as conn:
            stored = await service.get(conn, client_id, asset_id)
        # Removed since the metadata read: 404. Replaced (same id, same subject, so the same rule):
        # the headers follow the bytes actually sent.
        if stored is None:
            raise HTTPException(404, _NOT_FOUND)
        return Response(
            content=stored.data,
            media_type=stored.media_type,
            headers=picture_headers(stored.sha256),
        )

    return router
