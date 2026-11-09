"""Batch email recipients: named people, typed addresses, customers and fixed groups, plus quota.

Runs against a disposable migrated Postgres through the email test world (real sessions, the
hr and subscriptions package rows, company directory). Every person and address is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from csa.console.contracts import ConsoleActor, Role
from csa.console.security import digest
from csa.core import db
from csa.email.models import EmailPolicyError, EmailSession
from csa.workspace.contracts import RecipientSpec
from csa.workspace.email_batch import expand_recipients
from fastapi import HTTPException

from tests.conftest import requires_docker
from tests.test_email_service import (  # noqa: F401 - fixture import
    World,
    _employee,
    _enable_and_connect,
    _insert_draft,
    world,  # noqa: F811
)

pytestmark = [requires_docker, pytest.mark.asyncio]


def _request(w: World) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=w.app.state))


def _actor(w: World, role: Role = Role.TENANT_STAFF, tenant: UUID | None = None) -> ConsoleActor:
    return ConsoleActor(
        user_id=w.users["staff"],
        email="staff@example.test",
        display_name="Synthetic Staff",
        platform_admin=False,
        must_change_password=False,
        session_id=w.sessions["staff"],
        client_id=tenant or w.tenant,
        role=role,
        memberships=[],
        csrf_hash="synthetic",
    )


async def _package(w: World, tenant: UUID, package: str) -> None:
    await w.q(
        "INSERT INTO public.console_service_packages "
        "(client_id, package, status, activated_at, reference) "
        "VALUES (%s, %s, 'active', now(), 'test-fixture')",
        (tenant, package),
    )


async def _customer(w: World, name: str, email: str | None, contact: UUID | None = None) -> UUID:
    contact = contact or uuid4()
    await w.q(
        "INSERT INTO contacts(id,client_id,full_name,email) VALUES (%s,%s,%s,%s)",
        (contact, w.tenant, name, email),
    )
    return contact


async def _subscription(
    w: World, contact: UUID, *, status: str = "active", renews_in_days: float | None = None
) -> None:
    now = datetime.now(UTC)
    renews = None if renews_in_days is None else now + timedelta(days=renews_in_days)
    await w.q(
        "INSERT INTO subscriptions(id,client_id,contact_id,plan,status,price,started_at,renews_at)"
        " VALUES (%s,%s,%s,'basic',%s,10,%s,%s)",
        (uuid4(), w.tenant, contact, status, now - timedelta(days=30), renews),
    )


def _spec(kind: str, value: str = "", **rest: Any) -> RecipientSpec:
    return RecipientSpec(kind=kind, value=value, **rest)  # type: ignore[arg-type]


async def _expand(
    w: World,
    specs: list[RecipientSpec],
    *,
    offset: int = 0,
    limit: int = 20,
    actor: ConsoleActor | None = None,
    tenant: UUID | None = None,
) -> Any:
    return await expand_recipients(
        _request(w),
        actor or _actor(w),
        tenant or w.tenant,
        specs,
        offset=offset,
        limit=limit,
    )


def _addresses(expanded: Any) -> list[str]:
    return [recipient.address for recipient in expanded.recipients]


async def test_named_people_resolve_and_duplicates_collapse(world: World) -> None:  # noqa: F811
    await _package(world, world.tenant, "subscriptions")
    aisyah = await _employee(world, "aisyah@example.test", "Nur Aisyah")
    hakim = await _employee(world, "hakim@example.test", "Hakim Rahman")
    await _customer(world, "Aina Customer", "Hakim@Example.test")
    expanded = await _expand(
        world,
        [
            _spec("employee", "Nur Aisyah"),
            _spec("employee", "nur aisyah"),  # the same person named twice
            _spec("address", "AISYAH@example.test"),  # an address plus the employee it belongs to
            _spec("employee", "Hakim Rahman"),
            _spec("customer", "Aina Customer"),  # a customer whose email is an employee's
            _spec("address", "walkin@example.test"),
            _spec("address", "Walkin@example.test"),  # typed twice, other case
        ],
    )
    assert _addresses(expanded) == [
        "aisyah@example.test",
        "hakim@example.test",
        "walkin@example.test",
    ]
    assert [r.employee_id for r in expanded.recipients] == [aisyah, hakim, None]
    assert expanded.not_included == {"duplicate": 4}
    # Keys are stable and hold no name or address.
    assert len({r.key for r in expanded.recipients}) == 3
    for recipient in expanded.recipients:
        assert recipient.address.lower() not in recipient.key.lower()
        assert recipient.label


async def test_customer_without_email_is_not_included_with_reason(
    world: World,  # noqa: F811
) -> None:
    await _package(world, world.tenant, "subscriptions")
    await _customer(world, "Faiz Nomail", None)
    await _customer(world, "Gina Blank", "  ")
    await _employee(world, None, "Hana Nowork")
    await _customer(world, "Ivan Mail", "ivan@example.test")
    expanded = await _expand(
        world,
        [
            _spec("customer", "Faiz Nomail"),
            _spec("customer", "Gina Blank"),
            _spec("employee", "Hana Nowork"),
            _spec("customer", "Ivan Mail"),
        ],
    )
    assert _addresses(expanded) == ["ivan@example.test"]
    assert expanded.recipients[0].customer_id is not None
    assert expanded.not_included == {"no_email": 3}


async def test_ambiguous_name_is_not_included_and_counted(world: World) -> None:  # noqa: F811
    await _package(world, world.tenant, "subscriptions")
    await _employee(world, "tan@example.test", "Aisyah Tan")
    await _employee(world, "lim@example.test", "Aisyah Lim")
    await _customer(world, "Rina Satu", "rina1@example.test")
    await _customer(world, "Rina Dua", "rina2@example.test")
    expanded = await _expand(
        world,
        [
            _spec("employee", "Aisyah"),
            _spec("customer", "Rina"),
            _spec("employee", "Nobody Atall"),
            _spec("customer", "Nobody Atall"),
            _spec("address", "ok@example.test"),
        ],
    )
    assert _addresses(expanded) == ["ok@example.test"]
    assert expanded.not_included == {"ambiguous": 2, "not_found": 2}


async def test_typed_address_is_validated_like_compose(world: World) -> None:  # noqa: F811
    with pytest.raises(HTTPException) as refused:
        await _expand(world, [_spec("address", "not an address")])
    assert refused.value.status_code == 422
    assert refused.value.detail == "email_recipient_invalid"


async def test_active_subscribers_group_only_returns_active_with_email(
    world: World,  # noqa: F811
) -> None:
    await _package(world, world.tenant, "subscriptions")
    active = await _customer(world, "Active One", "active1@example.test")
    await _subscription(world, active)
    twice = await _customer(world, "Active Twice", "active2@example.test")
    await _subscription(world, twice)
    await _subscription(world, twice, renews_in_days=3)
    paused = await _customer(world, "Paused One", "paused@example.test")
    await _subscription(world, paused, status="paused")
    cancelled = await _customer(world, "Cancelled One", "cancelled@example.test")
    await _subscription(world, cancelled, status="cancelled")
    nomail = await _customer(world, "Active Nomail", None)
    await _subscription(world, nomail)
    await _customer(world, "Never Subscribed", "never@example.test")
    expanded = await _expand(world, [_spec("group", group="active_subscribers")])
    wanted = sorted(
        [(active, "active1@example.test"), (twice, "active2@example.test")], key=lambda p: p[0]
    )
    assert [(r.customer_id, r.address) for r in expanded.recipients] == wanted
    assert expanded.not_included == {}


async def test_renewing_soon_group_respects_days(world: World) -> None:  # noqa: F811
    await _package(world, world.tenant, "subscriptions")
    for name, days in [("Soon", 3), ("Later", 10), ("Overdue", -2), ("Never", None)]:
        contact = await _customer(world, name, f"{name.lower()}@example.test")
        await _subscription(world, contact, renews_in_days=days)
    week = await _expand(world, [_spec("group", group="renewing_soon", days=7)])
    assert _addresses(week) == ["soon@example.test"]
    month = await _expand(world, [_spec("group", group="renewing_soon", days=30)])
    assert sorted(_addresses(month)) == ["later@example.test", "soon@example.test"]


async def test_group_larger_than_limit_returns_first_page_and_offset_gives_next(
    world: World,  # noqa: F811
) -> None:
    await _package(world, world.tenant, "subscriptions")
    ids: dict[UUID, str] = {}
    for number in range(5):
        contact = await _customer(world, f"Member {number}", f"member{number}@example.test")
        await _subscription(world, contact)
        ids[contact] = f"member{number}@example.test"
    ordered = [ids[key] for key in sorted(ids)]
    group = [_spec("group", group="active_subscribers")]
    first = await _expand(world, group, offset=0, limit=2)
    assert _addresses(first) == ordered[0:2] and first.not_included == {"over_limit": 3}
    second = await _expand(world, group, offset=2, limit=2)
    assert _addresses(second) == ordered[2:4] and second.not_included == {"over_limit": 1}
    last = await _expand(world, group, offset=4, limit=2)
    assert _addresses(last) == ordered[4:] and last.not_included == {}
    beyond = await _expand(world, group, offset=10, limit=2)
    assert beyond.recipients == [] and beyond.not_included == {}


async def test_group_needs_subscriptions_package_and_role(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact = await _customer(world, "Gated One", "gated@example.test")
    await _subscription(world, contact)
    paths: list[str] = []
    real_all, real_one = db.q.fetch_all, db.q.fetch_one

    async def spy_all(path: str, *args: Any, **kwargs: Any) -> Any:
        paths.append(path)
        return await real_all(path, *args, **kwargs)

    async def spy_one(path: str, *args: Any, **kwargs: Any) -> Any:
        paths.append(path)
        return await real_one(path, *args, **kwargs)

    monkeypatch.setattr(db.q, "fetch_all", spy_all)
    monkeypatch.setattr(db.q, "fetch_one", spy_one)

    def reads() -> list[str]:
        return [p for p in paths if p.startswith(("subscriptions/", "workspace/people"))]

    for specs in (
        [_spec("group", group="active_subscribers")],
        [_spec("customer", "Gated One")],
    ):
        with pytest.raises(HTTPException) as inactive:
            await _expand(world, specs)
        assert inactive.value.status_code == 403 and inactive.value.detail == "package_inactive"
    assert reads() == []

    await _package(world, world.tenant, "subscriptions")
    other = await _expand(world, [_spec("group", group="active_subscribers")])
    assert _addresses(other) == ["gated@example.test"]
    # The Subscriptions tab lets every role of the tenant read; another tenant's member may not.
    reader = _actor(world, Role.READ_ONLY)
    assert _addresses(await _expand(world, [_spec("customer", "Gated One")], actor=reader)) == [
        "gated@example.test"
    ]
    paths.clear()
    stranger = _actor(world, Role.TENANT_ADMIN, tenant=world.other_tenant)
    with pytest.raises(HTTPException) as foreign:
        await _expand(world, [_spec("group", group="active_subscribers")], actor=stranger)
    assert foreign.value.status_code == 403
    assert reads() == []

    # Employees need the hr package, as the compose path does; another tenant has none.
    await _package(world, world.other_tenant, "subscriptions")
    with pytest.raises(HTTPException) as no_hr:
        await _expand(
            world,
            [_spec("employee", "Nur Aisyah")],
            actor=stranger,
            tenant=world.other_tenant,
        )
    assert no_hr.value.status_code == 403 and no_hr.value.detail == "package_inactive"
    assert reads() == []


def _session(w: World) -> EmailSession:
    return EmailSession(
        session_id=w.sessions["staff"],
        subject_id=w.users["staff"],
        tenant_id=w.tenant,
        role="tenant_staff",
        token_hash=digest(w.tokens["staff"]),  # type: ignore[arg-type]
        csrf_hash=None,
    )


async def test_remaining_sends_counts_the_hourly_window(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = world.app.state.email_service
    session = _session(world)
    await world.call("admin", "PUT", "/console/v1/email/settings", {"enabled": True})
    with pytest.raises(EmailPolicyError) as unconnected:
        await service.remaining_sends(session)
    assert unconnected.value.code == "mailbox_not_connected"
    await _enable_and_connect(world, monkeypatch)
    assert await service.remaining_sends(session) == 20

    async def mark(status: str, minutes_ago: int) -> None:
        draft = await _insert_draft(world, to=[f"p{uuid4().hex[:6]}@example.test"])
        await world.q(
            "UPDATE email_drafts SET status=%s, sent_at=CASE WHEN %s='sent' THEN "
            "now()-make_interval(mins=>%s) END, updated_at=now()-make_interval(mins=>%s) "
            "WHERE id=%s",
            (status, status, minutes_ago, minutes_ago, draft),
        )

    for _ in range(3):
        await mark("sent", 5)
    await mark("sent", 120)  # outside the rolling hour
    assert await service.remaining_sends(session) == 17
    await mark("sending", 2)  # a claim counts as a send
    await mark("sending", 120)  # a stale claim does not
    assert await service.remaining_sends(session) == 16
    for _ in range(20):
        await mark("sent", 1)
    assert await service.remaining_sends(session) == 0  # never negative
