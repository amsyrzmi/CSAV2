"""Console routes for bookings setup: people, rooms, types, hours, closed days and blocks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from csa.bookings import setup
from csa.bookings.contracts import (
    BookingBlockIn,
    BookingBlockOut,
    BookingClosedDayIn,
    BookingClosedDayOut,
    BookingHoursIn,
    BookingHoursOut,
    BookingPersonIn,
    BookingPersonOut,
    BookingPersonPatch,
    BookingResourceIn,
    BookingResourceOut,
    BookingResourcePatch,
    BookingTypeIn,
    BookingTypeOut,
    BookingTypePatch,
    HoursKind,
)
from csa.console.access import authenticate, tenant
from csa.console.contracts import ConsoleActor, Role
from csa.console.services import package_gate
from csa.core.tenancy import tx

WRITE_ROLES = {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}
# Module level: handler annotations are strings and must resolve in module globals.
Tenant = Annotated[UUID, Depends(tenant)]


async def human_session(request: Request) -> None:
    """Human console sessions only; the machine token never reaches bookings."""
    if request.headers.get("authorization"):
        raise HTTPException(403, "human session required")
    await authenticate(request, None, frozenset())


def require_bookings_read(actor: ConsoleActor, client_id: UUID) -> None:
    """Any member of the company, or a platform admin who has chosen it."""
    if not actor.platform_admin and actor.client_id != client_id:
        raise HTTPException(403, "bookings tenant is not authorized")


def require_bookings_write(actor: ConsoleActor, client_id: UUID) -> None:
    """Setup changes need an Admin of the company, or a platform admin who has chosen it."""
    if actor.role not in WRITE_ROLES:
        raise HTTPException(403, "bookings setup requires an administrator")
    require_bookings_read(actor, client_id)


def build_bookings_router() -> APIRouter:
    """The `/console/v1/bookings` router; reads need membership, setup writes need an Admin."""
    router = APIRouter(
        prefix="/console/v1/bookings",
        tags=["Bookings"],
        dependencies=[Depends(human_session), Depends(package_gate("bookings"))],
    )

    def reader(request: Request, client_id: UUID) -> None:
        require_bookings_read(request.state.console_actor, client_id)

    def writer(request: Request, client_id: UUID) -> ConsoleActor:
        actor: ConsoleActor = request.state.console_actor
        require_bookings_write(actor, client_id)
        return actor

    # --- people ------------------------------------------------------------------------------

    @router.get("/people", response_model=list[BookingPersonOut])
    async def people(request: Request, client_id: Tenant) -> list[BookingPersonOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_people(conn, client_id)

    @router.post("/people", response_model=BookingPersonOut, status_code=201)
    async def add_person(
        request: Request, payload: BookingPersonIn, client_id: Tenant
    ) -> BookingPersonOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.create_person(conn, client_id, actor, payload)

    # `/people/from-team` is a POST and `/people/{person_id}` a PATCH, so order does not matter.
    @router.post("/people/from-team", response_model=list[BookingPersonOut])
    async def people_from_team(request: Request, client_id: Tenant) -> list[BookingPersonOut]:
        """List each console member not yet listed; returns the people added."""
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.add_team(conn, client_id, actor)

    @router.patch("/people/{person_id}", response_model=BookingPersonOut)
    async def change_person(
        request: Request, person_id: UUID, payload: BookingPersonPatch, client_id: Tenant
    ) -> BookingPersonOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.update_person(conn, client_id, actor, person_id, payload)

    # --- rooms and shared things -------------------------------------------------------------

    @router.get("/resources", response_model=list[BookingResourceOut])
    async def resources(request: Request, client_id: Tenant) -> list[BookingResourceOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_resources(conn, client_id)

    @router.post("/resources", response_model=BookingResourceOut, status_code=201)
    async def add_resource(
        request: Request, payload: BookingResourceIn, client_id: Tenant
    ) -> BookingResourceOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.create_resource(conn, client_id, actor, payload)

    @router.patch("/resources/{resource_id}", response_model=BookingResourceOut)
    async def change_resource(
        request: Request, resource_id: UUID, payload: BookingResourcePatch, client_id: Tenant
    ) -> BookingResourceOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.update_resource(conn, client_id, actor, resource_id, payload)

    # --- booking types -----------------------------------------------------------------------

    @router.get("/types", response_model=list[BookingTypeOut])
    async def types(request: Request, client_id: Tenant) -> list[BookingTypeOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_types(conn, client_id)

    @router.post("/types", response_model=BookingTypeOut, status_code=201)
    async def add_type(
        request: Request, payload: BookingTypeIn, client_id: Tenant
    ) -> BookingTypeOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.create_type(conn, client_id, actor, payload)

    @router.post("/types/starters", response_model=list[BookingTypeOut], status_code=201)
    async def starter_types(
        request: Request, response: Response, client_id: Tenant
    ) -> list[BookingTypeOut]:
        """Create "Appointment" and "Consultation" if the company has no type; else do nothing."""
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            items, created = await setup.create_starters(conn, client_id, actor)
        if not created:
            response.status_code = 200
        return items

    @router.patch("/types/{type_id}", response_model=BookingTypeOut)
    async def change_type(
        request: Request, type_id: UUID, payload: BookingTypePatch, client_id: Tenant
    ) -> BookingTypeOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.update_type(conn, client_id, actor, type_id, payload)

    # --- hours, closed days and blocks -------------------------------------------------------

    @router.get("/hours", response_model=BookingHoursOut)
    async def hours(
        request: Request,
        client_id: Tenant,
        subject_kind: HoursKind = "company",
        subject_id: UUID | None = None,
    ) -> BookingHoursOut:
        """The company's hours (the default week until set), or one person's or room's own."""
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.get_hours(conn, client_id, subject_kind, subject_id)

    @router.put("/hours", response_model=BookingHoursOut)
    async def replace_hours(
        request: Request, payload: BookingHoursIn, client_id: Tenant
    ) -> BookingHoursOut:
        """Replace the hours as a whole; an empty list clears them."""
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.put_hours(conn, client_id, actor, payload)

    @router.get("/closed-days", response_model=list[BookingClosedDayOut])
    async def closed_days(
        request: Request,
        client_id: Tenant,
        start: Annotated[date | None, Query(alias="from")] = None,
        end: Annotated[date | None, Query(alias="to")] = None,
    ) -> list[BookingClosedDayOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_closed_days(conn, client_id, start, end)

    @router.post("/closed-days", response_model=BookingClosedDayOut, status_code=201)
    async def add_closed_day(
        request: Request, payload: BookingClosedDayIn, client_id: Tenant
    ) -> BookingClosedDayOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.add_closed_day(conn, client_id, actor, payload)

    @router.delete("/closed-days/{day}", status_code=204)
    async def remove_closed_day(request: Request, day: date, client_id: Tenant) -> Response:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await setup.remove_closed_day(conn, client_id, actor, day)
        return Response(status_code=204)

    @router.get("/blocks", response_model=list[BookingBlockOut])
    async def blocks(
        request: Request,
        client_id: Tenant,
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
    ) -> list[BookingBlockOut]:
        """Blocks ending after `from` (default: now) and starting before `to`."""
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_blocks(conn, client_id, start, end)

    @router.post("/blocks", response_model=BookingBlockOut, status_code=201)
    async def add_block(
        request: Request, payload: BookingBlockIn, client_id: Tenant
    ) -> BookingBlockOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.create_block(conn, client_id, actor, payload)

    @router.delete("/blocks/{block_id}", status_code=204)
    async def remove_block(request: Request, block_id: UUID, client_id: Tenant) -> Response:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await setup.remove_block(conn, client_id, actor, block_id)
        return Response(status_code=204)

    return router
