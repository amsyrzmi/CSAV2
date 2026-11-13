"""Staff routes to link and unlink contacts and to settle link reviews.

Mounted by :func:`csa.subscriptions.api.build_subscriptions_router` under its prefix, so the
router-level dependencies (human session, the ``subscriptions`` package gate), the read and write
guards and the static-before-``/{subscription_id}`` order all hold. The linking rules live in
:mod:`csa.contacts.linking`; this module maps them to HTTP, scopes every statement to the
session's company and audits each write with the acting user.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.console.access import tenant
from csa.contacts import linking
from csa.contacts.contracts import (
    LinkedContact,
    LinkIn,
    LinkOut,
    ResolveIn,
    ReviewOut,
    UnlinkResultOut,
)
from csa.core import db
from csa.core.tenancy import tx

Conn = AsyncConnection[Any]


def _contact(row: dict[str, Any], prefix: str) -> LinkedContact:
    return LinkedContact(
        id=row[f"{prefix}_id"],
        full_name=row[f"{prefix}_full_name"],
        phone=row[f"{prefix}_phone"],
        channels=row[f"{prefix}_channels"],
    )


def _link_out(row: dict[str, Any]) -> LinkOut:
    return LinkOut(
        id=row["id"],
        survivor=_contact(row, "survivor"),
        loser=_contact(row, "loser"),
        reason=row["reason"],
        created_at=row["created_at"],
        unlinked_at=row["unlinked_at"],
        can_unlink=row["can_unlink"],
    )


def _review_out(row: dict[str, Any]) -> ReviewOut:
    return ReviewOut(
        id=row["id"],
        contact_a=_contact(row, "a"),
        contact_b=_contact(row, "b"),
        reason=row["reason"],
        status=row["status"],
        resolution=row["resolution"],
        created_at=row["created_at"],
    )


async def _links(
    conn: Conn,
    client_id: UUID,
    *,
    link_id: UUID | None = None,
    contact_id: UUID | None = None,
    limit: int = 100,
) -> list[LinkOut]:
    rows = await db.q.fetch_all(
        "contacts/links_list.sql",
        {"client_id": client_id, "link_id": link_id, "contact_id": contact_id, "limit": limit},
        conn=conn,
    )
    return [_link_out(row) for row in rows]


async def _reviews(
    conn: Conn,
    client_id: UUID,
    *,
    review_id: UUID | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[ReviewOut]:
    rows = await db.q.fetch_all(
        "contacts/reviews_list.sql",
        {"client_id": client_id, "review_id": review_id, "status": status, "limit": limit},
        conn=conn,
    )
    return [_review_out(row) for row in rows]


async def _audit(
    conn: Conn,
    actor_user_id: UUID,
    client_id: UUID,
    action: str,
    target_id: UUID,
    details: dict[str, Any],
) -> None:
    await db.q.execute(
        "console/audit.sql",
        (actor_user_id, client_id, action, str(target_id), Jsonb(details)),
        conn=conn,
    )


async def _link_pair(
    conn: Conn, client_id: UUID, actor_user_id: UUID, contact_a: UUID, contact_b: UUID
) -> UUID:
    """Link two contacts as staff; a refusal is 409 ``link_refused:<code>`` and changes nothing."""
    outcome = await linking.link_contacts(
        conn, client_id, contact_a, contact_b, reason="staff", actor_user_id=actor_user_id
    )
    if outcome.refused is not None:
        raise HTTPException(409, f"link_refused:{outcome.refused}")
    assert outcome.link_id is not None
    return outcome.link_id


def build_links_router() -> APIRouter:
    """Link and review routes without a prefix; the subscriptions router adds it and the gates."""
    # The subscriptions guards import this module, so they are imported when the router is built.
    from csa.subscriptions.api import require_subscriptions_read, require_subscriptions_write

    router = APIRouter()

    def reader(request: Request, client_id: UUID) -> None:
        require_subscriptions_read(request.state.console_actor, client_id)

    def writer(request: Request, client_id: UUID) -> UUID:
        actor = request.state.console_actor
        require_subscriptions_write(actor, client_id)
        return actor.user_id  # type: ignore[no-any-return]

    @router.get("/links", response_model=list[LinkOut])
    async def list_links(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        contact_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[LinkOut]:
        """Links, newest first; with ``contact_id`` those where the contact is survivor or loser.

        ``channels`` is the contact's identity channels now, plus, for the loser of a link not yet
        undone, the channels its identities had before the link moved them. 404
        ``contact_not_found`` for a contact of another company.
        """
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if contact_id is not None:
                known = await db.q.fetch_one(
                    "contacts/contact_live.sql",
                    {"client_id": client_id, "contact_id": contact_id},
                    conn=conn,
                )
                if known is None:
                    raise HTTPException(404, "contact_not_found")
            return await _links(conn, client_id, contact_id=contact_id, limit=limit)

    @router.post("/links", response_model=LinkOut, status_code=201)
    async def create_link(
        request: Request, payload: LinkIn, client_id: Annotated[UUID, Depends(tenant)]
    ) -> LinkOut:
        """Link two contacts of the company (reason ``staff``); the survivor is picked for them."""
        actor_user_id = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            link_id = await _link_pair(
                conn, client_id, actor_user_id, payload.contact_a, payload.contact_b
            )
            [made] = await _links(conn, client_id, link_id=link_id)
            await _audit(
                conn,
                actor_user_id,
                client_id,
                "contacts.link.created",
                link_id,
                {"survivor": str(made.survivor.id), "loser": str(made.loser.id)},
            )
            return made

    @router.post("/links/{link_id}/unlink", response_model=UnlinkResultOut)
    async def unlink(
        request: Request, link_id: UUID, client_id: Annotated[UUID, Depends(tenant)]
    ) -> UnlinkResultOut:
        """Undo a link; ``skipped`` names the contact columns it could not put back."""
        actor_user_id = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            undone = await linking.unlink_contacts(conn, client_id, link_id, actor_user_id)
            await _audit(
                conn,
                actor_user_id,
                client_id,
                "contacts.link.removed",
                link_id,
                {"skipped": undone.skipped},
            )
            return UnlinkResultOut(skipped=undone.skipped)

    @router.get("/link-reviews", response_model=list[ReviewOut])
    async def list_reviews(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        status: Literal["open", "resolved"] = "open",
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[ReviewOut]:
        """Pairs of contacts the system could not link by itself, newest first."""
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await _reviews(conn, client_id, status=status, limit=limit)

    @router.post("/link-reviews/{review_id}/resolve", response_model=ReviewOut)
    async def resolve_review(
        request: Request,
        review_id: UUID,
        payload: ResolveIn,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> ReviewOut:
        """Dismiss a review, or link its pair as staff (a refusal is 409 ``link_refused:<code>``).

        404 ``review_not_found``; 409 ``review_not_open``. A pair with a merged contact cannot be
        linked (409 ``contact_merged``); dismiss it instead.
        """
        actor_user_id = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            [review] = await _reviews(conn, client_id, review_id=review_id) or [None]
            if review is None:
                raise HTTPException(404, "review_not_found")
            if review.status != "open":
                raise HTTPException(409, "review_not_open")
            details: dict[str, Any] = {}
            if payload.resolution == "dismissed":
                dismissed = await db.q.fetch_one(
                    "contacts/review_dismiss.sql",
                    {
                        "client_id": client_id,
                        "review_id": review_id,
                        "resolved_by": actor_user_id,
                    },
                    conn=conn,
                )
                if dismissed is None:  # settled by someone else since it was read
                    raise HTTPException(409, "review_not_open")
                action = "contacts.review.dismissed"
            else:
                # link_contacts also marks the pair's open review resolved as ``linked``. The
                # review row is not locked first: verify_phone takes contact locks and then writes
                # a review, so the other order could deadlock.
                link_id = await _link_pair(
                    conn, client_id, actor_user_id, review.contact_a.id, review.contact_b.id
                )
                action = "contacts.review.linked"
                details = {"link_id": str(link_id)}
            await _audit(conn, actor_user_id, client_id, action, review_id, details)
            [settled] = await _reviews(conn, client_id, review_id=review_id)
            return settled

    return router
