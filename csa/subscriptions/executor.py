"""The ``subscription_action`` proposal executor: every subscription state change is a card.

``card`` runs at create and again at confirm (the registry compares the version it returns
with the bound one); ``execute`` locks the row, checks that version once more, applies the
change and records one ``subscription_events`` row keyed by the proposal id, so a confirm run
again after the commit replays that event's receipt instead of acting twice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from pydantic import Field, ValidationError

from csa.console.contracts import ConsoleActor
from csa.console.kb_uploads import audit
from csa.console.services import ACTIVE_ONLY, require_package
from csa.core import db
from csa.core.tenancy import tx
from csa.subscriptions.api import require_subscriptions_write
from csa.subscriptions.contracts import Action, ActionIn
from csa.subscriptions.service import Conn, get_request_row, get_subscription_out, record_event
from csa.workspace.contracts import CardField, ProposalKind
from csa.workspace.registry import CardContent, ProposalError, ProposalOrigin

# Which statuses each action may start from; the card refuses anything else.
ALLOWED_FROM: dict[Action, frozenset[str]] = {
    "pause": frozenset({"active"}),
    "resume": frozenset({"paused"}),
    "change_plan": frozenset({"active", "paused"}),
    "cancel_at_period_end": frozenset({"active"}),
    "cancel_now": frozenset({"active", "paused"}),
    "extend_renewal": frozenset({"active"}),
}

TITLES: dict[Action, str] = {
    "pause": "Pause subscription",
    "resume": "Resume subscription",
    "change_plan": "Change plan",
    "cancel_at_period_end": "Cancel at period end",
    "cancel_now": "Cancel subscription now",
    "extend_renewal": "Extend renewal",
}

# Timeline event names, past tense like the due job's (``resumed``, ``plan_changed``).
EVENTS: dict[Action, str] = {
    "pause": "paused",
    "resume": "resumed",
    "change_plan": "plan_changed",
    "cancel_at_period_end": "cancel_scheduled",
    "cancel_now": "cancelled",
    "extend_renewal": "renewal_extended",
}
SCHEDULED_PLAN_EVENT = "plan_change_scheduled"


class ActionPayload(ActionIn):
    """The stored proposal payload: the route's ``ActionIn`` plus the subscription it targets.

    ``request_id`` and ``decision_note`` are set by the request service when it approves a staged
    customer request. The actions route's body is ``ActionIn`` (extra fields forbidden), but the
    workspace proposals route passes any payload through, so the executor validates them
    itself: the request must be undecided and the action must be the one it asked for. With a
    ``request_id`` the approved decision is written in the same transaction as the change.
    """

    subscription_id: UUID
    request_id: UUID | None = None
    decision_note: str | None = Field(default=None, max_length=1000)


def _parse(payload: dict[str, Any]) -> ActionPayload:
    try:
        return ActionPayload.model_validate(payload)
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(422, "invalid proposal payload") from None


def _immediate(action: ActionIn, now: datetime) -> bool:
    return action.effective_at is None or action.effective_at <= now


def _check(
    action: ActionIn,
    *,
    status: str,
    plan: str,
    renews_at: datetime | None,
    cancel_at_period_end: bool,
    target: dict[str, Any] | None,
    now: datetime,
) -> None:
    """Refuse an action the subscription's current state does not allow (422 with a code)."""
    if status not in ALLOWED_FROM[action.action]:
        raise HTTPException(422, "action_not_allowed")
    if action.action == "cancel_at_period_end" and cancel_at_period_end:
        raise HTTPException(422, "action_not_allowed")
    if action.action == "change_plan":
        if target is None or not target["is_active"]:
            raise HTTPException(422, "plan_unavailable")
        if action.plan_code == plan and action.price_override is None:
            raise HTTPException(422, "plan_unchanged")
        if action.price_override is not None and not _immediate(action, now):
            # The due job applies a pending change at the plan's price on the day.
            raise HTTPException(422, "price_override_requires_immediate_change")
    if action.action == "extend_renewal":
        assert action.renews_at is not None
        if action.renews_at <= (renews_at or now):
            raise HTTPException(422, "renews_at_not_after_current")


# The one action an approval of each staged customer request may run.
REQUEST_ACTIONS: dict[str, Action] = {
    "request_pause": "pause",
    "request_cancel": "cancel_at_period_end",
    "request_change": "change_plan",
}


async def _check_request(conn: Conn, client_id: UUID, action: ActionPayload, now: datetime) -> None:
    """A request-driven action needs an undecided request that it matches exactly.

    The proposal may come from the request service or straight from the workspace proposals
    route, so the executor itself checks that the action is the one the customer asked for.
    """
    assert action.request_id is not None
    row = await get_request_row(conn, client_id, action.request_id)
    if row["decision"] is not None:
        raise HTTPException(409, "request_decided")
    if row["subscription_id"] != action.subscription_id:
        raise HTTPException(422, "request_subscription_mismatch")
    if REQUEST_ACTIONS.get(row["action"]) != action.action:
        raise HTTPException(422, "request_action_mismatch")
    if action.action == "change_plan" and (
        action.plan_code != row["plan_code"]
        or action.price_override is not None
        or not _immediate(action, now)
    ):
        raise HTTPException(422, "request_action_mismatch")


