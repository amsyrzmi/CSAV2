"""Console routes for connecting a person's own Google Calendar to Bookings."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from csa.bookings.api import WRITE_ROLES, human_session, require_bookings_read
from csa.bookings.google.client import CalendarError
from csa.bookings.google.connections import CalendarConnections
from csa.bookings.google.contracts import CalendarConnectOut, CalendarStatusOut
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Role
from csa.console.services import package_gate
from csa.core.tenancy import tx

GOOGLE_ISSUER = "https://accounts.google.com"
# The Bookings page; the result of a connection is one query parameter, cleared by the page.
BOOKINGS_PAGE = "/portal/bookings"
CONNECTORS = {Role.TENANT_ADMIN, Role.TENANT_STAFF}
REFUSALS = {"person_required": 409, "not_connected": 409, "calendar_unavailable": 503}
Tenant = Annotated[UUID, Depends(tenant)]


def _service(request: Request) -> CalendarConnections | None:
    service = getattr(request.app.state, "calendar_connections", None)
    return service if isinstance(service, CalendarConnections) else None


def _refused(error: CalendarError) -> HTTPException:
    return HTTPException(REFUSALS.get(error.code, 502), error.code)


def build_calendar_router() -> APIRouter:
    """`/console/v1/bookings/calendar`: a member's own connection, and an Admin's status view."""
    router = APIRouter(
        prefix="/console/v1/bookings/calendar",
        tags=["Bookings"],
        dependencies=[Depends(human_session), Depends(package_gate("bookings"))],
    )

    def actor_of(request: Request, client_id: UUID) -> ConsoleActor:
        actor: ConsoleActor = request.state.console_actor
        require_bookings_read(actor, client_id)
        return actor

    @router.get("/status", response_model=CalendarStatusOut)
    async def status(request: Request, client_id: Tenant) -> CalendarStatusOut:
        """Whether the feature is on, the member's own state, and (for an Admin) everyone's."""
        actor = actor_of(request, client_id)
        service = _service(request)
        if service is None or not service.available:
            return CalendarStatusOut(available=False, can_connect=False, status=None, people=None)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            member = actor.client_id == client_id and actor.role in CONNECTORS
            person = await service.person_for(conn, client_id, actor.user_id) if member else None
            mine = await service.status(conn, client_id, actor.user_id) if person else None
            people = await service.people(conn, client_id) if actor.role in WRITE_ROLES else None
        return CalendarStatusOut(
            available=True, can_connect=person is not None, status=mine, people=people
        )

    @router.post("/connect", response_model=CalendarConnectOut)
    async def connect(request: Request, client_id: Tenant) -> CalendarConnectOut:
        """The Google address to send this member to: only for themselves, and only if they are
        linked to a bookable person."""
        actor = actor_of(request, client_id)
        service = _service(request)
        if service is None or not service.available:
            raise HTTPException(503, "calendar_unavailable")
        if actor.client_id != client_id or actor.role not in CONNECTORS:
            raise HTTPException(409, "person_required")
        try:
            return CalendarConnectOut(url=await service.start(client_id, actor.user_id))
        except CalendarError as error:
            raise _refused(error) from None

    @router.get("/callback", include_in_schema=True)
    async def callback(
        request: Request,
        client_id: Tenant,
        code: Annotated[str | None, Query(max_length=2048)] = None,
        state: Annotated[str | None, Query(max_length=256)] = None,
        error: Annotated[str | None, Query(max_length=64)] = None,
        iss: Annotated[str | None, Query(max_length=256)] = None,
    ) -> Response:
        """Google sends the browser back here; the answer is a redirect to the Bookings page."""
        actor = actor_of(request, client_id)
        service = _service(request)
        params: dict[str, str] = {}
        if service is None or not service.available:
            params["calendar_error"] = "calendar_unavailable"
        elif iss is not None and iss != GOOGLE_ISSUER:
            params["calendar_error"] = "oauth_issuer_invalid"
        elif error or not code or not state:
            params["calendar_error"] = "provider_denied" if error else "oauth_state_invalid"
        else:
            try:
                await service.finish(client_id, actor.user_id, code=code, state=state)
                params["calendar"] = "connected"
            except CalendarError as failure:
                params["calendar_error"] = failure.code
        response = RedirectResponse(f"{BOOKINGS_PAGE}?{urlencode(params)}", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/disconnect", status_code=204)
    async def disconnect(request: Request, client_id: Tenant) -> Response:
        """Stop syncing for this member; their calendar and its events stay in their account."""
        actor = actor_of(request, client_id)
        service = _service(request)
        if service is None:
            raise HTTPException(503, "calendar_unavailable")
        try:
            await service.disconnect(client_id, actor.user_id)
        except CalendarError as failure:
            raise _refused(failure) from None
        return Response(status_code=204)

    return router
