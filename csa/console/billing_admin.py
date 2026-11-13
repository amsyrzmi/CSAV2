"""The platform-admin billing API under ``/console/v1/billing/admin``: overview and config writes.

Routes (platform admins only; everyone else, a company's own Admin and the machine principal
included, gets 403 ``platform_admin_only``, and the router's session check answers 401 first):

- ``GET /overview?cycle=``: every company's usage, bill, raw cost, tokens and AI margin for one
  cycle. This is the one place raw cost, tokens and margin leave the service; the client models in
  ``csa.console.billing`` never carry them, and these models are never used by a client route.
- ``POST /clients/{client_id}/plan``: a plan row through ``insert_plan`` (rules 1 to 7, audited).
- ``POST /prices`` and ``POST /fx``: an effective-dated price or FX row, under the shadow rule
  (``check_new_price_date``), audited.
- ``GET /clients/{client_id}/langfuse-check?cycle=``: one company's ledger tokens for the cycle
  against Langfuse's (``csa.billing.langfuse_check``), on demand only: the overview never calls
  Langfuse, so it stays fast and a Langfuse outage or rate limit never touches it. The check
  reports and changes nothing.

Cross-tenant reads never widen ``csa_app``: the companies are listed from ``public.clients`` (as
the onboarding admin list does), then each company's usage is read in its own ``tx()`` for that
company, at most ``OVERVIEW_CONCURRENCY`` at a time. Closed cycles come from the usage memo.

Price and FX rows are global, so their audit row has no company. ``console_audit`` row security
lets only the identity role write such a row, so it is written through
``IdentityService.audit_platform_write`` while the app transaction holding the new row is still
open: a failed audit rolls the write back. Plan rows are audited by ``insert_plan`` itself.
The reverse is possible: if the app transaction's own COMMIT fails after the audit committed, an
audit row names a price or FX row that was never stored (its target id then matches no row).

Money is ``Decimal`` throughout, with explicit contexts; display figures are fixed-point strings:
RM0.01 for the bill and the margin, RM0.0001 for raw cost and cost per execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Context, Decimal, Inexact
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from csa.billing.config import ShadowRuleError, check_new_price_date
from csa.billing.cycles import KL, Cycle, kl_today
from csa.billing.langfuse_check import (
    DEFAULT_BASE_URL,
    SETTLE,
    TIMEOUT,
    CheckResult,
    LangfuseSource,
    check_window,
    drift,
    langfuse_tokens,
)
from csa.billing.memo import USAGE_MEMO
from csa.billing.plans import Bundle, PlanRow, PlanRuleError, Stage, insert_plan, pilot_ended
from csa.billing.service import CycleUsage, UsageStatus, cycle_usage
from csa.console.billing import Money2, Money4, billing_now, pick_cycle
from csa.console.contracts import ConsoleActor
from csa.console.identity import IdentityService
from csa.core import db
from csa.core.settings import get_settings
from csa.core.tenancy import tx
from csa.metering.meter import LEDGER_PROVIDERS

__all__ = [
    "OVERVIEW_CONCURRENCY",
    "AdminCompanyOut",
    "AdminOverviewOut",
    "BillingFxIn",
    "BillingFxOut",
    "BillingPlanIn",
    "BillingPlanOut",
    "BillingPriceIn",
    "BillingPriceOut",
    "LangfuseCheckOut",
    "build_billing_admin_router",
    "company_out",
    "langfuse_source",
    "platform_admin",
]

OVERVIEW_CONCURRENCY = 4
WORKBOOK_COST_PER_MESSAGE_RM = Decimal("0.0167")
_PLATFORM_ONLY = "platform_admin_only"
_PRICE_AUDIT = "billing.price_added"
_FX_AUDIT = "billing.fx_added"
# Exact arithmetic: a sum or difference that would need rounding raises instead.
_EXACT = Context(prec=60, traps=[Inexact])
_ROUND = Context(prec=60)
_CENT = Decimal("0.01")
_UNIT = Decimal("0.0001")

AdminWarning = Literal[
    "pilot_ended",
    "usage_after_termination",
    "not_set_up_with_usage",
    "usage_before_plan",
    "pending_prices",
]
# Only the on-demand Langfuse check raises this one; the overview never does.
LangfuseWarning = Literal["langfuse_drift"]

# A positive, finite input with at most 8 decimal places, bounded well above any real value.
UsdPerMtok = Annotated[
    Decimal, Field(gt=0, lt=100000, allow_inf_nan=False, max_digits=20, decimal_places=8)
]
UsdToMyr = Annotated[
    Decimal, Field(gt=0, lt=1000, allow_inf_nan=False, max_digits=20, decimal_places=8)
]
# The exact strings the provider clients record in the ledger: no surrounding whitespace.
_NAME_PATTERN = r"^\S(?:.*\S)?$"


class AdminCompanyOut(BaseModel):
    """One company's cycle, for the platform admin only.

    ``status`` is the service's (``ok``, ``pending_prices``, ``not_set_up``); ``stage`` and
    ``bundle`` are the plan that bills the cycle, ``None`` when nothing is billed (not set up, or
    after a termination). ``executions`` counts the billed window's executions, priced or not.
    ``raw_cost_rm`` is the provider cost of the priced executions (RM0.0001, half up);
    ``ai_margin_rm`` is ``ai_charge - raw_cost_rm`` computed exactly and rounded to RM0.01;
    ``cost_per_execution_rm`` is the raw cost over the priced executions, ``None`` when none is
    priced, beside the workbook's assumed cost per message.

    ``terminated_on`` is the earliest termination date in the plan history (``None`` if none);
    ``usage_after_termination`` and ``usage_without_plan`` count the cycle's executions from that
    date, and before the plan starts (the whole cycle when not set up); neither is billed.
    ``warnings``: ``pilot_ended`` is evaluated for today (the Kuala Lumpur date), not for the
    selected cycle, since it asks for an action now; the others describe the selected cycle
    (``usage_before_plan``: executions before the join date in a set-up cycle, not billed).
    """

    model_config = ConfigDict(extra="forbid")

    client_id: UUID
    name: str
    status: UsageStatus
    stage: Stage | None
    bundle: Bundle | None
    executions: int
    prompt_tokens: int
    completion_tokens: int
    raw_cost_rm: Money4
    ai_charge: Money2
    platform_fee: Money2
    billed_total: Money2
    ai_margin_rm: Money2
    cost_per_execution_rm: Money4 | None
    workbook_cost_per_message_rm: Money4
    unpriced_calls: int
    unpriced_executions: int
    usage_without_plan: int
    usage_after_termination: int
    terminated_on: date | None
    warnings: list[AdminWarning]


class AdminOverviewOut(BaseModel):
    """Every company for one cycle, ordered by name (``final`` is false for the open cycle)."""

    model_config = ConfigDict(extra="forbid")

    cycle_start: date
    cycle_end: date
    final: bool
    companies: list[AdminCompanyOut]


class LangfuseCheckOut(BaseModel):
    """One company's ledger tokens for a cycle against Langfuse's, for the platform admin only.

    Both sides cover ``[cycle_start 00:00 KL, checked_until)``: the whole cycle once it has
    closed, else up to a few minutes ago (usage that recent may not have reached Langfuse yet).
    The ledger side is every recorded call in that window, billed or not (usage before a plan or
    after a termination included), since every call is mirrored. ``result``: ``match`` when input
    and output each agree within 1% or 100 tokens, whichever is larger; ``drift`` otherwise (with
    the warning ``langfuse_drift``); ``unavailable`` when Langfuse is not configured, not reachable
    or answered an error (its figures are then ``None``). The ledger is the authority: no billed
    figure depends on this.
    """

    model_config = ConfigDict(extra="forbid")

    client_id: UUID
    cycle_start: date
    cycle_end: date
    checked_until: AwareDatetime
    result: CheckResult
    ledger_input_tokens: int
    ledger_output_tokens: int
    langfuse_input_tokens: int | None
    langfuse_output_tokens: int | None
    warnings: list[LangfuseWarning]


class BillingPlanIn(BaseModel):
    """A new plan row. ``bundle`` may be left out only for a termination, which then keeps the
    bundle of the company's latest non-terminated row."""

    model_config = ConfigDict(extra="forbid")

    stage: Stage
    bundle: Bundle | None = None
    effective_from: date
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value

    @model_validator(mode="after")
    def _bundle_unless_terminated(self) -> BillingPlanIn:
        if self.bundle is None and self.stage != "terminated":
            raise ValueError("bundle is required unless the stage is terminated")
        return self


