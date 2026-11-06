"""Console routes for bookings setup: people, rooms, types, hours, closed days and blocks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from csa.bookings import customer_messages, notify, service, setup
from csa.bookings.contracts import (
    BookingActionIn,
    BookingBlockIn,
    BookingBlockOut,
    BookingCalendarOut,
    BookingClosedDayIn,
    BookingClosedDayOut,
    BookingHoursIn,
    BookingHoursOut,
    BookingIn,
    BookingMoveIn,
    BookingOut,
    BookingPersonIn,
    BookingPersonOut,
    BookingPersonPatch,
    BookingResourceIn,
    BookingResourceOut,
    BookingResourcePatch,
    BookingSettingsIn,
    BookingSettingsOut,
    BookingSlotOut,
    BookingTime,
    BookingTypeIn,
    BookingTypeOut,
    BookingTypePatch,
    HoursKind,
)
from csa.console.access import authenticate, tenant
from csa.console.auth import _mailer
from csa.console.contracts import ConsoleActor, Role
from csa.console.services import package_gate
from csa.core.tenancy import tx
from csa.subscriptions import service as subscriptions
from csa.subscriptions.contracts import CustomerRef

WRITE_ROLES = {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}
# Customer details follow the Inbox rule: staff and Admins see them, read-only members do not.
CUSTOMER_ROLES = {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF}
Change = Literal["created", "moved", "cancelled", "confirmed", "declined", "no_show"]
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


class BookingRoute(APIRoute):
    """Sends a `service.Refusal` with its extra fields (a sentence, the findings) at the top level
    of the body, next to `detail`, which stays the short code."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except service.Refusal as refusal:
                return JSONResponse(refusal.body, status_code=refusal.status_code)

        return guarded


# What each staff action tells people. A confirmation is the invitation, because until then the
# customer has been sent nothing; a decline is a cancellation. A no-show sends nothing.
MAIL_FOR: dict[Change, notify.Change] = {
    "created": "created",
    "confirmed": "created",
    "moved": "moved",
    "cancelled": "cancelled",
    "declined": "cancelled",
}


async def after_commit(
    request: Request,
    record: service.BookingRecord,
    change: Change,
    *,
    by_chat: bool = False,
    original: service.BookingRecord | None = None,
    invite: bool = True,
) -> notify.Outcome | None:
    """Runs once the booking's transaction has committed, with the unredacted record.

    Sends the invitation, change or cancellation email from this one place, so a failed email can
    never undo a booking. It never raises, and the mail text never reaches the response. `by_chat`
    says the customer already has a chat message about this change (written in its transaction).
    `original` is the booking a declined request to move would have moved; `invite` is whether the
    bot would act on a reply (a declined customer's mail only then invites another request). Ending
    a booking whose time has passed mails nobody.
    """
    mail = MAIL_FOR.get(change)
    if mail is None or customer_messages.is_over(record, change):
        return None
    declined = customer_messages.is_declined(record, change)
    # Staff moved what the customer's own bot asked for, before confirming it: they asked, so they
    # are told, by plain text (nobody on it was invited yet).
    moved_request = change == "moved" and notify.bot_request(record)
    if declined:
        # The customer's own bot asked, so they are told; they were never sent an invitation, so it
        # is plain text and not a cancellation. A request to move goes to them alone.
        mail = "declined"
    elif change == "confirmed" and record.moved_by_request:
        mail = "moved"  # the booking keeps its id: the same calendar event, at its new time
    return await notify.notify_after_commit(
        request.app.state.pool,
        _mailer(request),
        record,
        mail,
        owed=True if declined or moved_request else None,
        by_chat=by_chat,
        original=original,
        invite=invite,
    )


