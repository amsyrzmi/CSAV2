"""The Inbox saved views (plan 1a, task 9; design 6.1): a person's named filter sets, private to
them, at most 20 each, names unique per person ignoring case, filters validated by the list's own
rules (disposable database, synthetic data).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from csa.console.inbox_queue_list import InboxFilters, QueueFilter
from pydantic import ValidationError

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

VIEWS_MAX = 20


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "customer_support")


def views_path(w: SessionWorld, view_id: str | None = None, tenant: int = 0) -> str:
    tail = f"/{view_id}" if view_id else ""
    return f"/console/v1/inbox/views{tail}?client_id={w.tenants[tenant]}"


async def _create(
    w: SessionWorld,
    name: str,
    filters: dict[str, Any] | None = None,
    *,
    client: Any = None,
    tenant: int = 0,
) -> Any:
    return await w.mutate(
        client or w.admin, views_path(w, tenant=tenant), {"name": name, "filters": filters or {}}
    )


async def _made(
    w: SessionWorld, name: str, filters: dict[str, Any] | None = None, **kw: Any
) -> str:
    response = await _create(w, name, filters, **kw)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _names(w: SessionWorld, *, client: Any = None, tenant: int = 0) -> list[str]:
    response = await (client or w.admin).get(views_path(w, tenant=tenant))
    assert response.status_code == 200, response.text
    return [view["name"] for view in response.json()]


async def _audit(w: SessionWorld, action: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT actor_id, client_id, target_id, details FROM console_audit"
        " WHERE action = %s AND client_id = %s ORDER BY created_at",
        action,
        UUID(w.tenants[0]),
    )


async def test_create_list_rename_replace_and_delete(sessions: SessionWorld) -> None:
    w = sessions
    created = await _create(
        w, "  Urgent, mine  ", {"state": ["needs_staff"], "assignee": "me", "priority_min": "high"}
    )
    assert created.status_code == 201, created.text
    view = created.json()
    assert set(view) == {"id", "name", "filters", "created_at"}
    assert view["name"] == "Urgent, mine"
    assert view["filters"]["state"] == ["needs_staff"]
    assert view["filters"]["assignee"] == "me" and view["filters"]["priority_min"] == "high"
    assert "q" not in view["filters"] and "needs_review" not in view["filters"]
    second = await _made(w, "WhatsApp", {"channel": "whatsapp", "order": "activity"})
    # Newest first.
    assert await _names(w) == ["WhatsApp", "Urgent, mine"]
    renamed = await w.mutate(w.admin, views_path(w, view["id"]), {"name": "Mine"}, method="PUT")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Mine"
    assert renamed.json()["filters"] == view["filters"]
    assert renamed.json()["created_at"] == view["created_at"]
    member = str(uuid4())
    replaced = await w.mutate(
        w.admin,
        views_path(w, view["id"]),
        {"filters": {"assignee": member, "state": ["waiting_customer"]}},
        method="PUT",
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["name"] == "Mine"
    assert replaced.json()["filters"]["assignee"] == member
    assert replaced.json()["filters"]["priority_min"] is None
    both = await w.mutate(
        w.admin,
        views_path(w, second),
        {"name": "Overdue", "filters": {"overdue": True, "waiting_over": 30}},
        method="PUT",
    )
    assert both.status_code == 200, both.text
    assert both.json()["filters"]["overdue"] is True
    assert both.json()["filters"]["waiting_over"] == 30
    assert both.json()["filters"]["channel"] is None
    stored = await w.q("SELECT filters FROM inbox_saved_views WHERE id = %s", UUID(second))
    assert InboxFilters.model_validate(stored[0]["filters"]).overdue is True
    gone = await w.mutate(w.admin, views_path(w, second), method="DELETE")
    assert gone.status_code == 204, gone.text
    assert await _names(w) == ["Mine"]
    again = await w.mutate(w.admin, views_path(w, second), method="DELETE")
    assert again.status_code == 404


async def test_at_most_twenty_views_each(sessions: SessionWorld) -> None:
    w = sessions
    ids = [await _made(w, f"View {n}") for n in range(VIEWS_MAX)]
    over = await _create(w, "One too many")
    assert over.status_code == 409, over.text
    assert len(await _names(w)) == VIEWS_MAX
    # The limit is per person: another member of the company still has room.
    staff, _ = await w.member()
    assert (await _create(w, "One too many", client=staff)).status_code == 201
    # Deleting one makes room again.
    deleted = await w.mutate(w.admin, views_path(w, ids[0]), method="DELETE")
    assert deleted.status_code == 204
    assert (await _create(w, "One too many")).status_code == 201
    assert (await _create(w, "Twenty-first")).status_code == 409


async def test_two_creates_at_once_never_exceed_the_limit(sessions: SessionWorld) -> None:
    w = sessions
    for n in range(VIEWS_MAX - 1):
        await _made(w, f"View {n}")
    both = await asyncio.gather(_create(w, "Last A"), _create(w, "Last B"))
    assert sorted(r.status_code for r in both) == [201, 409], [r.text for r in both]
    rows = await w.q(
        "SELECT count(*) AS n FROM inbox_saved_views WHERE client_id = %s AND user_id = %s",
        UUID(w.tenants[0]),
        UUID(w.admin_id),
    )
    assert rows[0]["n"] == VIEWS_MAX


async def test_a_name_is_unique_per_person_ignoring_case(sessions: SessionWorld) -> None:
    w = sessions
    first = await _made(w, "Urgent")
    for name in ("urgent", "URGENT", "  Urgent  "):
        duplicate = await _create(w, name)
        assert duplicate.status_code == 409, name
    other = await _made(w, "Later")
    clash = await w.mutate(w.admin, views_path(w, other), {"name": "uRgEnT"}, method="PUT")
    assert clash.status_code == 409
    assert await _names(w) == ["Later", "Urgent"]
    # A view may change the case of its own name.
    own = await w.mutate(w.admin, views_path(w, first), {"name": "URGENT"}, method="PUT")
    assert own.status_code == 200, own.text
    # Another person, or the same person in another company, may use the name.
    staff, _ = await w.member()
    assert (await _create(w, "urgent", client=staff)).status_code == 201
    assert (await _create(w, "urgent", tenant=1)).status_code == 201
    # Two creates of one name at once make one view.
    both = await asyncio.gather(_create(w, "Twice"), _create(w, "twice"))
    assert sorted(r.status_code for r in both) == [201, 409]


async def test_a_view_is_private_to_its_person(sessions: SessionWorld) -> None:
    w = sessions
    mine = await _made(w, "Admin's view", {"state": ["needs_staff"]})
    staff, _ = await w.member()
    theirs = await _made(w, "Staff view", client=staff)
    assert await _names(w, client=staff) == ["Staff view"]
    assert await _names(w) == ["Admin's view"]
    for method, body in (("PUT", {"name": "Taken over"}), ("DELETE", None)):
        response = await w.mutate(staff, views_path(w, mine), body, method=method)
        assert response.status_code == 404, method
    assert (await w.mutate(w.admin, views_path(w, theirs), method="DELETE")).status_code == 404
    unknown = await w.mutate(w.admin, views_path(w, str(uuid4())), {"name": "x"}, method="PUT")
    assert unknown.status_code == 404
    assert await _names(w) == ["Admin's view"]
    assert await _names(w, client=staff) == ["Staff view"]


async def test_another_company_never_sees_or_changes_a_view(sessions: SessionWorld) -> None:
    w = sessions
    mine = await _made(w, "Company A", {"assignee": "none"})
    foreign, _ = await w.member(tenant=w.tenants[1])
    assert await _names(w, client=foreign, tenant=1) == []
    assert (await foreign.get(views_path(w))).status_code == 403
    for method, body in (("PUT", {"name": "Moved"}), ("DELETE", None)):
        response = await w.mutate(foreign, views_path(w, mine, tenant=1), body, method=method)
        assert response.status_code == 404, method
    # The same person (a platform admin) acting for the other company does not see it either.
    assert await _names(w, tenant=1) == []
    moved = await w.mutate(w.admin, views_path(w, mine, tenant=1), {"name": "Moved"}, method="PUT")
    assert moved.status_code == 404
    gone = await w.mutate(w.admin, views_path(w, mine, tenant=1), method="DELETE")
    assert gone.status_code == 404
    assert await _names(w) == ["Company A"]


BAD_FILTERS: tuple[dict[str, Any], ...] = (
    {"q": "aisyah"},
    {"before": "YWN0aXZpdHk"},
    {"cursor": "x"},
    {"limit": 20},
    {"needs_review": True},
    {"colour": "red"},
    {"state": ["open"]},
    {"state": "needs_staff"},
    {"assignee": "someone"},
    {"assignee": ""},
    {"priority_min": "highest"},
    {"reason": ["whim"]},
    {"topic": ["weather"]},
    {"channel": "web"},
    {"sender": "vip"},
    {"overdue": "perhaps"},
    {"waiting_over": 0},
    {"waiting_over": 525601},
    {"order": "random"},
    {"order": "priority"},
    {"order": "priority", "state": ["needs_staff", "waiting_customer"]},
    {"order": "priority", "overdue": True},
)
BAD_NAMES: tuple[Any, ...] = ("", "   ", "x" * 61, None, 7)


async def test_an_invalid_name_or_filter_is_422_and_changes_nothing(
    sessions: SessionWorld,
) -> None:
    w = sessions
    for filters in BAD_FILTERS:
        response = await _create(w, "Bad", filters)
        assert response.status_code == 422, filters
    for name in BAD_NAMES:
        response = await w.mutate(w.admin, views_path(w), {"name": name, "filters": {}})
        assert response.status_code == 422, name
    incomplete = (
        {"name": "No filters"},
        {"filters": {}},
        {},
        {"name": "x", "filters": {}, "id": 1},
    )
    for body in incomplete:
        assert (await w.mutate(w.admin, views_path(w), body)).status_code == 422, body
    assert await _names(w) == []
    view = await _made(w, "x" * 60)
    padded = await _made(w, "  " + "y" * 60 + "  ")
    assert await _names(w) == ["y" * 60, "x" * 60]
    for filters in BAD_FILTERS:
        response = await w.mutate(w.admin, views_path(w, view), {"filters": filters}, method="PUT")
        assert response.status_code == 422, filters
    for name in BAD_NAMES:
        response = await w.mutate(w.admin, views_path(w, view), {"name": name}, method="PUT")
        assert response.status_code == 422, name
    empty = await w.mutate(w.admin, views_path(w, padded), {}, method="PUT")
    assert empty.status_code == 422
    bad_id = await w.mutate(w.admin, views_path(w, "not-a-uuid"), {"name": "z"}, method="PUT")
    assert bad_id.status_code == 422
    assert await _names(w) == ["y" * 60, "x" * 60]
    # The priority order is for the Needs us tab: accepted with state needs_staff alone.
    fine = await _create(w, "By priority", {"order": "priority", "state": ["needs_staff"]})
    assert fine.status_code == 201, fine.text


async def test_the_stored_filter_model_is_the_lists_model() -> None:
    """A stored view holds the list's filters minus the search: the field types are shared."""
    shared = set(InboxFilters.model_fields)
    assert shared == {
        "state",
        "assignee",
        "priority_min",
        "reason",
        "topic",
        "channel",
        "sender",
        "overdue",
        "waiting_over",
        "order",
    }
    assert set(QueueFilter.model_fields) == shared | {"q", "needs_review"}
    for name in shared:
        assert (
            InboxFilters.model_fields[name].annotation == QueueFilter.model_fields[name].annotation
        ), name
    with pytest.raises(ValidationError):
        InboxFilters.model_validate({"order": "priority"})
    loaded = InboxFilters.model_validate({"state": ["needs_staff"], "order": "priority"})
    assert QueueFilter(**loaded.model_dump()).effective_order() == "priority"