class BillingPlanOut(BaseModel):
    """The stored plan row."""

    model_config = ConfigDict(extra="forbid")

    client_id: UUID
    stage: Stage
    bundle: Bundle
    effective_from: date
    created_at: AwareDatetime


class BillingPriceIn(BaseModel):
    """A model price in USD per million tokens, from ``effective_from`` (timezone required).

    ``provider`` must be one the ledger records (``LEDGER_PROVIDERS``: ``groq``, ``gemini``), else
    the route answers 422 ``unknown_provider``; ``model`` is matched exactly, case included.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64, pattern=_NAME_PATTERN)
    model: str = Field(min_length=1, max_length=200, pattern=_NAME_PATTERN)
    input_usd_per_mtok: UsdPerMtok
    output_usd_per_mtok: UsdPerMtok
    effective_from: AwareDatetime


class BillingPriceOut(BaseModel):
    """The stored price row."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    effective_from: AwareDatetime
    created_at: AwareDatetime


class BillingFxIn(BaseModel):
    """A USD to MYR rate from ``effective_from`` (timezone required)."""

    model_config = ConfigDict(extra="forbid")

    usd_to_myr: UsdToMyr
    effective_from: AwareDatetime


class BillingFxOut(BaseModel):
    """The stored FX row."""

    model_config = ConfigDict(extra="forbid")

    usd_to_myr: Decimal
    effective_from: AwareDatetime
    created_at: AwareDatetime