def build_bookings_router() -> APIRouter:
    """The `/console/v1/bookings` router; reads need membership, setup writes need an Admin."""
    router = APIRouter(
        prefix="/console/v1/bookings",
        route_class=BookingRoute,
        tags=["Bookings"],
        dependencies=[Depends(human_session), Depends(package_gate("bookings"))],
    )

    def reader(request: Request, client_id: UUID) -> None:
        require_bookings_read(request.state.console_actor, client_id)

    def writer(request: Request, client_id: UUID) -> ConsoleActor:
        actor: ConsoleActor = request.state.console_actor
        require_bookings_write(actor, client_id)
        return actor

    def changer(request: Request, client_id: UUID) -> ConsoleActor:
        """Staff and Admins change bookings; read-only members get 403."""
        actor: ConsoleActor = request.state.console_actor
        require_bookings_read(actor, client_id)
        service.acting_as(actor)
        return actor

    async def respond(
        request: Request,
        actor: ConsoleActor,
        record: service.BookingRecord,
        change: Change,
        *,
        by_chat: bool = False,
        original: service.BookingRecord | None = None,
        invite: bool = True,
    ) -> BookingOut:
        outcome = await after_commit(
            request, record, change, by_chat=by_chat, original=original, invite=invite
        )
        out = service.to_out(record, service.viewer_of(actor))
        # A redacted answer says nothing about the meeting, not even whether it had addresses.
        if out.redacted or outcome is None:
            return out
        return out.model_copy(
            update={"email": outcome.email, "customer_told": outcome.customer_told}
        )

    # --- people ------------------------------------------------------------------------------

    @router.get("/people", response_model=list[BookingPersonOut])
    async def people(request: Request, client_id: Tenant) -> list[BookingPersonOut]:
        reader(request, client_id)
        admin = request.state.console_actor.role in WRITE_ROLES
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.people_for(conn, client_id, admin=admin)

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

    # --- settings ----------------------------------------------------------------------------

    @router.get("/settings", response_model=BookingSettingsOut)
    async def settings(request: Request, client_id: Tenant) -> BookingSettingsOut:
        """The reminder time, the pending window and the customer limit, with `replies_off`."""
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.get_settings(conn, client_id)

    @router.put("/settings", response_model=BookingSettingsOut)
    async def replace_settings(
        request: Request, payload: BookingSettingsIn, client_id: Tenant
    ) -> BookingSettingsOut:
        actor = writer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await setup.put_settings(conn, client_id, actor, payload)

    # --- the calendar ------------------------------------------------------------------------

    @router.get("/availability", response_model=list[BookingSlotOut])
    async def availability(
        request: Request,
        client_id: Tenant,
        start: Annotated[BookingTime, Query(alias="from")],
        end: Annotated[BookingTime, Query(alias="to")],
        type_id: UUID | None = None,
        people: Annotated[list[UUID] | None, Query()] = None,
        resources: Annotated[list[UUID] | None, Query()] = None,
        minutes: Annotated[int | None, Query(ge=1, le=1440)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[BookingSlotOut]:
        """Free times for a type, or for `people`, `resources` and `minutes`; at most 31 days."""
        reader(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await service.availability(
                conn,
                client_id,
                start=start,
                end=end,
                type_id=type_id,
                people=people or [],
                resources=resources or [],
                minutes=minutes,
                limit=limit,
            )

    @router.get("/calendar", response_model=BookingCalendarOut)
    async def calendar(
        request: Request,
        client_id: Tenant,
        start: Annotated[BookingTime, Query(alias="from")],
        end: Annotated[BookingTime, Query(alias="to")],
        person: UUID | None = None,
        resource: UUID | None = None,
    ) -> BookingCalendarOut:
        """Bookings that are not cancelled in the window, as this viewer may see them. With a
        `person` or `resource` filter, private meetings the viewer is not part of come back as bare
        busy times with no id."""
        reader(request, client_id)
        service.check_window(start, end, 62)
        viewer = service.viewer_of(request.state.console_actor)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            records = await service.read_records(
                conn, client_id, start=start, end=end, person=person, resource=resource
            )
        filtered = person is not None or resource is not None
        return service.calendar_out(records, viewer, filtered=filtered)

    @router.get("/customers", response_model=list[CustomerRef])
    async def customers(
        request: Request,
        client_id: Tenant,
        q: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> list[CustomerRef]:
        """The Subscriptions customer search, closed to read-only members as the Inbox is."""
        reader(request, client_id)
        if request.state.console_actor.role not in CUSTOMER_ROLES:
            raise HTTPException(403, "customer details are not available to read-only members")
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await subscriptions.search_customers(conn, client_id, q=q, limit=limit)

    # --- bookings ----------------------------------------------------------------------------

    @router.post("/bookings", response_model=BookingOut, status_code=201)
    async def book(request: Request, payload: BookingIn, client_id: Tenant) -> BookingOut:
        actor = changer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            record = await service.create_booking(conn, client_id, actor, payload)
        return await respond(request, actor, record, "created")

    @router.patch("/bookings/{booking_id}", response_model=BookingOut)
    async def move(
        request: Request, booking_id: UUID, payload: BookingMoveIn, client_id: Tenant
    ) -> BookingOut:
        """Move a booking; its people and rooms stay, and its holds are replaced with it."""
        actor = changer(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            record = await service.move_booking(conn, client_id, actor, booking_id, payload)
            by_chat = await customer_messages.after_staff_action(conn, client_id, record, "moved")
        return await respond(request, actor, record, "moved", by_chat=by_chat)

    def action(
        change: Change,
        run: Callable[..., Awaitable[service.BookingRecord]],
    ) -> Callable[..., Awaitable[BookingOut]]:
        async def handle(
            request: Request,
            booking_id: UUID,
            client_id: Tenant,
            payload: BookingActionIn | None = None,
        ) -> BookingOut:
            actor = changer(request, client_id)
            async with tx(request.app.state.pool, str(client_id)) as conn:
                record = await run(conn, client_id, actor, booking_id, payload or BookingActionIn())
                # A chat message is only rows, so it goes in the same transaction as the change.
                by_chat = await customer_messages.after_staff_action(
                    conn, client_id, record, change
                )
                original, invite = await customer_messages.mail_details(
                    conn, client_id, record, change
                )
            return await respond(
                request, actor, record, change, by_chat=by_chat, original=original, invite=invite
            )

        return handle

    for path, change, run in (
        ("cancel", "cancelled", service.cancel_booking),
        ("confirm", "confirmed", service.confirm_booking),
        ("decline", "declined", service.decline_booking),
        ("no-show", "no_show", service.mark_no_show),
    ):
        router.post(f"/bookings/{{booking_id}}/{path}", response_model=BookingOut)(
            action(change, run)  # type: ignore[arg-type]
        )

    return router
