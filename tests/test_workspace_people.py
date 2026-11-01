"""Company directory search and the caller's own manager lookup over HTTP."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.workspace import directory
from fastapi import HTTPException
from psycopg.rows import dict_row

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/workspace/people"


@pytest.fixture(autouse=True)
def _fresh_throttle() -> None:
    directory.clear_throttle()


async def employee(
    w: SessionWorld,
    tenant: str,
    name: str,
    *,
    email: str | None = None,
    manager: UUID | None = None,
    active: bool = True,
    user: str | None = None,
    job_title: str = "Analyst",
) -> UUID:
    eid = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,job_title,department,work_email,"
        "manager_id,is_active) VALUES (%s,%s,%s,%s,'Secret Dept',%s,%s,%s)",
        tenant, eid, name, job_title, email, manager, active,
    )  # fmt: skip
    if user:
        await w.q(
            "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
            tenant, user, eid,
        )  # fmt: skip
    return eid


async def staff(w: SessionWorld, tenant: str | None = None) -> tuple[httpx.AsyncClient, str]:
    browser, me = await w.member(role="tenant_staff", tenant=tenant)
    return browser, me["user"]["id"]


async def search(browser: httpx.AsyncClient, tenant: str, q: str) -> httpx.Response:
    return await browser.get(BASE, params={"q": q, "client_id": tenant})


async def mine(browser: httpx.AsyncClient, tenant: str) -> httpx.Response:
    return await browser.get(f"{BASE}/me", params={"client_id": tenant})


async def test_search_returns_only_three_fields_and_matches_name_or_email(
    sessions: SessionWorld,
) -> None:
    w = sessions
    t0, t1 = w.tenants
    await activate(w, t0, "hr")
    browser, _ = await staff(w)
    await employee(w, t0, "Ali Hassan", email="ali.h@example.test")
    await employee(w, t0, "Zed Other", email="alia@example.test")
    await employee(w, t0, "Bo Nomatch", email="bo@example.test")
    await employee(w, t0, "Gone Ali", active=False)
    await employee(w, t1, "Ali Other Tenant", email="ali@other.test")
    r = await search(browser, t0, "  ALI ")
    assert r.status_code == 200, r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert [p["display_name"] for p in r.json()] == ["Ali Hassan", "Zed Other"]
    for person in r.json():
        assert set(person) == {"employee_id", "display_name", "job_title", "work_email"}
    assert "Secret Dept" not in r.text and "department" not in r.text
    assert "manager" not in r.text and "Other Tenant" not in r.text


async def test_search_limit_wildcards_and_bounds(sessions: SessionWorld) -> None:
    w = sessions
    t0 = w.tenants[0]
    await activate(w, t0, "hr")
    browser, _ = await staff(w)
    for i in range(11):
        await employee(w, t0, f"Pat {i:02d}")
    await employee(w, t0, "100% Real")
    await employee(w, t0, "a_b")
    await employee(w, t0, "axb")
    assert len((await search(browser, t0, "pat")).json()) == 10
    assert [p["display_name"] for p in (await search(browser, t0, "%")).json()] == ["100% Real"]
    assert [p["display_name"] for p in (await search(browser, t0, "a_b")).json()] == ["a_b"]
    assert (await search(browser, t0, "")).status_code == 422
    assert (await search(browser, t0, "   ")).status_code == 422
    assert (await search(browser, t0, "x" * 81)).status_code == 422
    assert (await search(browser, t0, "x" * 80)).status_code == 200


async def _names(browser: httpx.AsyncClient, tenant: str, q: str) -> list[str]:
    r = await search(browser, tenant, q)
    assert r.status_code == 200, r.text
    return [p["display_name"] for p in r.json()]


async def test_search_finds_a_job_title(sessions: SessionWorld) -> None:
    w = sessions
    t0 = w.tenants[0]
    await activate(w, t0, "hr")
    browser, _ = await staff(w)
    await employee(w, t0, "Siti Aminah", job_title="Finance Manager")
    await employee(w, t0, "Bo Other")
    assert await _names(browser, t0, "finance manager") == ["Siti Aminah"]
    assert await _names(browser, t0, "FINANCE") == ["Siti Aminah"]


async def test_search_matches_every_word_in_any_order(sessions: SessionWorld) -> None:
    w = sessions
    t0 = w.tenants[0]
    await activate(w, t0, "hr")
    browser, _ = await staff(w)
    await employee(w, t0, "Nur Aina binti Rahman")
    await employee(w, t0, "Danial Hakim")
    assert await _names(browser, t0, "Aina Rahman") == ["Nur Aina binti Rahman"]
    assert await _names(browser, t0, "Hakim Danial") == ["Danial Hakim"]
    assert await _names(browser, t0, "Aina,  Rahman") == ["Nur Aina binti Rahman"]
    assert await _names(browser, t0, "Aina Zulkifli") == []
    # Only the first 5 words count: "x" is the fifth and matches nobody; "zzz" is the sixth.
    assert await _names(browser, t0, "nur aina binti rahman x y") == []
    assert await _names(browser, t0, "nur aina binti rahman nur zzz") == ["Nur Aina binti Rahman"]


async def test_search_mixes_a_name_word_and_a_title_word(sessions: SessionWorld) -> None:
    w = sessions
    t0 = w.tenants[0]
    await activate(w, t0, "hr")
    browser, _ = await staff(w)
    await employee(w, t0, "Danial Hakim", job_title="Finance Executive")
    await employee(w, t0, "Danial Other", job_title="Sales Executive")
    assert await _names(browser, t0, "danial finance") == ["Danial Hakim"]


async def test_search_ranks_name_matches_before_title_matches(sessions: SessionWorld) -> None:
    w = sessions
    t0 = w.tenants[0]
    await activate(w, t0, "hr")
    browser, _ = await staff(w)
    await employee(w, t0, "Aaron Lim", job_title="Sales Manager")
    await employee(w, t0, "Zara Sales")
    await employee(w, t0, "Mia Tan", email="sales.mia@example.test")
    # Name or email matches first (by name), then the title-only match.
    assert await _names(browser, t0, "sales") == ["Mia Tan", "Zara Sales", "Aaron Lim"]


async def test_search_gate_roles_tenant_and_audit(sessions: SessionWorld) -> None:
    w = sessions
    t0, t1 = w.tenants
    browser, user = await staff(w)
    gated = await search(browser, t0, "ali")
    assert gated.status_code == 403 and gated.json() == {"detail": "package_inactive"}
    await activate(w, t0, "hr")
    await employee(w, t0, "Ali Hassan")
    reader, _ = await w.member(role="read_only")
    assert (await search(reader, t0, "ali")).status_code == 403
    assert (await search(browser, t1, "ali")).status_code == 403
    assert (await search(browser, t0, "ali")).status_code == 200
    audit = await w.q(
        "SELECT details FROM console_audit WHERE actor_id=%s AND action='workspace.people.lookup'",
        user,
    )
    assert audit == [{"details": {"candidates": 1}}]


async def test_search_throttle_is_per_session_and_returns_429(sessions: SessionWorld) -> None:
    w = sessions
    t0 = w.tenants[0]
    await activate(w, t0, "hr")
    browser, _ = await staff(w)
    other, _ = await staff(w)
    for _ in range(60):
        assert (await search(browser, t0, "ali")).status_code == 200
    assert (await search(browser, t0, "ali")).status_code == 429
    assert (await search(other, t0, "ali")).status_code == 200


async def test_throttle_window_expires() -> None:
    sid = uuid4()
    for _ in range(60):
        directory.throttle_lookup(sid, now=0.0)
    with pytest.raises(HTTPException) as caught:
        directory.throttle_lookup(sid, now=59.0)
    assert caught.value.status_code == 429
    directory.throttle_lookup(sid, now=61.0)


async def test_me_reasons_and_manager_without_any_hr_grant(sessions: SessionWorld) -> None:
    w = sessions
    t0 = w.tenants[0]
    browser, user = await staff(w)
    assert (await mine(browser, t0)).json() == {
        "employee_id": None,
        "manager": None,
        "reason": "package_inactive",
    }
    await activate(w, t0, "hr")
    r = await mine(browser, t0)
    assert r.status_code == 200 and r.json()["reason"] == "no_employee_link"
    assert r.headers["Cache-Control"] == "no-store"
    me = await employee(w, t0, "Me Staff", user=user)
    r = await mine(browser, t0)
    assert r.json() == {"employee_id": str(me), "manager": None, "reason": "no_manager"}
    boss = await employee(w, t0, "Boss Person", email="boss@example.test")
    await w.q("UPDATE hr_employees SET manager_id=%s WHERE id=%s", boss, me)
    r = await mine(browser, t0)
    assert r.json() == {
        "employee_id": str(me),
        "manager": {
            "employee_id": str(boss),
            "display_name": "Boss Person",
            "job_title": "Analyst",
            "work_email": "boss@example.test",
        },
        "reason": None,
    }
    assert "department" not in r.text and "Secret Dept" not in r.text
    await w.q("UPDATE hr_employees SET is_active=false WHERE id=%s", boss)
    assert (await mine(browser, t0)).json()["reason"] == "no_manager"


async def test_me_never_shows_another_users_manager(sessions: SessionWorld, app_dsn: str) -> None:
    w = sessions
    t0 = w.tenants[0]
    await activate(w, t0, "hr")
    a, user_a = await staff(w)
    _, user_b = await staff(w)
    boss = await employee(w, t0, "Boss Person")
    await employee(w, t0, "Staff A", manager=boss, user=user_a)
    await employee(w, t0, "Staff B", manager=boss, user=user_b)
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT set_config('app.client_id', %s, true)", (t0,))
            await cur.execute("SELECT set_config('app.hr_user_id', %s, true)", (user_a,))
            await cur.execute("SELECT user_id FROM company_directory_me")
            rows: list[Any] = await cur.fetchall()
        async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT set_config('app.client_id', %s, true)", (t0,))
            await cur.execute("SELECT 1 FROM company_directory_me")
            unset = await cur.fetchall()
    assert [str(r["user_id"]) for r in rows] == [user_a]
    assert unset == []
    assert (await mine(a, t0)).json()["manager"]["display_name"] == "Boss Person"


async def test_me_read_only_denied(sessions: SessionWorld) -> None:
    w = sessions
    await activate(w, w.tenants[0], "hr")
    reader, _ = await w.member(role="read_only")
    assert (await mine(reader, w.tenants[0])).status_code == 403


async def test_platform_admin_reads_the_directory_of_the_named_tenant(
    sessions: SessionWorld,
) -> None:
    """Platform admins get the dock on tenant pages, so the tenant comes from ?client_id=."""
    w = sessions
    t0, t1 = w.tenants
    await activate(w, t0, "hr")
    await employee(w, t0, "Ali Hassan", email="ali.h@example.test")
    await employee(w, t1, "Ali Other Tenant", email="ali@other.test")
    found = await search(w.admin, t0, "ali")
    assert found.status_code == 200, found.text
    assert [p["display_name"] for p in found.json()] == ["Ali Hassan"]
    me = await mine(w.admin, t0)
    assert me.status_code == 200, me.text
    assert me.json() == {"employee_id": None, "manager": None, "reason": "no_employee_link"}
    unnamed = await w.admin.get(BASE, params={"q": "ali"})
    assert unnamed.status_code == 400
    assert (await w.admin.get(f"{BASE}/me")).status_code == 400