async def _plan(conn: Conn, client_id: UUID, code: str | None) -> dict[str, Any] | None:
    if code is None:
        return None
    row: dict[str, Any] | None = await db.q.fetch_one(
        "subscriptions/plan_get.sql", {"client_id": client_id, "code": code}, conn=conn
    )
    return row


def money(price: Decimal, currency: str) -> str:
    amount = f"{price:,.2f}"
    return f"RM {amount}" if currency == "MYR" else f"{currency} {amount}"


def _per(plan: dict[str, Any] | None) -> str:
    if plan is None:
        return ""
    return " a year" if plan["billing_interval"] == "year" else " a month"


def _day(value: date | datetime, zone: ZoneInfo) -> str:
    local = value.astimezone(zone) if isinstance(value, datetime) else value
    return f"{local.day} {local:%b %Y}"


def _change(
    action: ActionIn,
    *,
    zone: ZoneInfo,
    now: datetime,
    plan_label: str,
    price: Decimal,
    currency: str,
    current: dict[str, Any] | None,
    renews_at: datetime | None,
    target: dict[str, Any] | None,
) -> str:
    """One plain sentence saying what confirming the card will do."""
    match action.action:
        case "pause":
            if action.resume_at is None:
                return "Pause now until someone resumes it."
            return f"Pause now and resume on {_day(action.resume_at, zone)}."
        case "resume":
            if renews_at is not None and renews_at < now:
                return (
                    "Resume now. The renewal date has passed, so the next renewal is one "
                    "billing period from today."
                )
            return "Resume now."
        case "change_plan":
            assert target is not None
            if action.price_override is not None:
                override = money(action.price_override, target["currency"])
                new = f"{target['name']} at {override}{_per(target)} (price override)"
            else:
                listed = money(target["price"], target["currency"])
                new = f"{target['name']} ({listed}{_per(target)})"
            when = (
                "now"
                if action.effective_at is None or _immediate(action, now)
                else f"from {_day(action.effective_at, zone)}"
            )
            old = f"{plan_label} ({money(price, currency)}{_per(current)})"
            return f"Move from {old} to {new}, {when}."
        case "cancel_at_period_end":
            if renews_at is None:
                return "Cancel at the end of the current period."
            return f"Cancel when the current period ends on {_day(renews_at, zone)}."
        case "cancel_now":
            return "Cancel now. The subscription stops immediately."
        case "extend_renewal":
            assert action.renews_at is not None
            if renews_at is None:
                return f"Set the next renewal to {_day(action.renews_at, zone)}."
            return (
                f"Move the next renewal from {_day(renews_at, zone)} "
                f"to {_day(action.renews_at, zone)}."
            )


def _iso(value: date | datetime | None) -> str | None:
    return None if value is None else value.isoformat()