def platform_admin(request: Request) -> ConsoleActor:
    """The signed-in platform admin; anyone else (the machine principal included) is refused."""
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor) or not actor.platform_admin:
        raise HTTPException(403, _PLATFORM_ONLY)
    return actor


def _half_up(value: Decimal, unit: Decimal) -> Decimal:
    return value.quantize(unit, rounding=ROUND_HALF_UP, context=_ROUND)


def _per_execution(raw: Decimal, executions: int) -> Decimal:
    """``raw / executions`` rounded half up to RM0.0001, with no intermediate rounding."""
    quotient, remainder = _EXACT.divmod(_EXACT.scaleb(raw, 4), Decimal(executions))
    if _EXACT.multiply(remainder, 2) >= executions:
        quotient = _EXACT.add(quotient, 1)
    return _EXACT.scaleb(quotient, -4).quantize(_UNIT, context=_EXACT)


def _warnings(usage: CycleUsage, history: Sequence[PlanRow], today: date) -> list[AdminWarning]:
    found: list[AdminWarning] = []
    if pilot_ended(history, today):
        found.append("pilot_ended")
    if usage.usage_after_termination > 0:
        found.append("usage_after_termination")
    if usage.status == "not_set_up" and usage.usage_without_plan > 0:
        found.append("not_set_up_with_usage")
    # A set-up cycle whose plan starts after the cycle does: usage before the join date is spent
    # but not billed.
    if usage.status != "not_set_up" and usage.usage_without_plan > 0:
        found.append("usage_before_plan")
    if usage.status == "pending_prices":
        found.append("pending_prices")
    return found


def company_out(
    client_id: UUID, name: str, usage: CycleUsage, history: Sequence[PlanRow], today: date
) -> AdminCompanyOut:
    """The admin view of one company's cycle, copied field by field from the service."""
    zero = Decimal("0.00")
    bill = usage.bill
    ai_charge = zero if bill is None else bill.ai_charge
    priced = len(usage.priced)
    return AdminCompanyOut(
        client_id=client_id,
        name=name,
        status=usage.status,
        stage=usage.stage,
        bundle=usage.bundle,
        executions=len(usage.executions),
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        raw_cost_rm=_half_up(usage.raw_cost_rm, _UNIT),
        ai_charge=ai_charge,
        platform_fee=zero if bill is None else bill.platform_fee,
        billed_total=zero if bill is None else bill.total,
        ai_margin_rm=_half_up(_EXACT.subtract(ai_charge, usage.raw_cost_rm), _CENT),
        cost_per_execution_rm=_per_execution(usage.raw_cost_rm, priced) if priced else None,
        workbook_cost_per_message_rm=WORKBOOK_COST_PER_MESSAGE_RM,
        unpriced_calls=sum(e.unpriced_calls for e in usage.executions),
        unpriced_executions=usage.unpriced_executions,
        usage_without_plan=usage.usage_without_plan,
        usage_after_termination=usage.usage_after_termination,
        terminated_on=min(
            (r.effective_from for r in history if r.stage == "terminated"), default=None
        ),
        warnings=_warnings(usage, history, today),
    )


