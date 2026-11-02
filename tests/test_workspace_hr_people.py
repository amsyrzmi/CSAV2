"""Assistant actions for People: add, update, mark as leaving, who reports to, open a profile.

The planner is scripted (as in tests/test_workspace_planner.py); every name, permission and
version is resolved and checked by the server against People as the caller sees it.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from csa.core import crypto

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_workspace_planner import (  # noqa: F401 - fixture import
    PROPOSALS,
    _proposal_count,
    _thread,
    _turn,
    planned,  # noqa: F811
)
from tests.test_workspace_registry import proposals_app  # noqa: F401 - fixture import

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

NOT_PERMITTED = (
    "Adding, changing or marking people as leaving needs the HR owner or the Manage people "
    "switch. Ask your HR owner."
)
LIST_NOT_PERMITTED = "Your access doesn't include the People list."


async def _hr_on(w: SessionWorld) -> str:
    tenant = w.tenants[0]
    await activate(w, tenant, "hr")
    await w.q(
        "UPDATE hr_agent_settings SET enabled=true WHERE client_id=%s AND name='hr_assistant'",
        tenant,
    )
    return tenant


async def _owner(w: SessionWorld) -> httpx.AsyncClient:
    """A tenant admin who is the tenant's HR owner, with the HR assistant switch on."""
    tenant = await _hr_on(w)
    browser, me = await w.member(role="tenant_admin")
    user = me["user"]["id"]
    await w.q(
        "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
        tenant, user, user,
    )  # fmt: skip
    return browser


async def _person(
    w: SessionWorld,
    name: str,
    *,
    title: str | None = None,
    manager: UUID | None = None,
    department: str | None = None,
) -> UUID:
    eid = uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,job_title,manager_id,department)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        w.tenants[0], eid, name, title, manager, department,
    )  # fmt: skip
    return eid


async def _employee(w: SessionWorld, eid: UUID) -> dict[str, Any]:
    rows = await w.q(
        "SELECT display_name, job_title, manager_id, department_id, department, start_date,"
        " last_day, leaving_reason, reports_new_manager, version FROM hr_employees WHERE id=%s",
        eid,
    )
    return rows[0]


async def _confirm(w: SessionWorld, browser: httpx.AsyncClient, pid: str) -> httpx.Response:
    return await w.mutate(browser, f"{PROPOSALS}/{pid}/confirm")


