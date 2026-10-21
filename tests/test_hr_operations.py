"""Synthetic real-role acceptance for the remaining local HR operations."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.decide.groq import GroqClient
from csa.hr.assistant import HrAssistant
from psycopg.conninfo import make_conninfo
from scripts.run_hr_maintenance import run_once

from tests.conftest import requires_docker
from tests.test_hr_persistence import ATTENDANCE, HrWorld, hr_database, workflow_grant, world

__all__ = ["hr_database", "world"]
pytestmark = [requires_docker, pytest.mark.asyncio]


async def test_staff_chat_uses_only_typed_actions_and_replays_without_another_model_call(
    world: HrWorld,
):
    import asyncio

    for name in ("hr_assistant", "hr_training"):
        assert (
            await world.call(
                "owner", "PUT", "/console/v1/hr/agents", {"name": name, "enabled": True}
            )
        ).status_code == 200
    calls = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"action":{"action":"training.prepare","content":'
                                '{"title":"Synthetic workshop","learning_objective":"Practice"}}}'
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        world.app.state.hr_assistant = HrAssistant(
            world.service, GroqClient("synthetic-provider-key", "synthetic-model", client)
        )
        key = uuid4()
        responses = await asyncio.gather(
            *[
                world.call(
                    "staff",
                    "POST",
                    "/console/v1/hr/assistant",
                    {"message": "Prepare a training workshop"},
                    key=key,
                )
                for _ in range(2)
            ]
        )
        assert all(response.status_code == 200 for response in responses), [
            r.text for r in responses
        ]
        assert len(calls) == 1
        assert responses[0].json()["request"] == responses[1].json()["request"]
        assert (
            await world.q(
                "SELECT count(*) AS n FROM hr_training_requests WHERE client_id=%s", (world.tenant,)
            )
        )[0]["n"] == 1
        await world.call(
            "owner", "PUT", "/console/v1/hr/agents", {"name": "hr_assistant", "enabled": False}
        )
        denied = await world.call(
            "staff",
            "POST",
            "/console/v1/hr/assistant",
            {"message": "Prepare a training workshop"},
            key=key,
        )
        assert denied.json()["detail"] == "agent_disabled"
        assert len(calls) == 1


async def test_model_cannot_turn_chat_into_human_approval_or_authority_provisioning(world: HrWorld):
    submitted = await world.submit()
    for name in ("hr_assistant", "hr_training"):
        await world.call("owner", "PUT", "/console/v1/hr/agents", {"name": name, "enabled": True})
    import json

    response_json = {
        "action": {
            "action": "training.transition",
            "request_id": submitted["request_id"],
            "command": {"action": "approve", "expected_version": 1},
        }
    }

    def provider(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(response_json)}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        world.app.state.hr_assistant = HrAssistant(
            world.service, GroqClient("synthetic", "synthetic", client)
        )
        denied = await world.call(
            "manager", "POST", "/console/v1/hr/assistant", {"message": "Approve that training"}
        )
        assert denied.status_code == 503 and denied.json()["detail"] == "hr_model_unavailable", (
            denied.text
        )
    row = await world.q(
        "SELECT status FROM hr_training_requests WHERE client_id=%s", (world.tenant,)
    )
    assert row == [{"status": "submitted"}]
    assert "Synthetic" not in denied.text


async def test_retention_redacts_private_content_and_revoked_grant_denies_replay(world: HrWorld):
    await private_grant(world, "operator", "recruitment")
    body = {
        "operation": "candidate.intake",
        "display_name": "Synthetic private applicant",
        "cv_text": "Synthetic private CV",
        "consent_evidence": "Synthetic consent",
        "retain_until": (world.service.now() + timedelta(hours=1)).isoformat(),
    }
    key = uuid4()
    saved = await operation(world, "operator", body, key=key)
    candidate = UUID(saved.json()["resource_id"])
    await world.q(
        "UPDATE hr_candidates SET retain_until=now()-interval '1 second' "
        "WHERE client_id=%s AND id=%s",
        (world.tenant, candidate),
    )
    expired = await world.call("operator", "GET", f"/console/v1/hr/private/candidate/{candidate}")
    assert expired.status_code == 404
    maintained = await operation(world, "operator", {"operation": "automation.run"})
    assert maintained.status_code == 200, maintained.text
    row = (
        await world.q(
            "SELECT cv_text,erased_at FROM hr_candidates WHERE client_id=%s AND id=%s",
            (world.tenant, candidate),
        )
    )[0]
    assert row["cv_text"] is None and row["erased_at"] is not None
    revoked = await operation(
        world,
        "owner",
        {
            "operation": "private_access.save",
            "user_id": str(world.users["operator"]),
            "area": "recruitment",
            "enabled": False,
        },
    )
    assert revoked.status_code == 200
    assert (await operation(world, "operator", body, key=key)).status_code == 404
    receipts = await world.q(
        "SELECT receipt FROM hr_operations WHERE client_id=%s", (world.tenant,)
    )
    assert "Synthetic private" not in str(receipts)


async def test_worker_is_opt_in_deduplicates_reminders_and_cannot_approve(world: HrWorld):
    previous = world.service.now
    world.service.now = lambda: previous() - timedelta(days=2)
    try:
        await world.submit()
    finally:
        world.service.now = previous
    await world.q("ALTER ROLE csa_hr_maintenance LOGIN PASSWORD 'synthetic_hr_worker'")
    dsn = make_conninfo(world.dsn, user="csa_hr_maintenance", password="synthetic_hr_worker")
    await operation(
        world,
        "owner",
        {"operation": "automation.configure", "enabled": True, "remind_after_hours": 1},
    )
    first = await run_once(dsn)
    assert sum(result.notification_count for result in first) == 2
    second = await run_once(dsn)
    assert sum(result.notification_count for result in second) == 0
    assert (
        await world.q("SELECT status FROM hr_training_requests WHERE client_id=%s", (world.tenant,))
    ) == [{"status": "submitted"}]
    await operation(world, "owner", {"operation": "automation.configure", "enabled": False})


async def test_system_worker_has_only_maintenance_functions(world: HrWorld):
    await world.q("ALTER ROLE csa_hr_maintenance LOGIN PASSWORD 'synthetic_hr_worker'")
    dsn = make_conninfo(world.dsn, user="csa_hr_maintenance", password="synthetic_hr_worker")
    assert await run_once(dsn) == []
    configured = await operation(
        world,
        "owner",
        {"operation": "automation.configure", "enabled": True, "remind_after_hours": 1},
    )
    assert configured.status_code == 200, configured.text
    assert (
        await operation(world, "admin", {"operation": "automation.configure", "enabled": True})
    ).status_code == 404
    first = await run_once(dsn)
    assert first and all(result.status == "completed" for result in first)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        for statement in (
            "SELECT * FROM hr_candidates",
            "SELECT * FROM hr_payroll_snapshots",
            "UPDATE hr_cases SET status='approved'",
            "SELECT * FROM console_sessions",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(statement)  # type: ignore[arg-type]
    async with world.service.pool.connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT * FROM hr_maintain_due(NULL)")
    await operation(world, "owner", {"operation": "automation.configure", "enabled": False})
    assert await run_once(dsn) == []


async def test_local_interview_agent_and_cancel_keep_cv_out_of_results(world: HrWorld):
    await private_grant(world, "operator", "recruitment")
    await world.call(
        "owner", "PUT", "/console/v1/hr/agents", {"name": "hr_recruitment", "enabled": True}
    )
    intake = await operation(
        world,
        "operator",
        {
            "operation": "candidate.intake",
            "display_name": "Synthetic candidate",
            "cv_text": "Synthetic private CV",
            "consent_evidence": "Synthetic consent",
            "retain_until": (world.service.now() + timedelta(days=10)).isoformat(),
        },
    )
    stamp = world.service.now() + timedelta(days=1)
    candidate = intake.json()["resource_id"]
    schedule = {
        "operation": "interview.schedule",
        "candidate_id": candidate,
        "interviewer_id": str(world.users["manager"]),
        "starts_at": stamp.isoformat(),
        "ends_at": (stamp + timedelta(hours=1)).isoformat(),
        "timezone": "Asia/Kuching",
    }
    arranged = await world.call(
        "operator",
        "POST",
        "/console/v1/hr/agents/execute",
        {"action": "interview.schedule", "content": schedule},
    )
    assert arranged.status_code == 200, arranged.text
    assert arranged.json()["status"] == "scheduled_locally"
    assert "Synthetic private" not in arranged.text
    mine = await world.call("manager", "GET", "/console/v1/hr/records/my_interviews")
    assert mine.status_code == 200 and len(mine.json()["items"]) == 1, mine.text
    assert (
        await world.call("manager", "GET", f"/console/v1/hr/private/candidate/{candidate}")
    ).status_code == 404
    cancelled = await operation(
        world,
        "operator",
        {
            "operation": "interview.cancel",
            "schedule_id": arranged.json()["resource_id"],
            "reason": "Human reschedule",
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    assert (await world.call("manager", "GET", "/console/v1/hr/records/my_interviews")).json()[
        "items"
    ] == []


async def test_cross_year_leave_consumes_each_reviewed_year_atomically(world: HrWorld):
    await workflow_grant(world, "staff", "leave", "prepare", "self")
    await workflow_grant(world, "manager", "leave", "decide", "assigned_manager")
    for year in (2026, 2027):
        assert (
            await operation(
                world,
                "owner",
                {
                    "operation": "calendar.save",
                    "region": "sarawak",
                    "year": year,
                    "working_weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "holidays": [],
                },
            )
        ).status_code == 200
        assert (
            await operation(
                world,
                "owner",
                {
                    "operation": "leave_balance.save",
                    "employee_id": str(world.employees["staff"]),
                    "year": year,
                    "leave_type": "annual",
                    "days": "1.00",
                    "policy_reference": "Synthetic policy",
                },
            )
        ).status_code == 200
    saved = await world.call(
        "staff",
        "POST",
        "/console/v1/hr/workflows",
        {
            "domain": "leave",
            "region": "sarawak",
            "starts_on": "2026-12-31",
            "ends_on": "2027-01-01",
            "leave_type": "annual",
            "reason": "Synthetic cross-year leave",
        },
    )
    approved = await world.call(
        "manager",
        "POST",
        f"/console/v1/hr/workflows/{saved.json()['id']}/transitions",
        {"action": "approve", "expected_version": 0},
    )
    assert approved.status_code == 200, approved.text
    usages = await world.q(
        "SELECT year,days FROM hr_leave_usage WHERE client_id=%s ORDER BY year", (world.tenant,)
    )
    assert [row["year"] for row in usages] == [2026, 2027]
    assert all(str(row["days"]) == "1.00" for row in usages)


async def operation(w: HrWorld, who: str, body: dict, *, key: UUID | None = None):
    return await w.call(who, "POST", "/console/v1/hr/operations", body, key=key)


async def private_grant(w: HrWorld, who: str, area: str):
    result = await operation(
        w,
        "owner",
        {
            "operation": "private_access.save",
            "user_id": str(w.users[who]),
            "area": area,
            "enabled": True,
        },
    )
    assert result.status_code == 200, result.text


async def test_owner_can_onboard_link_and_set_explicit_grants_without_console_roles(world: HrWorld):
    employee = uuid4()
    body = {
        "operation": "employee.save",
        "employee_id": str(employee),
        "values": {
            "display_name": "Synthetic recruit",
            "assigned_manager": {
                "tenant_id": str(world.tenant),
                "employee_id": str(world.employees["manager"]),
            },
        },
        "account_id": str(world.users["operator"]),
        "region": "sabah",
        "legal_entity_reference": "Synthetic Entity",
        "employment_category": "Synthetic adults",
    }
    assert (await operation(world, "admin", body)).status_code == 404
    key = uuid4()
    response = await operation(world, "owner", body, key=key)
    assert response.status_code == 200, response.text
    replay = await operation(world, "owner", body, key=key)
    assert replay.status_code == 200 and replay.json()["duplicate"]
    assert (await operation(world, "owner", {**body, "expected_version": 99})).status_code == 409
    access = {
        "operation": "access.save",
        "user_id": str(world.users["operator"]),
        "grants": [{"action": "directory.read", "scope": "self", "fields": ["display_name"]}],
        "workflows": [],
    }
    saved = await operation(world, "owner", access)
    assert saved.status_code == 200, saved.text
    response = await world.call("operator", "GET", f"/console/v1/hr/employees/{employee}")
    assert response.status_code == 200, response.text
    assert set(response.json()["values"]) == {"display_name"}
    assert (await world.call("admin", "GET", "/console/v1/hr/records/employees")).status_code == 404


async def test_candidate_consent_notes_scheduling_and_erasure_are_private_and_atomic(
    world: HrWorld,
):
    await private_grant(world, "operator", "recruitment")
    intake = {
        "operation": "candidate.intake",
        "display_name": "Synthetic Applicant",
        "cv_text": "Synthetic CV",
        "consent_evidence": "Synthetic signed consent reference",
        "retain_until": (world.service.now() + timedelta(days=30)).isoformat(),
    }
    assert (await operation(world, "admin", intake)).status_code == 404
    response = await operation(world, "operator", intake)
    assert response.status_code == 200, response.text
    candidate = response.json()["resource_id"]
    assert "Synthetic CV" not in response.text
    private = await world.call("operator", "GET", f"/console/v1/hr/private/candidate/{candidate}")
    assert private.status_code == 200, private.text
    assert private.json()["items"][0]["cv_text"] == "Synthetic CV"
    assert (
        await world.call("owner", "GET", f"/console/v1/hr/private/candidate/{candidate}")
    ).status_code == 404
    note = await operation(
        world,
        "operator",
        {
            "operation": "candidate.note",
            "candidate_id": candidate,
            "expected_version": 0,
            "note": "Human reviewed job evidence",
        },
    )
    assert note.status_code == 200, note.text
    stamp = world.service.now() + timedelta(days=1)
    interview = {
        "operation": "interview.schedule",
        "candidate_id": candidate,
        "interviewer_id": str(world.users["manager"]),
        "starts_at": stamp.isoformat(),
        "ends_at": (stamp + timedelta(hours=1)).isoformat(),
        "timezone": "Asia/Kuching",
    }
    scheduled = await operation(world, "operator", interview)
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["status"] == "scheduled_locally"
    assert (await operation(world, "operator", interview)).json()["detail"] == "interview_overlap"
    erased = await operation(
        world,
        "operator",
        {"operation": "candidate.erase", "candidate_id": candidate, "expected_version": 1},
    )
    assert erased.status_code == 200, erased.text
    assert (
        await world.call("operator", "GET", f"/console/v1/hr/private/candidate/{candidate}")
    ).status_code == 404
    row = (
        await world.q(
            "SELECT cv_text,consent_evidence,notes FROM hr_candidates WHERE client_id=%s AND id=%s",
            (world.tenant, UUID(candidate)),
        )
    )[0]
    assert row == {"cv_text": None, "consent_evidence": None, "notes": []}
    interviews = await world.call("operator", "GET", "/console/v1/hr/records/interviews")
    assert interviews.json()["items"][0]["status"] == "cancelled"


async def test_import_batch_rolls_back_all_rows_and_never_approves_attendance(world: HrWorld):
    await private_grant(world, "operator", "attendance")
    body = {
        "operation": "attendance.import",
        "employee_id": str(world.employees["staff"]),
        "records": [ATTENDANCE, {**ATTENDANCE, "correction_of": str(uuid4())}],
    }
    failed = await operation(world, "operator", body)
    assert failed.status_code == 404, failed.text
    assert (
        await world.q("SELECT count(*) AS n FROM hr_cases WHERE client_id=%s", (world.tenant,))
    )[0]["n"] == 0
    key = uuid4()
    good = await operation(world, "operator", {**body, "records": [ATTENDANCE]}, key=key)
    assert good.status_code == 200, good.text
    replay = await operation(world, "operator", {**body, "records": [ATTENDANCE]}, key=key)
    assert replay.json()["duplicate"]
    rows = await world.q("SELECT status FROM hr_cases WHERE client_id=%s", (world.tenant,))
    assert rows == [{"status": "pending"}]


async def test_reviewed_calendar_leave_balance_and_overlap_gate(world: HrWorld):
    for who, action, scope in [
        ("staff", "prepare", "self"),
        ("manager", "decide", "assigned_manager"),
    ]:
        await workflow_grant(world, who, "leave", action, scope)
    cal = await operation(
        world,
        "owner",
        {
            "operation": "calendar.save",
            "region": "sabah",
            "year": 2026,
            "working_weekdays": [0, 1, 2, 3, 4],
            "holidays": ["2026-11-02"],
        },
    )
    assert cal.status_code == 200, cal.text
    balance = await operation(
        world,
        "owner",
        {
            "operation": "leave_balance.save",
            "employee_id": str(world.employees["staff"]),
            "year": 2026,
            "leave_type": "annual",
            "days": "2.00",
            "policy_reference": "Synthetic reviewed policy",
        },
    )
    assert balance.status_code == 200, balance.text
    content = {
        "domain": "leave",
        "region": "sabah",
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-04",
        "leave_type": "annual",
        "reason": "Synthetic",
    }
    saved = await world.call("staff", "POST", "/console/v1/hr/workflows", content)
    approved = await world.call(
        "manager",
        "POST",
        f"/console/v1/hr/workflows/{saved.json()['id']}/transitions",
        {"action": "approve", "expected_version": 0},
    )
    assert approved.status_code == 200, approved.text
    usage = (await world.q("SELECT days FROM hr_leave_usage WHERE client_id=%s", (world.tenant,)))[
        0
    ]
    assert str(usage["days"]) == "2.00"
    saved2 = await world.call(
        "staff",
        "POST",
        "/console/v1/hr/workflows",
        {**content, "starts_on": "2026-11-05", "ends_on": "2026-11-05"},
    )
    denied = await world.call(
        "manager",
        "POST",
        f"/console/v1/hr/workflows/{saved2.json()['id']}/transitions",
        {"action": "approve", "expected_version": 0},
    )
    assert denied.json()["detail"] == "insufficient_reviewed_leave_balance"


async def test_payroll_exact_snapshot_distinct_review_and_reversal_preserve_history(world: HrWorld):
    for who, action, scope in [
        ("operator", "prepare", "payroll_operator"),
        ("operator", "read", "payroll_operator"),
        ("owner", "decide", "payroll_reviewer"),
    ]:
        await workflow_grant(world, who, "payroll", action, scope)
    await private_grant(world, "operator", "payroll")
    await private_grant(world, "owner", "payroll")
    rule_id = uuid4()
    band = [{"above": "0.00", "through": "2000.00", "employee": "10.00", "employer": "20.00"}]
    rules = {
        "operation": "payroll.rules",
        "rule_id": str(rule_id),
        "region": "sabah",
        "legal_entity_reference": "Synthetic Entity",
        "employment_category": "Synthetic category",
        "cycle": "monthly",
        "effective_from": "2026-01-01",
        "effective_until": "2026-12-31",
        "source_reference": "Synthetic reviewed table, not statutory rates",
        "reviewer_qualification_reference": "Synthetic reviewer",
        "epf": band,
        "socso": band,
        "eis": band,
    }
    registered = await operation(world, "owner", rules)
    assert registered.status_code == 200, registered.text
    await world.q(
        "UPDATE hr_employees SET region='sabah',legal_entity_reference='Synthetic Entity',"
        "employment_category='Synthetic category' WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["staff"]),
    )
    period = {
        "domain": "payroll",
        "region": "sabah",
        "legal_entity_reference": "Synthetic Entity",
        "cycle": "monthly",
        "starts_on": "2026-10-01",
        "ends_on": "2026-10-31",
        "rule_set_reference": str(rule_id),
    }
    prepared = await world.call("operator", "POST", "/console/v1/hr/workflows", period)
    case = prepared.json()["id"]
    snapshot = await operation(
        world,
        "operator",
        {
            "operation": "payroll.snapshot",
            "case_id": case,
            "lines": [
                {
                    "employee_id": str(world.employees["staff"]),
                    "gross": "1000.00",
                    "epf_wage": "1000.00",
                    "socso_wage": "1000.00",
                    "eis_wage": "1000.00",
                    "reviewed_pcb": "5.00",
                    "reviewed_other_deductions": "0.00",
                    "tax_review_reference": "Synthetic external tax review",
                }
            ],
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    details = await world.call("owner", "GET", f"/console/v1/hr/private/payroll_snapshot/{case}")
    assert details.status_code == 200, details.text
    record = details.json()["items"][0]
    assert record["payload"]["net_total"] == "965.00"
    path = f"/console/v1/hr/workflows/{case}/transitions"
    bad = await world.call(
        "owner",
        "POST",
        path,
        {"action": "approve", "expected_version": 0, "payroll_digest": "0" * 64},
    )
    assert bad.status_code == 409, bad.text
    command = {"action": "approve", "expected_version": 0, "payroll_digest": record["digest"]}
    key = uuid4()
    approved = await world.call("owner", "POST", path, command, key=key)
    assert approved.status_code == 200, approved.text
    assert (await world.call("owner", "POST", path, command, key=key)).status_code == 200
    reversed_run = await operation(
        world,
        "owner",
        {
            "operation": "payroll.reverse",
            "case_id": case,
            "expected_version": 1,
            "reason": "Synthetic correction",
        },
    )
    assert reversed_run.status_code == 200, reversed_run.text
    assert (
        await world.q(
            "SELECT status FROM hr_cases WHERE client_id=%s AND id=%s", (world.tenant, UUID(case))
        )
    )[0]["status"] == "approved"
    assert (
        await world.q(
            "SELECT status FROM hr_payroll_snapshots WHERE client_id=%s AND case_id=%s",
            (world.tenant, UUID(case)),
        )
    )[0]["status"] == "reversed"