async def test_staff_and_admins_may_keep_views_and_read_only_may_not(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member()
    tenant_admin, _ = await w.member(role="tenant_admin")
    reader, _ = await w.member(role="read_only")
    for client in (staff, tenant_admin, w.admin):
        view = await _made(w, "Mine", {"assignee": "me"}, client=client)
        assert await _names(w, client=client) == ["Mine"]
        put = await w.mutate(client, views_path(w, view), {"name": "Renamed"}, method="PUT")
        assert put.status_code == 200, put.text
    assert (await reader.get(views_path(w))).status_code == 403
    assert (await _create(w, "Reader", client=reader)).status_code == 403
    put = await w.mutate(reader, views_path(w, view), {"name": "Reader"}, method="PUT")
    assert put.status_code == 403
    assert (await w.mutate(reader, views_path(w, view), method="DELETE")).status_code == 403
    # A platform admin acts only for the company they select.
    assert (await w.admin.get("/console/v1/inbox/views")).status_code == 400


async def test_each_change_is_audited_without_details(sessions: SessionWorld) -> None:
    w = sessions
    view = await _made(w, "Audited", {"state": ["needs_staff"]})
    assert (await _create(w, "audited")).status_code == 409
    renamed = await w.mutate(w.admin, views_path(w, view), {"name": "Audited 2"}, method="PUT")
    assert renamed.status_code == 200
    assert (await w.mutate(w.admin, views_path(w, view), method="DELETE")).status_code == 204
    for action in ("inbox.view_created", "inbox.view_updated", "inbox.view_deleted"):
        rows = await _audit(w, action)
        assert len(rows) == 1, action
        assert str(rows[0]["actor_id"]) == w.admin_id
        assert rows[0]["target_id"] == view and rows[0]["details"] == {}