async def _company(
    pool: AsyncConnectionPool, company: dict[str, Any], cycle: Cycle, now: datetime
) -> AdminCompanyOut:
    client_id: UUID = company["id"]
    async with tx(pool, str(client_id)) as conn:
        usage = await cycle_usage(conn, client_id, cycle, now=now, memo=USAGE_MEMO)
        rows = await db.q.fetch_all("billing/plans_history.sql", (client_id,), conn=conn)
    history = [PlanRow(**r) for r in rows]
    return company_out(client_id, company["name"], usage, history, kl_today(now))


async def _overview(pool: AsyncConnectionPool, cycle: Cycle, now: datetime) -> AdminOverviewOut:
    async with pool.connection() as conn:
        companies = await db.q.fetch_all("billing/admin_companies.sql", conn=conn)
    gate = asyncio.Semaphore(OVERVIEW_CONCURRENCY)

    async def one(company: dict[str, Any]) -> AdminCompanyOut:
        async with gate:
            return await _company(pool, company, cycle, now)

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(one(company)) for company in companies]
    return AdminOverviewOut(
        cycle_start=cycle.start,
        cycle_end=cycle.end,
        final=now >= datetime.combine(cycle.end, time(0), tzinfo=KL),
        companies=[task.result() for task in tasks],
    )


async def langfuse_source() -> AsyncIterator[LangfuseSource]:
    """The sink's Langfuse credentials and a short-lived HTTP client (a dependency for tests)."""
    settings = get_settings()
    secret = settings.LANGFUSE_SECRET_KEY
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        yield LangfuseSource(
            http=http,
            base_url=settings.LANGFUSE_BASE_URL or DEFAULT_BASE_URL,
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=None if secret is None else secret.get_secret_value(),
            pseudonym_key=settings.langfuse_pseudonym_key(),
        )


async def _langfuse_check(
    pool: AsyncConnectionPool,
    source: LangfuseSource,
    client_id: UUID,
    cycle: Cycle,
    now: datetime,
) -> LangfuseCheckOut:
    begin, finish = check_window(cycle.start, cycle.end, now - SETTLE)
    async with tx(pool, str(client_id)) as conn:
        if await db.q.fetch_one("console/client_get.sql", (client_id,), conn=conn) is None:
            raise HTTPException(404, "tenant not found")
        ledger = (0, 0)
        if finish > begin:
            row = await db.q.fetch_one(
                "billing/usage_token_totals.sql",
                {"client_id": client_id, "start": begin, "end": finish},
                conn=conn,
            )
            if row is None:  # an aggregate always returns one row
                raise RuntimeError("usage_token_totals.sql returned no row")
            ledger = (int(row["prompt_tokens"]), int(row["completion_tokens"]))
    # Langfuse is read after the transaction closes: a slow Langfuse never holds a connection.
    totals = await langfuse_tokens(
        source.http,
        base_url=source.base_url,
        public_key=source.public_key,
        secret_key=source.secret_key,
        pseudonym_key=source.pseudonym_key,
        client_id=client_id,
        start=cycle.start,
        end=cycle.end,
        until=now - SETTLE,
    )
    result = drift(ledger, totals)
    return LangfuseCheckOut(
        client_id=client_id,
        cycle_start=cycle.start,
        cycle_end=cycle.end,
        checked_until=max(finish, begin).astimezone(UTC),
        result=result,
        ledger_input_tokens=ledger[0],
        ledger_output_tokens=ledger[1],
        langfuse_input_tokens=None if totals is None else totals.input_tokens,
        langfuse_output_tokens=None if totals is None else totals.output_tokens,
        warnings=["langfuse_drift"] if result == "drift" else [],
    )


def _identity(request: Request) -> IdentityService:
    service = getattr(request.app.state, "identity_service", None)
    if not isinstance(service, IdentityService):  # a signed-in human implies one
        raise RuntimeError("the console identity service is not configured")
    return service


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _price_audit(row: dict[str, Any] | None) -> dict[str, str] | None:
    if row is None:
        return None
    return {
        "provider": row["provider"],
        "model": row["model"],
        "input_usd_per_mtok": str(row["input_usd_per_mtok"]),
        "output_usd_per_mtok": str(row["output_usd_per_mtok"]),
        "effective_from": _utc(row["effective_from"]),
    }


def _fx_audit(row: dict[str, Any] | None) -> dict[str, str] | None:
    if row is None:
        return None
    return {"usd_to_myr": str(row["usd_to_myr"]), "effective_from": _utc(row["effective_from"])}