def _card(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "proposal", result
    assert result["proposal"]["kind"] == "hr_person"
    return result["proposal"]


def _fields(proposal: dict[str, Any]) -> dict[str, str]:
    return {field["label"]: field["value"] for field in proposal["card"]["fields"]}


async def test_add_person_card_round_trip(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    razak = await _person(w, "Razak Ali", title="Sales Manager", department="Sales")
    model.reply(
        {
            "action": "hr_person_add",
            "display_name": "Danial Hakim",
            "job_title": "Sales Executive",
            "start_date": "2026-12-01",
            "department": "sales",
            "manager": "Razak",
        }
    )
    thread = await _thread(w, browser)
    message = "Add Danial Hakim as Sales Executive in Sales starting 1 December 2026 under Razak"
    proposal = _card(await _turn(w, browser, thread, message, "hr"))
    assert proposal["card"]["title"] == "Add Danial Hakim to People"
    assert _fields(proposal) == {
        "Name": "Danial Hakim",
        "Job title": "Sales Executive",
        "Department": "Sales",
        "Manager": "Razak Ali",
        "Start date": "1 December 2026",
    }
    assert proposal["card"]["expected_version"] is None
    done = await _confirm(w, browser, proposal["id"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed"
    receipt = done.json()["result"]
    assert receipt["operation"] == "person.add"
    added = await _employee(w, UUID(receipt["resource_id"]))
    assert added["display_name"] == "Danial Hakim" and added["job_title"] == "Sales Executive"
    assert added["manager_id"] == razak and added["department"] == "Sales"
    history = await w.q(
        "SELECT count(*) n FROM hr_employee_changes WHERE employee_id=%s", receipt["resource_id"]
    )
    assert history[0]["n"] >= 1
    again = await _confirm(w, browser, proposal["id"])
    assert again.status_code == 200 and again.json() == done.json()
    rows = await w.q("SELECT count(*) n FROM hr_employees WHERE display_name='Danial Hakim'")
    assert rows[0]["n"] == 1


async def test_add_person_card_in_malay_marks_a_new_department(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    model.reply(
        {
            "action": "hr_person_add",
            "display_name": "Danial Hakim",
            "job_title": "Sales Executive",
            "start_date": "2026-12-01",
            "department": "Jualan Baharu",
        }
    )
    thread = await _thread(w, browser)
    body = "Tolong tambah Danial Hakim sebagai Sales Executive bermula 1 Disember 2026"
    proposal = _card(await _turn(w, browser, thread, body, "hr"))
    assert proposal["card"]["language"] == "ms"
    assert _fields(proposal) == {
        "Nama": "Danial Hakim",
        "Jawatan": "Sales Executive",
        "Jabatan": "Jualan Baharu (baharu)",
        "Tarikh mula": "1 Disember 2026",
    }


async def test_update_uses_open_profile_record_when_person_is_omitted(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    ali = await _person(w, "Ali Hassan")
    razak = await _person(w, "Razak Ali")
    siti = await _person(w, "Siti Aminah", title="Finance Manager", manager=ali)
    version = (await _employee(w, siti))["version"]
    model.reply({"action": "hr_person_update", "manager": "Razak"})
    thread = await _thread(w, browser)
    record = {"kind": "employee", "id": str(siti)}
    response = await _turn(w, browser, thread, "Change her manager to Razak", "hr", record=record)
    proposal = _card(response)
    assert proposal["card"]["title"] == "Update Siti Aminah"
    assert _fields(proposal) == {"Manager": "Ali Hassan → Razak Ali"}
    assert proposal["card"]["expected_version"] == str(version)
    done = await _confirm(w, browser, proposal["id"])
    assert done.status_code == 200, done.text
    updated = await _employee(w, siti)
    assert updated["manager_id"] == razak and updated["job_title"] == "Finance Manager"


async def test_ambiguous_name_asks_with_options(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    first = await _person(w, "Siti Aminah", title="Finance Manager")
    second = await _person(w, "Siti Nur", title="Clerk")
    await _person(w, "Razak Ali")
    model.reply({"action": "hr_person_update", "person": "Siti", "manager": "Razak"})
    thread = await _thread(w, browser)
    response = await _turn(w, browser, thread, "Change Siti's manager to Razak", "hr")
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "clarification"
    assert "Siti" in result["question"]
    assert result["options"] == [
        {"label": "Siti Aminah — Finance Manager", "value": str(first)},
        {"label": "Siti Nur — Clerk", "value": str(second)},
    ]
    assert await _proposal_count(w) == 0
    # The pick comes back as the employee id, which resolves to exactly that person.
    model.reply({"action": "hr_person_update", "person": str(second), "manager": "Razak"})
    picked = _card(await _turn(w, browser, thread, str(second), "hr"))
    assert picked["card"]["title"] == "Update Siti Nur"


async def test_unknown_name_answers_not_found(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    await _person(w, "Siti Aminah")
    model.reply({"action": "hr_person_leave", "person": "Zulkifli", "last_day": "2026-12-31"})
    thread = await _thread(w, browser)
    response = await _turn(w, browser, thread, "Mark Zulkifli as leaving", "hr")
    assert response.status_code == 201, response.text
    assert response.json()["result"] == {
        "action": "answer",
        "markdown": "I couldn't find Zulkifli in People.",
    }
    assert await _proposal_count(w) == 0


async def test_missing_start_date_asks_once(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    model.reply(
        {"action": "hr_person_add", "display_name": "Danial Hakim", "job_title": "Sales Executive"}
    )
    thread = await _thread(w, browser)
    response = await _turn(w, browser, thread, "Add Danial Hakim as Sales Executive", "hr")
    assert response.status_code == 201, response.text
    assert response.json()["result"] == {
        "action": "clarification",
        "question": "When do they start? For example 1 December 2026.",
        "options": [],
    }
    assert len(model.calls) == 1
    assert await _proposal_count(w) == 0


async def test_leave_asks_for_the_reason_and_defaults_reports_to_the_leavers_manager(
    planned,  # noqa: F811
) -> None:
    w, model = planned
    browser = await _owner(w)
    ali = await _person(w, "Ali Hassan")
    aina = await _person(w, "Aina Rahman", manager=ali)
    model.reply({"action": "hr_person_leave", "person": "Aina", "last_day": "2099-11-30"})
    thread = await _thread(w, browser)
    asked = await _turn(w, browser, thread, "Mark Aina as leaving on 30 November 2099", "hr")
    assert asked.status_code == 201, asked.text
    question = asked.json()["result"]
    assert question["action"] == "clarification"
    assert [option["value"] for option in question["options"]] == [
        "resigned", "contract_ended", "dismissed", "retired", "other",
    ]  # fmt: skip
    model.reply(
        {
            "action": "hr_person_leave",
            "person": "Aina",
            "last_day": "2099-11-30",
            "reason": "resigned",
        }
    )
    proposal = _card(await _turn(w, browser, thread, "resigned", "hr"))
    assert proposal["card"]["title"] == "Mark Aina Rahman as leaving"
    assert _fields(proposal) == {
        "Last day": "30 November 2099",
        "Reason": "Resigned",
        "Reports' new manager": "Ali Hassan",
    }
    done = await _confirm(w, browser, proposal["id"])
    assert done.status_code == 200, done.text
    row = await _employee(w, aina)
    assert row["leaving_reason"] == "resigned" and row["reports_new_manager"] == ali


async def test_people_changing_actions_refused_without_manage_people(planned) -> None:  # noqa: F811
    w, model = planned
    await _hr_on(w)
    browser, _ = await w.member(role="tenant_staff")
    siti = await _person(w, "Siti Aminah")
    plans = [
        {"action": "hr_person_add", "display_name": "Danial", "job_title": "Sales Executive"},
        {"action": "hr_person_update", "person": "Siti", "job_title": "Clerk"},
        {"action": "hr_person_leave", "person": "Siti", "last_day": "2099-01-01"},
    ]
    thread = await _thread(w, browser)
    for plan in plans:
        model.reply(plan)
        response = await _turn(w, browser, thread, "Add Danial as Sales Executive", "hr")
        assert response.status_code == 201, response.text
        assert response.json()["result"] == {"action": "answer", "markdown": NOT_PERMITTED}
    assert await _proposal_count(w) == 0
    assert (await _employee(w, siti))["job_title"] is None


async def test_card_goes_stale_when_person_changed_before_confirm(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    siti = await _person(w, "Siti Aminah", title="Clerk")
    model.reply({"action": "hr_person_update", "person": "Siti", "job_title": "Senior Clerk"})
    thread = await _thread(w, browser)
    proposal = _card(await _turn(w, browser, thread, "Change Siti's title to Senior Clerk", "hr"))
    assert _fields(proposal) == {"Job title": "Clerk → Senior Clerk"}
    await w.q(
        "UPDATE hr_employees SET work_phone='0123456789', version=version+1 WHERE id=%s", siti
    )
    stale = await _confirm(w, browser, proposal["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    assert (await _employee(w, siti))["job_title"] == "Clerk"


async def test_people_query_lists_reports_within_access(planned) -> None:  # noqa: F811
    w, model = planned
    owner = await _owner(w)
    razak = await _person(w, "Razak Ali")
    await _person(w, "Hafiz Rahman", title="Analyst", manager=razak)
    await _person(w, "Mei Ling Tan", title="Clerk", manager=razak)
    await _person(w, "Kumar Raj")
    model.reply({"action": "people_query", "reports_to": "Razak"})
    thread = await _thread(w, owner)
    response = await _turn(w, owner, thread, "Who reports to Razak?", "hr")
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "directory_lookup"
    assert [p["display_name"] for p in result["candidates"]] == ["Hafiz Rahman", "Mei Ling Tan"]
    # A member with no People access (the "self" view) gets an answer and no names.
    staff, _ = await w.member(role="tenant_staff")
    model.reply({"action": "people_query", "reports_to": "Razak"})
    mine = await _thread(w, staff)
    refused = await _turn(w, staff, mine, "Who reports to Razak?", "hr")
    assert refused.status_code == 201, refused.text
    assert refused.json()["result"] == {"action": "answer", "markdown": LIST_NOT_PERMITTED}
    for name in ("Hafiz", "Mei Ling", "Kumar", "Razak Ali"):
        assert name not in refused.text


async def test_open_profile_navigates_to_the_profile_path(planned) -> None:  # noqa: F811
    from csa.workspace.hr_people import profile_path

    w, model = planned
    browser = await _owner(w)
    siti = await _person(w, "Siti Aminah")
    model.reply({"action": "hr_person_open", "person": "Siti"})
    thread = await _thread(w, browser)
    response = await _turn(w, browser, thread, "Open Siti's profile", "overview")
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "navigate" and result["tab"] == "hr"
    assert result["path"] == f"/portal/hr/people/{siti}"
    assert result["path"].endswith(f"/hr/people/{siti}")
    tenant = w.tenants[0]
    assert profile_path(siti, platform=True, client_id=UUID(tenant)) == (
        f"/hr/people/{siti}?client_id={tenant}"
    )


async def test_planner_facts_never_hold_personal_fields(planned) -> None:  # noqa: F811
    w, model = planned
    browser = await _owner(w)
    tenant = w.tenants[0]
    await _person(w, "Razak Ali")
    siti = await _person(w, "Siti Aminah")
    plaintext = "880101-14-5678"
    ciphertext = crypto.encrypt(
        plaintext, crypto.credential_aad("hr_employee_personal", "ic_or_passport", str(siti))
    )
    await w.q(
        "INSERT INTO hr_employee_personal(client_id,employee_id,ic_or_passport,home_address,"
        "personal_phone,bank_name) VALUES (%s,%s,%s,'12 Jalan Rahsia','0199998888','Bank Rahsia')",
        tenant, siti, ciphertext,
    )  # fmt: skip
    users = await w.q("SELECT id FROM console_users ORDER BY created_at LIMIT 1")
    await w.q(
        "INSERT INTO hr_employee_documents(client_id,id,employee_id,kind,file_name,storage_name,"
        "media_type,byte_size,sha256,uploaded_by,request_key) VALUES (%s,%s,%s,'identity',"
        "'siti-ic-scan-secret.pdf',%s,'application/pdf',10,%s,%s,%s)",
        tenant, uuid4(), siti, f"{uuid4()}.pdf", "a" * 64, users[0]["id"], uuid4(),
    )  # fmt: skip
    seen: list[dict[str, Any]] = []

    def answer(facts: dict[str, Any]) -> dict[str, Any]:
        seen.append(facts)
        return {"action": "answer", "markdown": "ok"}

    model.respond(answer)
    thread = await _thread(w, browser)
    record = {"kind": "employee", "id": str(siti)}
    response = await _turn(w, browser, thread, "Change her manager to Razak", "hr", record=record)
    assert response.status_code == 201, response.text
    assert seen[0]["people_access"] == "hr"
    _, system, user = model.calls[-1]
    prompt = system + user + json.dumps(seen[0])
    for secret in (
        plaintext, ciphertext, "12 Jalan Rahsia", "0199998888", "Bank Rahsia",
        "siti-ic-scan-secret", ".pdf",
    ):  # fmt: skip
        assert secret not in prompt


async def test_refusal_only_the_database_decides_comes_back_at_confirm(planned) -> None:  # noqa: F811
    """The company's only HR owner marking their own person as leaving: the card is made, and
    Confirm is refused with ``last_owner_leaving`` (the dock shows the profile's sentence for it,
    lib/hr-person-card.ts). Nothing changes and the card stays open."""
    w, model = planned
    browser = await _owner(w)
    me = await w.q("SELECT user_id FROM hr_client_owners WHERE client_id=%s", w.tenants[0])
    myself = await _person(w, "Owner Synthetic")
    await w.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        w.tenants[0], me[0]["user_id"], myself,
    )  # fmt: skip
    model.reply(
        {
            "action": "hr_person_leave",
            "person": "Owner Synthetic",
            "last_day": "2099-12-31",
            "reason": "retired",
        }
    )
    thread = await _thread(w, browser)
    proposal = _card(await _turn(w, browser, thread, "Mark me as leaving, I am retiring", "hr"))
    refused = await _confirm(w, browser, proposal["id"])
    assert refused.status_code == 409, refused.text
    assert refused.json() == {"detail": "last_owner_leaving"}
    assert (await _employee(w, myself))["last_day"] is None
    seen = await browser.get(f"{PROPOSALS}/{proposal['id']}")
    assert seen.json()["status"] == "requested"
