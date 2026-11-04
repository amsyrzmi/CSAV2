"""Console routes for bookings setup: people, rooms, types, hours, closed days and blocks."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from csa.bookings import setup
from csa.bookings.contracts import (
    BlockIn,
    BlockOut,
    ClosedDayIn,
    ClosedDayOut,
    HoursIn,
    HoursKind,
    HoursOut,
    PersonIn,
    PersonOut,
    PersonPatch,
    ResourceIn,
    ResourceOut,
    ResourcePatch,
    TypeIn,
    TypeOut,
    TypePatch,
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

    @router.get("/people", response_model=list[PersonOut])
    async def people(request: Request, client_id: Tenant) -> list[PersonOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_people(conn, client_id)

    @router.post("/people", response_model=PersonOut, status_code=201)
    async def add_person(request: Request, payload: PersonIn, client_id: Tenant) -> PersonOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.create_person(conn, client_id, actor, payload)

    # `/people/from-team` is a POST and `/people/{person_id}` a PATCH, so order does not matter.
    @router.post("/people/from-team", response_model=list[PersonOut])
    async def people_from_team(request: Request, client_id: Tenant) -> list[PersonOut]:
        """List each console member not yet listed; returns the people added."""
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.add_team(conn, client_id, actor)

    @router.patch("/people/{person_id}", response_model=PersonOut)
    async def change_person(
        request: Request, person_id: UUID, payload: PersonPatch, client_id: Tenant
    ) -> PersonOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.update_person(conn, client_id, actor, person_id, payload)

    # --- rooms and shared things -------------------------------------------------------------

    @router.get("/resources", response_model=list[ResourceOut])
    async def resources(request: Request, client_id: Tenant) -> list[ResourceOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_resources(conn, client_id)

    @router.post("/resources", response_model=ResourceOut, status_code=201)
    async def add_resource(request: Request, payload: ResourceIn, client_id: Tenant) -> ResourceOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.create_resource(conn, client_id, actor, payload)

    @router.patch("/resources/{resource_id}", response_model=ResourceOut)
    async def change_resource(
        request: Request, resource_id: UUID, payload: ResourcePatch, client_id: Tenant
    ) -> ResourceOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.update_resource(conn, client_id, actor, resource_id, payload)

    # --- booking types -----------------------------------------------------------------------

    @router.get("/types", response_model=list[TypeOut])
    async def types(request: Request, client_id: Tenant) -> list[TypeOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_types(conn, client_id)

    @router.post("/types", response_model=TypeOut, status_code=201)
    async def add_type(request: Request, payload: TypeIn, client_id: Tenant) -> TypeOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.create_type(conn, client_id, actor, payload)

    @router.post("/types/starters", response_model=list[TypeOut], status_code=201)
    async def starter_types(
        request: Request, response: Response, client_id: Tenant
    ) -> list[TypeOut]:
        """Create "Appointment" and "Consultation" if the company has no type; else do nothing."""
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            items, created = await setup.create_starters(conn, client_id, actor)
        if not created:
            response.status_code = 200
        return items

    @router.patch("/types/{type_id}", response_model=TypeOut)
    async def change_type(
        request: Request, type_id: UUID, payload: TypePatch, client_id: Tenant
    ) -> TypeOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.update_type(conn, client_id, actor, type_id, payload)

    # --- hours, closed days and blocks -------------------------------------------------------

    @router.get("/hours", response_model=HoursOut)
    async def hours(
        request: Request,
        client_id: Tenant,
        subject_kind: HoursKind = "company",
        subject_id: UUID | None = None,
    ) -> HoursOut:
        """The company's hours (the default week until set), or one person's or room's own."""
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.get_hours(conn, client_id, subject_kind, subject_id)

    @router.put("/hours", response_model=HoursOut)
    async def replace_hours(request: Request, payload: HoursIn, client_id: Tenant) -> HoursOut:
        """Replace the hours as a whole; an empty list clears them."""
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.put_hours(conn, client_id, actor, payload)

    @router.get("/closed-days", response_model=list[ClosedDayOut])
    async def closed_days(
        request: Request,
        client_id: Tenant,
        start: Annotated[date | None, Query(alias="from")] = None,
        end: Annotated[date | None, Query(alias="to")] = None,
    ) -> list[ClosedDayOut]:
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_closed_days(conn, client_id, start, end)

    @router.post("/closed-days", response_model=ClosedDayOut, status_code=201)
    async def add_closed_day(
        request: Request, payload: ClosedDayIn, client_id: Tenant
    ) -> ClosedDayOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.add_closed_day(conn, client_id, actor, payload)

    @router.delete("/closed-days/{day}", status_code=204)
    async def remove_closed_day(request: Request, day: date, client_id: Tenant) -> Response:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await setup.remove_closed_day(conn, client_id, actor, day)
        return Response(status_code=204)

    @router.get("/blocks", response_model=list[BlockOut])
    async def blocks(
        request: Request,
        client_id: Tenant,
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
    ) -> list[BlockOut]:
        """Blocks ending after `from` (default: now) and starting before `to`."""
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.list_blocks(conn, client_id, start, end)

    @router.post("/blocks", response_model=BlockOut, status_code=201)
    async def add_block(request: Request, payload: BlockIn, client_id: Tenant) -> BlockOut:
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
