"""The create-defaults action (HR PR 4): `POST /console/v1/hr/templates/defaults` creates the
Malaysian default onboarding and offboarding templates (`csa.hr.default_templates`) once, behind
the `HR_DEFAULT_TEMPLATES_ENABLED` switch, through the real HR router (disposable database).

Review focus 3 and 4 of the plan: two clicks, a double submit, a retried request or a company that
already has a template of that kind never create a second default (it would tie and raise
conflicts); a failure halfway leaves neither template; with the switch off the route refuses and
the list says the action is not available. Reuses the HrWorld fixture of
tests/test_hr_persistence.py (owner is the HR owner, operator gets Manage people, reader is a
read-only member, admin a tenant admin with no HR authority). All data is synthetic.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.hr import processes as process_service
from csa.hr.default_templates import DEFAULT_TEMPLATES
from csa.hr.policy import HrPolicyError
from csa.hr.process_models import TemplateCreate, TemplateStep

from tests.conftest import requires_docker
from tests.test_hr_people import make_people_manager
from tests.test_hr_persistence import ORIGIN, HrWorld, hr_database, world
from tests.test_hr_process_api import (
    TEMPLATES,
    get_ok,
    post_template,
    problem,
    without_csrf,
    without_key,
)
from tests.test_hr_process_templates import step

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]

DEFAULTS = f"{TEMPLATES}/defaults"
KINDS = ("onboarding", "offboarding")


def switch(world: HrWorld, on: bool) -> None:
    world.app.state.hr_default_templates_enabled = on


async def state(world: HrWorld) -> dict[str, int]:
    """What the action may write: templates, receipts and audit rows of this company."""
    found = {}
    for name, statement in (
        ("templates", "SELECT count(*) AS n FROM hr_process_templates WHERE client_id=%s"),
        ("steps", "SELECT count(*) AS n FROM hr_process_template_steps WHERE client_id=%s"),
        ("operations", "SELECT count(*) AS n FROM hr_operations WHERE client_id=%s"),
        (
            "audit",
            "SELECT count(*) AS n FROM console_audit WHERE client_id=%s "
            "AND action='hr.template_created'",
        ),
    ):
        rows = await world.q(statement, (world.tenant,))  # type: ignore[arg-type]
        found[name] = rows[0]["n"]
    return found


async def create(
    world: HrWorld, who: str = "owner", *, key: UUID | None = None, body: Any = None
) -> dict[str, Any]:
    response = await world.call(who, "POST", DEFAULTS, body if body is not None else {}, key=key)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def created_kinds(receipt: dict[str, Any]) -> list[str]:
    return [item["kind"] for item in receipt["created"]]


# ---- The switch -------------------------------------------------------------------------------


async def test_switch_off_refuses_the_route_and_the_list_says_unavailable(world: HrWorld) -> None:
    before = await state(world)
    listed = await get_ok(world, "owner", TEMPLATES)
    assert listed["defaults"] == {"available": False, "created": False}
    for who in ("owner", "staff", "admin"):
        problem(await world.call(who, "POST", DEFAULTS, {}), 404, "resource_unavailable")
    # Not discoverable: the refusal comes before the session, CSRF and key checks.
    problem(await without_csrf(world, "owner", "POST", DEFAULTS, {}), 404, "resource_unavailable")
    problem(await without_key(world, "owner", "POST", DEFAULTS, {}), 404, "resource_unavailable")
    switch(world, False)
    problem(await world.call("owner", "POST", DEFAULTS, {}), 404, "resource_unavailable")
    assert await state(world) == before


# ---- Run once ---------------------------------------------------------------------------------


async def test_switch_on_creates_both_templates_once(world: HrWorld) -> None:
    switch(world, True)
    await make_people_manager(world)
    listed = await get_ok(world, "operator", TEMPLATES)
    assert listed["defaults"] == {"available": True, "created": False}
    before = await state(world)
    key = uuid4()
    first = await create(world, "operator", key=key)
    assert created_kinds(first) == list(KINDS)
    assert first["skipped"] == []
    assert first["duplicate"] is False
    for item, template in zip(first["created"], DEFAULT_TEMPLATES, strict=True):
        assert set(item) == {"kind", "template_id", "version", "name"}
        assert (item["kind"], item["name"], item["version"]) == (template.kind, template.name, 1)
    after = await state(world)
    steps = sum(len(t.steps) for t in DEFAULT_TEMPLATES)
    # Two templates, two audit rows, and three receipts: one per template and the action's own.
    assert after == {
        "templates": before["templates"] + 2,
        "steps": before["steps"] + steps,
        "operations": before["operations"] + 3,
        "audit": before["audit"] + 2,
    }
    audit = await world.q(
        "SELECT actor_id, target_id, details FROM console_audit WHERE client_id=%s "
        "AND action='hr.template_created'",
        (world.tenant,),
    )
    assert sorted(row["details"]["kind"] for row in audit) == sorted(KINDS)
    assert {row["target_id"] for row in audit} == {i["template_id"] for i in first["created"]}
    assert {row["actor_id"] for row in audit} == {world.users["operator"]}

    # Readable through the list and the template read, equal to the content file; English only and
    # the company default (no rule), with no tie.
    listed = await get_ok(world, "operator", TEMPLATES)
    assert listed["defaults"] == {"available": True, "created": True}
    by_kind = {item["kind"]: item for item in listed["items"]}
    for template in DEFAULT_TEMPLATES:
        summary = by_kind[template.kind]
        assert (summary["name"], summary["step_count"], summary["is_active"]) == (
            template.name,
            len(template.steps),
            True,
        )
        assert set(summary["rules"].values()) == {None}
        detail = await get_ok(world, "operator", f"{TEMPLATES}/{summary['template_id']}")
        assert detail["conflicts"] == []
        assert [s["title_en"] for s in detail["steps"]] == [s.title_en for s in template.steps]
        assert {s["title_ms"] for s in detail["steps"]} == {None}
        assert {s["description_ms"] for s in detail["steps"]} == {None}

    # A second click (a new key) skips both and writes only its own receipt.
    again = await create(world, "owner")
    assert again == {
        "created": [],
        "skipped": [{"kind": kind, "reason": "already_present"} for kind in KINDS],
        "duplicate": False,
    }
    assert await state(world) == {**after, "operations": after["operations"] + 1}

    # A retry with the first key returns the first result, marked duplicate, and writes nothing.
    retried = await create(world, "operator", key=key)
    assert retried == {**first, "duplicate": True}
    assert await state(world) == {**after, "operations": after["operations"] + 1}
    # The key is never reused for another change: a template create under it is refused.
    body = DEFAULT_TEMPLATES[0].model_dump(mode="json")
    problem(
        await world.call("operator", "POST", TEMPLATES, body, key=key),
        409,
        "idempotency_conflict",
    )


async def test_a_kind_with_an_active_template_is_skipped(world: HrWorld) -> None:
    switch(world, True)
    mine = await post_template(world, kind="onboarding", name="Synthetic onboarding")
    # An inactive offboarding template does not count: the default is still created.
    old = await post_template(
        world, kind="offboarding", name="Synthetic old offboarding", steps=[step(anchor="last_day")]
    )
    response = await world.call(
        "owner", "POST", f"{TEMPLATES}/{old['template_id']}/deactivate", {"expected_version": 1}
    )
    assert response.status_code == 200, response.text
    listed = await get_ok(world, "owner", TEMPLATES)
    assert listed["defaults"] == {"available": True, "created": False}
    before = await state(world)
    receipt = await create(world)
    assert created_kinds(receipt) == ["offboarding"]
    assert receipt["skipped"] == [{"kind": "onboarding", "reason": "already_present"}]
    after = await state(world)
    assert after["templates"] == before["templates"] + 1
    assert after["audit"] == before["audit"] + 1
    listed = await get_ok(world, "owner", TEMPLATES)
    assert listed["defaults"] == {"available": True, "created": True}
    onboarding = [item for item in listed["items"] if item["kind"] == "onboarding"]
    assert [item["template_id"] for item in onboarding] == [mine["template_id"]]


# ---- Both or none -----------------------------------------------------------------------------


def _bad_offboarding() -> TemplateCreate:
    """A valid shape the database refuses: the step names a person who is not in the company."""
    good = DEFAULT_TEMPLATES[1]
    first = good.steps[0].model_dump()
    first |= {"owner_role": "person", "owner_employee_id": uuid4()}
    return good.model_copy(update={"steps": [TemplateStep.model_validate(first), *good.steps[1:]]})


async def test_a_refusal_on_the_second_template_creates_neither(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    switch(world, True)
    broken = (DEFAULT_TEMPLATES[0], _bad_offboarding())
    monkeypatch.setattr(process_service, "DEFAULT_TEMPLATES", broken)
    before = await state(world)
    key = uuid4()
    refused = await world.call("owner", "POST", DEFAULTS, {}, key=key)
    problem(refused, 409, "template_step_person_unavailable")
    assert await state(world) == before
    listed = await get_ok(world, "owner", TEMPLATES)
    assert listed["items"] == []
    assert listed["defaults"]["created"] is False

    # Nothing was kept under the key either: the same key, once the content is right, creates both.
    monkeypatch.setattr(process_service, "DEFAULT_TEMPLATES", DEFAULT_TEMPLATES)
    assert created_kinds(await create(world, key=key)) == list(KINDS)


async def test_a_database_error_on_the_second_template_creates_neither(
    world: HrWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    switch(world, True)
    real = process_service.HrProcesses._call
    applied: list[str] = []

    async def failing(conn: Any, path: str, params: tuple[Any, ...]) -> Any:
        if path == "hr/process_template_apply.sql":
            applied.append(path)
            if len(applied) == 2:
                raise psycopg.errors.QueryCanceled("synthetic: canceling statement")
        return await real(conn, path, params)

    monkeypatch.setattr(process_service.HrProcesses, "_call", staticmethod(failing))
    before = await state(world)
    problem(await world.call("owner", "POST", DEFAULTS, {}), 503, "hr_database_unavailable")
    assert len(applied) == 2
    assert await state(world) == before


# ---- Concurrency ------------------------------------------------------------------------------


async def test_two_concurrent_requests_create_one_pair(world: HrWorld) -> None:
    """Two tabs clicking at once, each with its own key: the tenant's hr-operations lock makes the
    second wait for the first to commit, then it finds both kinds present.
    """
    switch(world, True)
    await make_people_manager(world)
    first, second = await asyncio.gather(
        world.call("owner", "POST", DEFAULTS, {}), world.call("operator", "POST", DEFAULTS, {})
    )
    assert (first.status_code, second.status_code) == (201, 201), (first.text, second.text)
    receipts = sorted((first.json(), second.json()), key=lambda r: len(r["created"]))
    assert receipts[0]["created"] == [] and len(receipts[0]["skipped"]) == 2
    assert created_kinds(receipts[1]) == list(KINDS)
    assert (await state(world))["templates"] == 2


async def test_a_double_submit_with_one_key_creates_one_pair(world: HrWorld) -> None:
    switch(world, True)
    key = uuid4()
    first, second = await asyncio.gather(
        world.call("owner", "POST", DEFAULTS, {}, key=key),
        world.call("owner", "POST", DEFAULTS, {}, key=key),
    )
    assert (first.status_code, second.status_code) == (201, 201), (first.text, second.text)
    assert sorted((first.json()["duplicate"], second.json()["duplicate"])) == [False, True]
    assert {**first.json(), "duplicate": None} == {**second.json(), "duplicate": None}
    assert (await state(world))["templates"] == 2


# ---- Who may do it ----------------------------------------------------------------------------


async def test_only_a_signed_in_hr_team_member_may_create_them(world: HrWorld) -> None:
    switch(world, True)
    before = await state(world)
    for who in ("staff", "manager", "outsider", "admin", "operator"):
        problem(await world.call(who, "POST", DEFAULTS, {}), 404, "resource_unavailable")
        problem(await world.call(who, "GET", TEMPLATES), 404, "resource_unavailable")
    problem(await world.call("reader", "POST", DEFAULTS, {}), 403, "read_only_session")
    problem(await without_csrf(world, "owner", "POST", DEFAULTS, {}), 403, "invalid csrf token")
    wrong = await world.call("owner", "POST", DEFAULTS, {}, headers={"X-CSRF-Token": "synthetic"})
    problem(wrong, 403, "invalid csrf token")
    no_key = await without_key(world, "owner", "POST", DEFAULTS, {})
    assert (no_key.status_code, no_key.json()["detail"]) == (422, "invalid_hr_input")
    # An assistant or capability session, and a proof without CSRF, never reach the database.
    service = process_service.HrProcesses(world.service)
    proof = world.proof("owner")
    for session in (
        proof.model_copy(update={"assistant": True}),
        proof.model_copy(update={"capability": "hr_assistant"}),
        proof.model_copy(update={"csrf_hash": None}),
    ):
        with pytest.raises(HrPolicyError) as refused:
            await service.create_default_templates(session, uuid4())
        assert refused.value.code == "human_operation_required"
    assert await state(world) == before
    # A Manage people holder may.
    await make_people_manager(world)
    assert created_kinds(await create(world, "operator")) == list(KINDS)


async def test_the_body_is_empty_and_capped_at_the_hr_default(world: HrWorld) -> None:
    switch(world, True)
    before = await state(world)
    for body in ({"kind": "onboarding"}, {"templates": []}, [], "x"):
        response = await world.call("owner", "POST", DEFAULTS, body)
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_hr_input")
    # The default HR cap of 8192 bytes: one byte over is refused before it is parsed.
    await world.call("owner", "GET", f"{TEMPLATES}")
    browser = world.browsers["owner"]
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": world.csrf["owner"],
        "Idempotency-Key": str(uuid4()),
        "Content-Type": "application/json",
    }
    filler = '{"x":"' + "a" * (8192 - 8) + '"}'
    assert len(filler) == 8192
    at_cap = await browser.post(DEFAULTS, content=filler, headers=headers)
    assert (at_cap.status_code, at_cap.json()["detail"]) == (422, "invalid_hr_input")
    over = await browser.post(DEFAULTS, content=filler + " ", headers=headers)
    problem(over, 413, "hr_request_too_large")
    assert await state(world) == before
    # No body at all is the same as an empty object.
    headers["Idempotency-Key"] = str(uuid4())
    response = await browser.post(DEFAULTS, headers=headers)
    assert response.status_code == 201, response.text
    assert created_kinds(response.json()) == list(KINDS)
