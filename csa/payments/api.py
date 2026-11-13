"""Console routes for payments: invoices, mark paid, void, attention, settings and methods.

Human sessions only, behind the `subscriptions` package gate. Reading and the staff writes (mark
paid, void) are for platform admins, tenant admins and tenant staff; settings and methods are
changed by platform and tenant admins only. `read_only` members get 403 on every route, reads
included. Refusals are `{"detail": "<code>"}`. The services audit every write themselves
(`payments.invoice.paid`, `payments.invoice.voided`, `payments.settings.updated`,
`payments.method.created`, `payments.method.updated`).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from psycopg_pool import AsyncConnectionPool

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Role
from csa.console.identity import IdentityService
from csa.console.services import package_gate
from csa.core.tenancy import tx
from csa.payments import console, invoices, settings
from csa.payments.contracts import (
    InvoiceDetail,
    InvoiceFilter,
    InvoicePage,
    MarkPaidIn,
    MethodIn,
    MethodOut,
    MethodPatch,
    PaymentAttentionPage,
    PaymentSettingsIn,
    PaymentSettingsOut,
    VoidIn,
)
from csa.subscriptions.api import human_session

STAFF_ROLES = frozenset({Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.TENANT_STAFF})
ADMIN_ROLES = frozenset({Role.PLATFORM_ADMIN, Role.TENANT_ADMIN})

InvoiceId = Annotated[str, Path(max_length=64)]


class PaymentsRoute(APIRoute):
    """Bad method details are 422 `{"detail": "invalid_details"}` on create as on change.

    `MethodIn` refuses details that do not fit the kind with the error type `invalid_details`;
    the PATCH service raises the same code. Every other validation error keeps FastAPI's list.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = exc.errors()
                if errors and all(error.get("type") == "invalid_details" for error in errors):
                    return JSONResponse(status_code=422, content={"detail": "invalid_details"})
                raise

        return guarded


def require_payments(actor: ConsoleActor, client_id: UUID, roles: frozenset[Role]) -> None:
    if not actor.platform_admin and actor.role not in roles:
        raise HTTPException(403, "payments access requires an administrator or staff role")
    if not actor.platform_admin and actor.client_id != client_id:
        raise HTTPException(403, "payments tenant is not authorized")


def _identity_pool(request: Request) -> AsyncConnectionPool | None:
    identity = getattr(request.app.state, "identity_service", None)
    return identity.pool if isinstance(identity, IdentityService) else None


def build_payments_router() -> APIRouter:
    router = APIRouter(
        prefix="/console/v1/payments",
        tags=["Payments"],
        dependencies=[Depends(human_session), Depends(package_gate("subscriptions"))],
        route_class=PaymentsRoute,
    )

    def staff(request: Request, client_id: UUID) -> ConsoleActor:
        actor: ConsoleActor = request.state.console_actor
        require_payments(actor, client_id, STAFF_ROLES)
        return actor

    def admin(request: Request, client_id: UUID) -> ConsoleActor:
        actor: ConsoleActor = request.state.console_actor
        require_payments(actor, client_id, ADMIN_ROLES)
        return actor

    @router.get("/settings", response_model=PaymentSettingsOut)
    async def get_settings(
        request: Request, client_id: Annotated[UUID, Depends(tenant)]
    ) -> PaymentSettingsOut:
        staff(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await settings.get_settings(conn, client_id)

    @router.put("/settings", response_model=PaymentSettingsOut)
    async def put_settings(
        request: Request,
        payload: PaymentSettingsIn,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> PaymentSettingsOut:
        """Replace the settings (409 `version_conflict`, 409 `no_active_method`).

        Switching on needs an active QR or transfer method.
        """
        actor = admin(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await settings.put_settings(conn, client_id, actor, payload)

    @router.get("/methods", response_model=list[MethodOut])
    async def list_methods(
        request: Request, client_id: Annotated[UUID, Depends(tenant)]
    ) -> list[MethodOut]:
        staff(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await settings.list_methods(conn, client_id)

    @router.post("/methods", response_model=MethodOut, status_code=201)
    async def create_method(
        request: Request, payload: MethodIn, client_id: Annotated[UUID, Depends(tenant)]
    ) -> MethodOut:
        actor = admin(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await settings.create_method(conn, client_id, actor, payload)

    @router.patch("/methods/{method_id}", response_model=MethodOut)
    async def update_method(
        request: Request,
        method_id: UUID,
        payload: MethodPatch,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> MethodOut:
        actor = admin(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await settings.update_method(conn, client_id, actor, method_id, payload)

    @router.get("/invoices", response_model=InvoicePage)
    async def list_invoices(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        status: InvoiceFilter = "open",
        q: Annotated[str | None, Query(max_length=100)] = None,
        before: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> InvoicePage:
        """Newest issued first; `before` is the previous page's `next_before`."""
        staff(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await console.list_invoices(
                conn, client_id, status=status, q=q, before=before, limit=limit
            )

    @router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
    async def invoice_detail(
        request: Request, invoice_id: InvoiceId, client_id: Annotated[UUID, Depends(tenant)]
    ) -> InvoiceDetail:
        staff(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await console.invoice_detail(
                conn, client_id, invoice_id, identity_pool=_identity_pool(request)
            )

    @router.post("/invoices/{invoice_id}/mark-paid", response_model=InvoiceDetail)
    async def mark_paid(
        request: Request,
        invoice_id: InvoiceId,
        payload: MarkPaidIn,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InvoiceDetail:
        """Record the payment; the same `request_key` again returns the same detail."""
        actor = staff(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await invoices.mark_paid(
                conn,
                client_id,
                invoice_id,
                actor_user_id=actor.user_id,
                request_key=payload.request_key,
                method_kind=payload.method_kind,
                received_at=payload.received_at or datetime.now(UTC),
                note=payload.note,
            )
            return await console.invoice_detail(
                conn, client_id, invoice_id, identity_pool=_identity_pool(request)
            )

    @router.post("/invoices/{invoice_id}/void", response_model=InvoiceDetail)
    async def void(
        request: Request,
        invoice_id: InvoiceId,
        payload: VoidIn,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> InvoiceDetail:
        """Void an invoice that is not paid: 409 `invoice_paid` or `invoice_void` otherwise."""
        actor = staff(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await invoices.void_invoice(
                conn, client_id, invoice_id, actor_user_id=actor.user_id, reason=payload.reason
            )
            return await console.invoice_detail(
                conn, client_id, invoice_id, identity_pool=_identity_pool(request)
            )

    @router.get("/attention", response_model=PaymentAttentionPage)
    async def attention(
        request: Request, client_id: Annotated[UUID, Depends(tenant)]
    ) -> PaymentAttentionPage:
        staff(request, client_id)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            return await console.attention(conn, client_id)

    return router
