"""The console notification routes and the purge job (plan 2a, task 7; design 8): a person reads
and marks only their own rows, in the company of their session, whatever their role (disposable
database, synthetic data).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from csa.console.access import reader_may_post
from csa.jobs.clock import FakeClock
from csa.jobs.console_notifications import run_console_notifications_purge

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

NOW = datetime(2031, 3, 1, 12, 0, tzinfo=UTC)
LINK = {"route": "inbox", "conversation": "c-1"}


def path(w: SessionWorld, tail: str = "", query: str = "", tenant: int = 0) -> str:
    extra = f"&{query}" if query else ""
    return f"/console/v1/notifications{tail}?client_id={w.tenants[tenant]}{extra}"


async def put(
    w: SessionWorld,
    user_id: str,
    *,
    tenant: int = 0,
    age: timedelta = timedelta(0),
    read: bool = False,
    key: str | None = None,
    kind: str = "inbox.overdue",
) -> str:
    """A notification row for ``user_id``, written as the owner; returns its id."""
    row_id = str(uuid4())
    created = NOW - age
    await w.q(
        "INSERT INTO console_notifications (id, client_id, user_id, kind, priority, message_key,"
        " params, link, dedupe_key, read_at, created_at)"
        " VALUES (%s, %s, %s, %s, 'high', %s, %s::jsonb, %s::jsonb, %s, %s, %s)",
        row_id,
        w.tenants[tenant],
        user_id,
        kind,
        kind,
        json.dumps({"customer": "Siti", "waited_minutes": 12}),
        json.dumps(LINK),
        key or f"k-{row_id}",
        created if read else None,
        created,
    )
    return row_id


async def person(w: SessionWorld, role: str = "tenant_staff", tenant: int = 0) -> Any:
    client, accepted = await w.member(role=role, tenant=w.tenants[tenant])
    return client, accepted["user"]["id"]


async def test_a_member_sees_only_their_own_rows_newest_first(sessions: SessionWorld) -> None:
    w = sessions
    me, me_id = await person(w)
    other, other_id = await person(w)
    away, away_id = await person(w, tenant=1)
    oldest = await put(w, me_id, age=timedelta(hours=3))
    newest = await put(w, me_id, age=timedelta(minutes=1))
    middle = await put(w, me_id, age=timedelta(hours=1), read=True)
    await put(w, other_id)
    await put(w, away_id, tenant=1)
    answer = await me.get("/console/v1/notifications")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert [item["id"] for item in body["items"]] == [newest, middle, oldest]
    assert body["unread"] == 2
    first = body["items"][0]
    assert set(first) == {
        "id",
        "kind",
        "priority",
        "message_key",
        "params",
        "link",
        "read_at",
        "created_at",
    }
    assert first["kind"] == "inbox.overdue" and first["message_key"] == "inbox.overdue"
    assert first["priority"] == "high" and first["link"] == LINK
    assert first["params"] == {"customer": "Siti", "waited_minutes": 12}
    assert first["read_at"] is None and body["items"][1]["read_at"] is not None
    # The others see theirs only; the company is the session's, even when named explicitly.
    assert len((await other.get("/console/v1/notifications")).json()["items"]) == 1
    assert len((await away.get("/console/v1/notifications")).json()["items"]) == 1
    cross = await me.get(path(w, tenant=1))
    assert cross.status_code == 403


async def test_limit_unread_filter_and_bounds(sessions: SessionWorld) -> None:
    w = sessions
    me, me_id = await person(w)
    ids = [await put(w, me_id, age=timedelta(minutes=n), read=n % 2 == 0) for n in range(6)]
    body = (await me.get("/console/v1/notifications?limit=2")).json()
    assert [item["id"] for item in body["items"]] == ids[:2]
    assert body["unread"] == 3  # the count is of all unread rows, not of the page
    only = (await me.get("/console/v1/notifications?unread=true")).json()
    assert [item["id"] for item in only["items"]] == [ids[1], ids[3], ids[5]]
    assert only["unread"] == 3
    assert len((await me.get("/console/v1/notifications?limit=50")).json()["items"]) == 6
    assert len((await me.get("/console/v1/notifications?limit=1")).json()["items"]) == 1
    for bad in ("limit=0", "limit=51", "limit=-1", "limit=abc"):
        assert (await me.get(f"/console/v1/notifications?{bad}")).status_code == 422, bad


async def test_count_and_marking_by_ids_and_all(sessions: SessionWorld) -> None:
    w = sessions
    me, me_id = await person(w)
    other, other_id = await person(w)
    mine = [await put(w, me_id) for _ in range(3)]
    theirs = await put(w, other_id)
    assert (await me.get("/console/v1/notifications/count")).json() == {"unread": 3}
    # Mark two by id; an id of somebody else's row and an unknown id are ignored, no error.
    marked = await w.mutate(
        me,
        "/console/v1/notifications/read",
        {"ids": [mine[0], mine[1], theirs, str(uuid4())]},
    )
    assert marked.status_code == 200, marked.text
    assert marked.json() == {"updated": 2}
    assert (await me.get("/console/v1/notifications/count")).json() == {"unread": 1}
    assert (await other.get("/console/v1/notifications/count")).json() == {"unread": 1}
    stored = await w.q("SELECT read_at FROM console_notifications WHERE id = %s", theirs)
    assert stored[0]["read_at"] is None
    # Marking the same ids again changes nothing.
    again = await w.mutate(me, "/console/v1/notifications/read", {"ids": [mine[0]]})
    assert again.json() == {"updated": 0}
    everything = await w.mutate(me, "/console/v1/notifications/read", {"all": True})
    assert everything.json() == {"updated": 1}
    assert (await me.get("/console/v1/notifications/count")).json() == {"unread": 0}
    assert (await me.get("/console/v1/notifications")).json()["unread"] == 0
    # Somebody else's row is still unread, and so is the same person's row in another company.
    assert (await other.get("/console/v1/notifications/count")).json() == {"unread": 1}
    first_read = (await me.get("/console/v1/notifications")).json()["items"][0]["read_at"]
    assert first_read is not None
    # Read-at is kept on a repeat of "all".
    assert (await w.mutate(me, "/console/v1/notifications/read", {"all": True})).json() == {
        "updated": 0
    }
    kept = (await me.get("/console/v1/notifications")).json()["items"][0]["read_at"]
    assert kept == first_read


async def test_marking_never_crosses_companies(sessions: SessionWorld) -> None:
    w = sessions
    me, _me_id = await person(w)
    _away, away_id = await person(w, tenant=1)
    foreign = await put(w, away_id, tenant=1)
    done = await w.mutate(me, "/console/v1/notifications/read", {"ids": [foreign]})
    assert done.json() == {"updated": 0}
    done = await w.mutate(me, "/console/v1/notifications/read", {"all": True})
    assert done.json() == {"updated": 0}
    stored = await w.q("SELECT read_at FROM console_notifications WHERE id = %s", foreign)
    assert stored[0]["read_at"] is None


async def test_exactly_one_of_ids_and_all(sessions: SessionWorld) -> None:
    w = sessions
    me, me_id = await person(w)
    row = await put(w, me_id)
    for body in (
        {},
        {"ids": [row], "all": True},
        {"ids": [row], "all": False},
        {"all": False},
        {"ids": None, "all": None},
        {"ids": ["not-a-uuid"]},
        {"all": True, "extra": 1},
    ):
        answer = await w.mutate(me, "/console/v1/notifications/read", body)
        assert answer.status_code == 422, (body, answer.text)
    assert (await me.get("/console/v1/notifications/count")).json() == {"unread": 1}


async def test_read_only_reads_and_marks_their_own(sessions: SessionWorld) -> None:
    w = sessions
    reader, reader_id = await person(w, role="read_only")
    _, other_id = await person(w)
    mine = await put(w, reader_id)
    await put(w, other_id)
    assert (await reader.get("/console/v1/notifications")).json()["unread"] == 1
    assert (await reader.get("/console/v1/notifications/count")).json() == {"unread": 1}
    done = await w.mutate(reader, "/console/v1/notifications/read", {"ids": [mine]})
    assert done.status_code == 200, done.text
    assert done.json() == {"updated": 1}
    # The exemption is for this one route: another write is still refused.
    refused = await w.mutate(reader, "/console/v1/inbox/views", {"name": "x", "filters": {}})
    assert refused.status_code == 403


async def test_reader_exemption_matches_the_notification_route_only() -> None:
    route = "/notifications/read"
    path_ = "/console/v1/notifications/read"
    assert reader_may_post("POST", route, path_)
    assert reader_may_post("POST", "/console/v1" + route, path_)
    assert not reader_may_post("GET", route, path_)
    assert not reader_may_post("PUT", route, path_)
    assert not reader_may_post("POST", "/notifications", path_)
    assert not reader_may_post("POST", route, "/console/v1/notifications/read/")
    assert not reader_may_post("POST", route, "/console/v1/notifications/readx")
    assert not reader_may_post("POST", None, path_)
    # A route is never paired with the other route's path.
    item = uuid4()
    summary_route = "/training/items/{item_id}/summary"
    summary_path = f"/console/v1/training/items/{item}/summary"
    assert reader_may_post("POST", summary_route, summary_path)
    assert not reader_may_post("POST", route, summary_path)
    assert not reader_may_post("POST", "/console/v1" + route, summary_path)
    assert not reader_may_post("POST", summary_route, path_)
    assert not reader_may_post("POST", "/console/v1" + summary_route, path_)


async def test_a_platform_admin_has_no_notifications(sessions: SessionWorld) -> None:
    w = sessions
    _, staff_id = await person(w)
    await put(w, staff_id)
    listed = await w.admin.get(path(w))
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": [], "unread": 0}
    assert (await w.admin.get(path(w, "/count"))).json() == {"unread": 0}
    marked = await w.mutate(w.admin, path(w, "/read"), {"all": True})
    assert marked.status_code == 200, marked.text
    assert marked.json() == {"updated": 0}
    assert (await w.mutate(w.admin, path(w, "/read"), {})).status_code == 422
    stored = await w.q("SELECT count(*) AS n FROM console_notifications WHERE read_at IS NULL")
    assert stored[0]["n"] == 1
    # A platform admin must name a company, as everywhere else.
    assert (await w.admin.get("/console/v1/notifications")).status_code == 400


async def test_the_user_is_the_session_never_a_parameter(sessions: SessionWorld) -> None:
    w = sessions
    me, _ = await person(w)
    _, other_id = await person(w)
    await put(w, other_id)
    for query in (f"user_id={other_id}", f"actor_id={other_id}"):
        body = (await me.get(f"/console/v1/notifications?{query}")).json()
        assert body == {"items": [], "unread": 0}
    forged = await w.mutate(
        me, "/console/v1/notifications/read", {"all": True, "user_id": other_id}
    )
    assert forged.status_code == 422


async def test_unauthenticated_and_csrf(sessions: SessionWorld) -> None:
    w = sessions
    me, me_id = await person(w)
    row = await put(w, me_id)
    anonymous = await w.browser()
    for url in (
        "/console/v1/notifications",
        "/console/v1/notifications/count",
    ):
        assert (await anonymous.get(url)).status_code == 401, url
    headers = w.headers(anonymous)
    unauth = await anonymous.post(
        "/console/v1/notifications/read", json={"all": True}, headers=headers
    )
    assert unauth.status_code == 401
    # A signed-in post without the CSRF token is refused and marks nothing.
    bare = await me.post("/console/v1/notifications/read", json={"ids": [row]})
    assert bare.status_code == 403, bare.text
    wrong = await me.post(
        "/console/v1/notifications/read",
        json={"ids": [row]},
        headers={**w.headers(me), "X-CSRF-Token": "wrong"},
    )
    assert wrong.status_code == 403, wrong.text
    assert (await me.get("/console/v1/notifications/count")).json() == {"unread": 1}


async def test_no_package_is_needed(sessions: SessionWorld) -> None:
    # No add-on is activated in this world: the routes answer all the same.
    w = sessions
    me, me_id = await person(w)
    await put(w, me_id, kind="hr.task_due")
    assert (await me.get("/console/v1/notifications/count")).json() == {"unread": 1}


# --- the purge job --------------------------------------------------------------------------------


async def _purge(w: SessionWorld, tenant: int, **kw: Any) -> dict[str, Any]:
    return await run_console_notifications_purge(
        w.pool, UUID(w.tenants[tenant]), FakeClock(NOW), **kw
    )


async def _remaining(w: SessionWorld, tenant: int) -> set[str]:
    rows = await w.q("SELECT id FROM console_notifications WHERE client_id = %s", w.tenants[tenant])
    return {str(row["id"]) for row in rows}


async def test_the_purge_deletes_thirty_one_day_rows_and_keeps_twenty_nine_per_company(
    sessions: SessionWorld,
) -> None:
    w = sessions
    _, a_id = await person(w)
    _, b_id = await person(w, tenant=1)
    old_a = await put(w, a_id, age=timedelta(days=31), read=True)
    new_a = await put(w, a_id, age=timedelta(days=29))
    unread_old_a = await put(w, a_id, age=timedelta(days=45))  # unread rows expire too
    old_b = await put(w, b_id, tenant=1, age=timedelta(days=31))
    new_b = await put(w, b_id, tenant=1, age=timedelta(days=29), read=True)
    assert await _purge(w, 0) == {"purged": 2, "email_purged": 0}
    assert await _remaining(w, 0) == {new_a}
    # Company A's run never touched company B's rows.
    assert await _remaining(w, 1) == {old_b, new_b}
    assert await _purge(w, 1) == {"purged": 1, "email_purged": 0}
    assert await _remaining(w, 1) == {new_b}
    assert await _purge(w, 0) == {"purged": 0, "email_purged": 0}
    assert old_a not in await _remaining(w, 0) and unread_old_a not in await _remaining(w, 0)


async def test_the_purge_works_in_batches(sessions: SessionWorld) -> None:
    w = sessions
    _, a_id = await person(w)
    for n in range(5):
        await put(w, a_id, age=timedelta(days=40, minutes=n))
    keep = await put(w, a_id, age=timedelta(days=1))
    assert await _purge(w, 0, batch_size=2) == {"purged": 5, "email_purged": 0}
    assert await _remaining(w, 0) == {keep}


async def _put_outbox(w: SessionWorld, user_id: str, *, tenant: int = 0, age: timedelta) -> str:
    """An outbox row for ``user_id`` (any status), written as the owner; returns its id."""
    row_id = str(uuid4())
    await w.q(
        "INSERT INTO console_email_outbox (id, client_id, user_id, notification_id, kind,"
        " priority, message_key, created_at)"
        " VALUES (%s, %s, %s, %s, 'inbox.overdue', 'high', 'inbox.overdue', %s)",
        row_id,
        w.tenants[tenant],
        user_id,
        str(uuid4()),
        NOW - age,
    )
    return row_id


async def _outbox_remaining(w: SessionWorld, tenant: int) -> set[str]:
    rows = await w.q("SELECT id FROM console_email_outbox WHERE client_id = %s", w.tenants[tenant])
    return {str(row["id"]) for row in rows}


async def test_the_purge_also_deletes_old_email_outbox_rows_per_company(
    sessions: SessionWorld,
) -> None:
    w = sessions
    _, a_id = await person(w)
    _, b_id = await person(w, tenant=1)
    old_a = await _put_outbox(w, a_id, age=timedelta(days=31))
    new_a = await _put_outbox(w, a_id, age=timedelta(days=29))
    old_b = await _put_outbox(w, b_id, tenant=1, age=timedelta(days=45))
    keep_note = await put(w, a_id, age=timedelta(days=2))
    assert await _purge(w, 0) == {"purged": 0, "email_purged": 1}
    assert await _outbox_remaining(w, 0) == {new_a} and old_a not in await _outbox_remaining(w, 0)
    assert await _remaining(w, 0) == {keep_note}
    # Company A's run never touched company B's outbox.
    assert await _outbox_remaining(w, 1) == {old_b}
    assert await _purge(w, 1) == {"purged": 0, "email_purged": 1}
    assert await _outbox_remaining(w, 1) == set()
    assert await _purge(w, 0) == {"purged": 0, "email_purged": 0}


async def test_the_outbox_purge_works_in_batches(sessions: SessionWorld) -> None:
    w = sessions
    _, a_id = await person(w)
    for n in range(5):
        await _put_outbox(w, a_id, age=timedelta(days=40, minutes=n))
    keep = await _put_outbox(w, a_id, age=timedelta(days=1))
    assert await _purge(w, 0, batch_size=2) == {"purged": 0, "email_purged": 5}
    assert await _outbox_remaining(w, 0) == {keep}