class SubscriptionActionExecutor:
    kind: ProposalKind = "subscription_action"

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        """Write role in the tenant and an active ``subscriptions`` package, for both origins.

        There is no per-tenant assistant switch for this kind, so an assistant proposal passes
        the same gate as a tab one; the human still confirms every card.
        """
        require_subscriptions_write(actor, client_id)
        await require_package(
            request.app.state.pool, client_id, frozenset({"subscriptions"}), ACTIVE_ONLY
        )

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
    ) -> tuple[CardContent, str | None]:
        action = _parse(payload)
        await self.gate(request, actor, client_id, origin=origin)
        now = datetime.now(UTC)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            if action.request_id is not None:
                await _check_request(conn, client_id, action, now)
            subscription = await get_subscription_out(conn, client_id, action.subscription_id)
            current = await _plan(conn, client_id, subscription.plan)
            target = await _plan(conn, client_id, action.plan_code)
            tenant = await db.q.fetch_one(
                "subscriptions/tenant_timezone.sql", {"client_id": client_id}, conn=conn
            )
        _check(
            action,
            status=subscription.status,
            plan=subscription.plan,
            renews_at=subscription.renews_at,
            cancel_at_period_end=subscription.cancel_at_period_end,
            target=target,
            now=now,
        )
        if tenant is None:
            raise RuntimeError(f"tenant {client_id} has no clients row")
        zone = ZoneInfo(tenant["timezone"])
        customer = subscription.customer
        name = customer.full_name or customer.phone or customer.email or "Unnamed customer"
        plan_label = subscription.plan_name or subscription.plan
        price = money(subscription.price, subscription.currency)
        change = _change(
            action,
            zone=zone,
            now=now,
            plan_label=plan_label,
            price=subscription.price,
            currency=subscription.currency,
            current=current,
            renews_at=subscription.renews_at,
            target=target,
        )
        return (
            CardContent(
                title=TITLES[action.action],
                summary=f"{name} · {plan_label} · {price}{_per(current)}",
                fields=[
                    CardField(label="Customer", value=name),
                    CardField(label="Plan", value=plan_label),
                    CardField(label="Change", value=change),
                    CardField(label="Reason", value=action.reason or "—"),
                ],
            ),
            str(subscription.version),
        )

    async def execute(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        proposal_id: UUID,
        expected_version: str | None,
        origin: ProposalOrigin,
    ) -> dict[str, Any]:
        action = _parse(payload)
        if expected_version is None:
            raise RuntimeError("subscription_action proposal has no bound version")
        now = datetime.now(UTC)
        key = {"client_id": client_id, "subscription_id": action.subscription_id}
        async with tx(request.app.state.pool, str(client_id)) as conn:
            locked = await db.q.fetch_one("subscriptions/subscription_lock.sql", key, conn=conn)
            if locked is None:
                raise HTTPException(404, "subscription_not_found")
            # Looked up under the row lock: a concurrent run of this proposal that committed
            # first is visible here, and its receipt is returned unchanged.
            done = await db.q.fetch_one(
                "subscriptions/event_by_proposal.sql",
                {"client_id": client_id, "proposal_id": proposal_id},
                conn=conn,
            )
            if done is not None:
                return {
                    "subscription_id": str(done["subscription_id"]),
                    "status": done["to_status"],
                    "version": done["payload"]["version"],
                    "event_id": done["id"],
                }
            if action.request_id is not None:
                # Decisions are final and the request's primary key allows one: whichever of an
                # approval, a decline or a twin approval commits first wins, and a loser's
                # exception rolls this whole transaction back, so no action applies.
                await _check_request(conn, client_id, action, now)
                recorded = await db.q.fetch_one(
                    "subscriptions/decision_insert.sql",
                    {
                        "client_id": client_id,
                        "request_id": action.request_id,
                        "decision": "approved",
                        "note": action.decision_note,
                        "decided_by": actor.user_id,
                        "proposal_id": proposal_id,
                    },
                    conn=conn,
                )
                if recorded is None:
                    raise HTTPException(409, "request_decided")
            if locked["version"] != int(expected_version):
                raise ProposalError("proposal_stale")
            target = await _plan(conn, client_id, action.plan_code)
            _check(
                action,
                status=locked["status"],
                plan=locked["plan"],
                renews_at=locked["renews_at"],
                cancel_at_period_end=locked["cancel_at_period_end"],
                target=target,
                now=now,
            )
            is_plan = action.action == "change_plan"
            immediate_plan = is_plan and _immediate(action, now)
            new_price: Decimal | None = None
            if immediate_plan:
                assert target is not None
                new_price = (
                    action.price_override if action.price_override is not None else target["price"]
                )
            applied = await db.q.fetch_one(
                "subscriptions/subscription_apply.sql",
                {
                    **key,
                    "version": locked["version"],
                    "action": action.action,
                    "resume_at": action.resume_at,
                    "renews_at": action.renews_at,
                    "immediate_plan": immediate_plan,
                    "scheduled_plan": is_plan and not immediate_plan,
                    "plan_code": action.plan_code,
                    "price": new_price,
                    "currency": target["currency"] if immediate_plan and target else None,
                    "effective_at": action.effective_at,
                },
                conn=conn,
            )
            if applied is None:
                raise RuntimeError(f"subscription {action.subscription_id} apply matched no row")
            details: dict[str, Any] = {}
            match action.action:
                case "pause":
                    details = {"resume_at": _iso(action.resume_at)}
                case "resume":
                    details = {
                        "old_renews_at": _iso(locked["renews_at"]),
                        "new_renews_at": _iso(applied["renews_at"]),
                    }
                case "change_plan" if immediate_plan:
                    details = {
                        "old_plan": locked["plan"],
                        "new_plan": action.plan_code,
                        "old_price": str(locked["price"]),
                        "new_price": str(new_price),
                        "price_override": action.price_override is not None,
                    }
                case "change_plan":
                    details = {
                        "old_plan": locked["plan"],
                        "new_plan": action.plan_code,
                        "effective_at": _iso(action.effective_at),
                    }
                case "cancel_at_period_end":
                    details = {"renews_at": _iso(locked["renews_at"])}
                case "cancel_now":
                    details = {"cleared_pending_plan": locked["pending_plan"]}
                case "extend_renewal":
                    details = {
                        "old_renews_at": _iso(locked["renews_at"]),
                        "new_renews_at": _iso(applied["renews_at"]),
                    }
            event = (
                SCHEDULED_PLAN_EVENT if is_plan and not immediate_plan else EVENTS[action.action]
            )
            event_id = await record_event(
                conn,
                client_id,
                action.subscription_id,
                actor=str(actor.user_id),
                action=event,
                from_status=locked["status"],
                to_status=applied["status"],
                payload={
                    **details,
                    "reason": action.reason,
                    "version": applied["version"],
                },
                proposal_id=proposal_id,
            )
            if action.request_id is not None:
                await audit(
                    conn, actor, client_id, "subscriptions.request.approved", str(action.request_id)
                )
        return {
            "subscription_id": str(action.subscription_id),
            "status": applied["status"],
            "version": applied["version"],
            "event_id": event_id,
        }
