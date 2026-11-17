"""The Inbox saved views (design 2026-11-16, section 6.1): a person's named filter sets.

A view is private to the person who made it, in the company it was made in: every read and write
is keyed by ``(client_id, user_id)`` on the tenant transaction, so another person's view, or the
same person's view in another company, is 404. ``filters`` is :class:`InboxFilters`, the list's
own filter model without the search, cursor and limit, so a stored filter is always one the list
accepts (an invalid one is 422 through FastAPI's body validation).

At most ``VIEWS_MAX`` views per person and company, and a name unique per person ignoring case
(the rule of the unique index ``inbox_saved_views_name_key``). Both are checked under an advisory
lock for the person, so two requests at once cannot both pass: a double submit makes one view and
one 409. Audit rows ``inbox.view_created``, ``inbox.view_updated`` and ``inbox.view_deleted``
carry the view id as the target and no details.

Roles: the router's ``inbox_actor`` (tenant staff, tenant admins, platform admins acting for the
company they select); read-only is 403.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import StringConstraints, model_validator

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract
from csa.console.inbox import inbox_actor
from csa.console.inbox_queue_list import InboxFilters
from csa.console.kb_uploads import audit
from csa.core import db
from csa.core.tenancy import tx

VIEWS_MAX = 20
ViewName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class InboxSavedViewIn(Contract):
    """A new view: a name (1 to 60 characters once trimmed) and the filters to keep."""

    name: ViewName
    filters: InboxFilters


class InboxSavedViewChange(Contract):
    """A rename, a replacement of the filters, or both; at least one of the two."""

    name: ViewName | None = None
    filters: InboxFilters | None = None

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if self.name is None and self.filters is None:
            raise ValueError("give a new name, new filters or both")
        return self


class InboxSavedView(Contract):
    """A saved view as the person reads it back."""

    id: UUID
    name: str
    filters: InboxFilters
    created_at: datetime


def _view(row: dict[str, Any]) -> InboxSavedView:
    return InboxSavedView.model_validate(row)


def _stored(filters: InboxFilters) -> Jsonb:
    """The filters as stored: only what differs from "no filter", so later defaults apply."""
    return Jsonb(filters.model_dump(mode="json", exclude_defaults=True))


def _keys(client_id: UUID, actor: ConsoleActor) -> dict[str, Any]:
    return {"client_id": client_id, "user_id": actor.user_id}


async def _checked(
    conn: AsyncConnection[Any],
    client_id: UUID,
    actor: ConsoleActor,
    *,
    name: str | None,
    view_id: UUID | None,
) -> dict[str, Any]:
    """The person's view count, name clash and the view's presence, read under their lock."""
    await db.q.fetch_one(
        "inbox/lock_request.sql", (f"inbox:views:{client_id}:{actor.user_id}",), conn=conn
    )
    row = await db.q.fetch_one(
        "inbox/saved_view_check.sql",
        {**_keys(client_id, actor), "name": name, "view_id": view_id},
        conn=conn,
    )
    if row is None:
        raise RuntimeError("saved_view_check_returned_no_row")
    if view_id is not None and not row["found"]:
        raise _not_found()
    if row["name_taken"]:
        raise HTTPException(409, "you already have a saved view with this name")
    return row


def _not_found() -> HTTPException:
    return HTTPException(404, "saved view not found")


def add_saved_view_routes(router: APIRouter) -> None:
    """Add the saved view routes to the Inbox router (``build_inbox_router``)."""

    @router.get("/views", response_model=list[InboxSavedView])
    async def saved_views(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(inbox_actor)],
    ) -> list[InboxSavedView]:
        """The asking person's saved views in this company, newest first."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "inbox/saved_view_list.sql", _keys(client_id, actor), conn=conn
            )
        return [_view(row) for row in rows]

    @router.post("/views", response_model=InboxSavedView, status_code=201)
    async def create_saved_view(
        request: Request,
        payload: InboxSavedViewIn,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(inbox_actor)],
    ) -> InboxSavedView:
        """Save the filters under a name; 409 for a name the person already uses (ignoring
        case) or when they already have 20 views."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            check = await _checked(conn, client_id, actor, name=payload.name, view_id=None)
            if check["n"] >= VIEWS_MAX:
                raise HTTPException(409, f"you can keep at most {VIEWS_MAX} saved views")
            row = await db.q.fetch_one(
                "inbox/saved_view_insert.sql",
                {
                    **_keys(client_id, actor),
                    "name": payload.name,
                    "filters": _stored(payload.filters),
                },
                conn=conn,
            )
            view = _view(row)
            await audit(conn, actor, client_id, "inbox.view_created", str(view.id))
        return view

    @router.put("/views/{view_id}", response_model=InboxSavedView)
    async def change_saved_view(
        request: Request,
        view_id: UUID,
        payload: InboxSavedViewChange,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(inbox_actor)],
    ) -> InboxSavedView:
        """Rename the view, replace its filters, or both; the create's name rule applies."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await _checked(conn, client_id, actor, name=payload.name, view_id=view_id)
            row = await db.q.fetch_one(
                "inbox/saved_view_update.sql",
                {
                    **_keys(client_id, actor),
                    "view_id": view_id,
                    "name": payload.name,
                    "filters": _stored(payload.filters) if payload.filters is not None else None,
                },
                conn=conn,
            )
            if row is None:
                raise _not_found()
            view = _view(row)
            await audit(conn, actor, client_id, "inbox.view_updated", str(view.id))
        return view

    @router.delete("/views/{view_id}", status_code=204)
    async def delete_saved_view(
        request: Request,
        view_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(inbox_actor)],
    ) -> None:
        """Delete one of the asking person's views."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "inbox/saved_view_delete.sql",
                {**_keys(client_id, actor), "view_id": view_id},
                conn=conn,
            )
            if row is None:
                raise _not_found()
            await audit(conn, actor, client_id, "inbox.view_deleted", str(view_id))
