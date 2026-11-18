"""The console notification routes (plan 2a, task 7; design 8): the signed-in person's own
notifications, written by :mod:`csa.notify.people`.

``GET /notifications`` lists them newest first with the person's unread count, ``GET
/notifications/count`` is the count alone (the bell polls it), ``POST /notifications/read`` marks
rows read: ``{"ids": [...]}`` or ``{"all": true}``, exactly one of the two.

The person is the session's: ``user_id`` comes from the actor and no parameter or body field can
name another, and every statement filters on it besides the company's row security. An id that is
not the caller's matches nothing and is not an error (``updated`` counts the caller's rows that
changed). Any active member may use the routes, a read-only member included (the POST is on the
read-only allow list of :mod:`csa.console.access`); no package gate, because kinds from other
areas will arrive here. A platform admin is not a member and has no rows: they get empty answers.
Machine principals are refused.

A notification carries a ``message_key`` and ``params`` that the browser renders (console users
have no language column) and a ``link`` of ids only, never customer message text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, model_validator

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract
from csa.core import db
from csa.core.tenancy import tx

LIST_MAX = 50
IDS_MAX = 200


class NotificationOut(Contract):
    """One notification as the browser renders it."""

    id: UUID
    kind: str
    priority: str
    message_key: str
    params: dict[str, Any]
    link: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class NotificationList(Contract):
    """The newest notifications and the person's whole unread count (not the page's)."""

    items: list[NotificationOut]
    unread: int


class NotificationCount(Contract):
    """The person's unread count."""

    unread: int


class NotificationsRead(Contract):
    """What to mark read: a list of ids, or everything; exactly one of the two."""

    ids: Annotated[list[UUID] | None, Field(max_length=IDS_MAX)] = None
    all_: Annotated[bool | None, Field(alias="all")] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.ids is not None) == bool(self.all_):
            raise ValueError("give ids or all: true, not both and not neither")
        return self


class NotificationsMarked(Contract):
    """How many of the caller's unread rows were marked."""

    updated: int


def notification_actor(request: Request) -> ConsoleActor:
    """The signed-in person; a machine principal has no notifications and gets 403."""
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, "a signed-in person is required")
    return actor


def _keys(client_id: UUID, actor: ConsoleActor) -> dict[str, Any]:
    return {"client_id": client_id, "user_id": actor.user_id}


def build_notifications_router() -> APIRouter:
    """The routes under ``/notifications`` (included in the console router)."""
    router = APIRouter(prefix="/notifications", tags=["notifications"])

    @router.get("", response_model=NotificationList)
    async def list_notifications(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(notification_actor)],
        limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = LIST_MAX,
        unread: bool = False,
    ) -> NotificationList:
        """The asking person's notifications, newest first; ``unread=true`` keeps the unread."""
        if actor.platform_admin:
            return NotificationList(items=[], unread=0)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "notify/list_console_notifications.sql",
                {**_keys(client_id, actor), "unread_only": unread, "limit": limit},
                conn=conn,
            )
            count = await db.q.fetch_one(
                "notify/count_console_notifications.sql", _keys(client_id, actor), conn=conn
            )
        return NotificationList(
            items=[NotificationOut.model_validate(row) for row in rows],
            unread=count["unread"] if count else 0,
        )

    @router.get("/count", response_model=NotificationCount)
    async def count_notifications(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(notification_actor)],
    ) -> NotificationCount:
        """The asking person's unread count."""
        if actor.platform_admin:
            return NotificationCount(unread=0)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            count = await db.q.fetch_one(
                "notify/count_console_notifications.sql", _keys(client_id, actor), conn=conn
            )
        return NotificationCount(unread=count["unread"] if count else 0)

    @router.post("/read", response_model=NotificationsMarked)
    async def mark_notifications_read(
        request: Request,
        payload: NotificationsRead,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(notification_actor)],
    ) -> NotificationsMarked:
        """Mark the caller's notifications read, by ``ids`` or all; others' ids are ignored."""
        if actor.platform_admin:
            return NotificationsMarked(updated=0)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "notify/mark_console_notifications_read.sql",
                {
                    **_keys(client_id, actor),
                    "mark_all": bool(payload.all_),
                    "ids": payload.ids or [],
                },
                conn=conn,
            )
        return NotificationsMarked(updated=row["updated"] if row else 0)

    return router