def _in_force(rows: Sequence[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    """The row in force at ``at`` (latest ``effective_from`` at or before it), or ``None``."""
    return max(
        (r for r in rows if r["effective_from"] <= at),
        key=lambda r: r["effective_from"],
        default=None,
    )


def _shadow_rule(rows: Sequence[dict[str, Any]], effective_from: datetime, now: datetime) -> None:
    try:
        check_new_price_date([r["effective_from"] for r in rows], effective_from, now)
    except ShadowRuleError:
        raise HTTPException(422, "shadow_rule") from None


_TODAY_NOTE = (
    " Shadow rule: ``effective_from`` may be before the earliest existing row (it then fills a gap"
    " of unpriced calls), or today or tomorrow (Kuala Lumpur days); anything else is 422"
    " ``shadow_rule``. A row dated today is accepted at any time of day, so it also re-prices"
    " calls already made earlier today. A row with the same key and ``effective_from`` is 409."
)


def build_billing_admin_router() -> APIRouter:
    """The ``/billing/admin`` routes, mounted under ``/console/v1`` by ``csa.console.router``."""
    router = APIRouter(prefix="/billing/admin")

    @router.get("/overview", response_model=AdminOverviewOut)
    async def overview(
        request: Request,
        _actor: Annotated[ConsoleActor, Depends(platform_admin)],
        now: Annotated[datetime, Depends(billing_now)],
        cycle: Annotated[date | None, Query()] = None,
    ) -> AdminOverviewOut:
        """Every company's cycle: raw cost, tokens, bill and AI margin. Platform admin only.

        ``cycle`` is the cycle's start date (a 5th; default the current cycle): 422 off a 5th,
        404 in the future.
        """
        return await _overview(request.app.state.pool, pick_cycle(cycle, now), now)

    @router.get("/clients/{client_id}/langfuse-check", response_model=LangfuseCheckOut)
    async def langfuse_check(
        request: Request,
        client_id: UUID,
        _actor: Annotated[ConsoleActor, Depends(platform_admin)],
        now: Annotated[datetime, Depends(billing_now)],
        source: Annotated[LangfuseSource, Depends(langfuse_source)],
        cycle: Annotated[date | None, Query()] = None,
    ) -> LangfuseCheckOut:
        """One company's ledger tokens for a cycle against Langfuse's. Platform admin only.

        Asked for on demand (one Langfuse request per call; Langfuse Cloud rate-limits its
        Metrics API), never by the overview. ``cycle`` follows the overview's rules (default the
        current cycle; 422 off a 5th, 404 in the future); an unknown company is 404. A Langfuse
        that is unset, unreachable or failing is ``unavailable``, never an error status: the
        check only reports and changes no figure.
        """
        picked = pick_cycle(cycle, now)
        return await _langfuse_check(request.app.state.pool, source, client_id, picked, now)

    @router.post("/clients/{client_id}/plan", response_model=BillingPlanOut, status_code=201)
    async def add_plan(
        request: Request,
        client_id: UUID,
        body: BillingPlanIn,
        actor: Annotated[ConsoleActor, Depends(platform_admin)],
        now: Annotated[datetime, Depends(billing_now)],
    ) -> BillingPlanOut:
        """Insert a plan row (audited). A broken plan rule is 422 with its code as ``detail``.

        A termination without ``bundle`` keeps the bundle of the plan in force on its date (the
        latest non-terminated row dated on or before it), or of the latest non-terminated row when
        none is (the rules then refuse a termination before the join date).
        """
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if await db.q.fetch_one("console/client_get.sql", (client_id,), conn=conn) is None:
                raise HTTPException(404, "tenant not found")
            bundle = body.bundle
            if bundle is None:
                # The same lock insert_plan takes (it is re-entrant), so the history read here is
                # the one insert_plan checks.
                await db.q.execute(
                    "billing/plans_lock.sql", (f"client_billing_plans:{client_id}",), conn=conn
                )
                rows = await db.q.fetch_all("billing/plans_history.sql", (client_id,), conn=conn)
                prior = [PlanRow(**r) for r in rows if r["stage"] != "terminated"]
                if not prior:
                    raise HTTPException(422, "first_row_must_be_pilot_or_collaborator")
                # plans_history.sql is oldest first (effective_from, created_at, id), so the last
                # row dated on or before the termination is the one in force then: the same row
                # insert_plan audits as "before".
                in_force = [r for r in prior if r.effective_from <= body.effective_from]
                bundle = (in_force or prior)[-1].bundle
            try:
                stored = await insert_plan(
                    conn,
                    actor.display_name,
                    client_id,
                    PlanRow(body.stage, bundle, body.effective_from, now),
                    body.reason,
                    kl_today(now),
                    actor_id=actor.user_id,
                )
            except PlanRuleError as exc:
                raise HTTPException(422, exc.code) from None
        return BillingPlanOut(
            client_id=client_id,
            stage=stored.stage,
            bundle=stored.bundle,
            effective_from=stored.effective_from,
            created_at=stored.created_at,
        )

    @router.post(
        "/prices",
        response_model=BillingPriceOut,
        status_code=201,
        description="Add a model price row (audited). Platform admin only." + _TODAY_NOTE,
    )
    async def add_price(
        request: Request,
        body: BillingPriceIn,
        actor: Annotated[ConsoleActor, Depends(platform_admin)],
        now: Annotated[datetime, Depends(billing_now)],
    ) -> BillingPriceOut:
        if body.provider not in LEDGER_PROVIDERS:
            raise HTTPException(422, "unknown_provider")
        identity = _identity(request)
        key = {"provider": body.provider, "model": body.model}
        try:
            async with request.app.state.pool.connection() as conn, conn.transaction():
                await db.q.execute(
                    "billing/config_lock.sql",
                    (f"ai_model_prices:{body.provider}:{body.model}",),
                    conn=conn,
                )
                rows = await db.q.fetch_all("billing/prices_model.sql", key, conn=conn)
                _shadow_rule(rows, body.effective_from, now)
                row = await db.q.fetch_one(
                    "billing/prices_insert.sql",
                    {
                        **key,
                        "input_usd_per_mtok": body.input_usd_per_mtok,
                        "output_usd_per_mtok": body.output_usd_per_mtok,
                        "effective_from": body.effective_from,
                    },
                    conn=conn,
                )
                if row is None:  # INSERT ... RETURNING returns the row or raises
                    raise RuntimeError("prices_insert.sql returned no row")
                # Audited before this transaction commits: a failed audit rolls the row back.
                await identity.audit_platform_write(
                    actor,
                    _PRICE_AUDIT,
                    str(row["id"]),
                    {
                        "before": _price_audit(_in_force(rows, body.effective_from)),
                        "after": _price_audit(row),
                    },
                )
        except UniqueViolation:
            raise HTTPException(409, "price_exists") from None
        return BillingPriceOut(
            provider=row["provider"],
            model=row["model"],
            input_usd_per_mtok=row["input_usd_per_mtok"],
            output_usd_per_mtok=row["output_usd_per_mtok"],
            effective_from=row["effective_from"],
            created_at=row["created_at"],
        )

    @router.post(
        "/fx",
        response_model=BillingFxOut,
        status_code=201,
        description="Add a USD to MYR row (audited). Platform admin only." + _TODAY_NOTE,
    )
    async def add_fx(
        request: Request,
        body: BillingFxIn,
        actor: Annotated[ConsoleActor, Depends(platform_admin)],
        now: Annotated[datetime, Depends(billing_now)],
    ) -> BillingFxOut:
        identity = _identity(request)
        try:
            async with request.app.state.pool.connection() as conn, conn.transaction():
                await db.q.execute("billing/config_lock.sql", ("billing_fx_rates",), conn=conn)
                rows = await db.q.fetch_all("billing/fx_history.sql", conn=conn)
                _shadow_rule(rows, body.effective_from, now)
                row = await db.q.fetch_one(
                    "billing/fx_insert.sql",
                    {"usd_to_myr": body.usd_to_myr, "effective_from": body.effective_from},
                    conn=conn,
                )
                if row is None:  # INSERT ... RETURNING returns the row or raises
                    raise RuntimeError("fx_insert.sql returned no row")
                await identity.audit_platform_write(
                    actor,
                    _FX_AUDIT,
                    str(row["id"]),
                    {
                        "before": _fx_audit(_in_force(rows, body.effective_from)),
                        "after": _fx_audit(row),
                    },
                )
        except UniqueViolation:
            raise HTTPException(409, "fx_rate_exists") from None
        return BillingFxOut(
            usd_to_myr=row["usd_to_myr"],
            effective_from=row["effective_from"],
            created_at=row["created_at"],
        )

    return router
