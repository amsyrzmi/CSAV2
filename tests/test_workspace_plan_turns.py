"""Planner turns that make a plan card: ``email_batch`` and ``plan`` (assistant engine, Task 9).

A scripted planner (as in tests/test_workspace_planner.py) and a scripted drafter run against a
disposable migrated Postgres through the real routes: sessions, packages, the mailbox, the
directory, subscriptions, bookings, draft rows and the proposal registry. No network. Every
person, address, subject and body is invented.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from csa.bookings.api import build_bookings_router
from csa.workspace import email_batch

from tests.conftest import requires_docker
from tests.test_bookings_api import soon_tuesday
from tests.test_bookings_setup import Team, make_person, make_resource
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_customer_replies import activate_only
from tests.test_email_batch import BatchModel
from tests.test_workspace_planner import (  # noqa: F401 - fixture import
    PROPOSALS,
    ScriptedCompletion,
    _mail_ready,
    _proposal_count,
    _thread,
    _turn,
    planned,
)
from tests.test_workspace_registry import proposals_app  # noqa: F401 - fixture import

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

AINA = ("Aina Example", "aina.example@example.test")
BAKAR = ("Bakar Example", "bakar.example@example.test")
TYPED = "carol.typed@example.test"
SUBJECT = "Office closes early"
BODY_MARK = "Thank you for staying with us"  # BatchModel's body
SPLIT = {
    "en": "That is more than one request can do: at most 20 steps, 10 of them bookings, "
    "training or inbox decisions, and 20 email recipients. Split it into smaller requests.",
    "ms": "Itu lebih daripada yang boleh dibuat dalam satu permintaan: paling banyak 20 "
    "langkah, 10 daripadanya tempahan, latihan atau keputusan peti masuk, dan 20 penerima "
    "e-mel. Pecahkan kepada permintaan yang lebih kecil.",
}
RESUME_UNKNOWN = "I couldn't find the earlier email to continue. Ask again for the whole group."
ONE_BATCH = "I can write one batch of emails per request. Ask for the other emails separately."
REST = ' Say "the rest" to email the next ones.'
DAY = soon_tuesday()


async def _employee(w: SessionWorld, person: tuple[str, str]) -> None:
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,work_email) VALUES (%s,%s,%s,%s)",
        w.tenants[0], uuid4(), person[0], person[1],
    )  # fmt: skip


async def _subscriber(w: SessionWorld, name: str, address: str) -> None:
    contact, now = uuid4(), datetime.now(UTC)
    await w.q(
        "INSERT INTO contacts(id,client_id,full_name,email) VALUES (%s,%s,%s,%s)",
        contact, w.tenants[0], name, address,
    )  # fmt: skip
    await w.q(
        "INSERT INTO subscriptions(id,client_id,contact_id,plan,status,price,started_at,renews_at)"
        " VALUES (%s,%s,%s,'basic','active',10,%s,%s)",
        uuid4(), w.tenants[0], contact, now - timedelta(days=30), now + timedelta(days=9),
    )  # fmt: skip


def _batch(*recipients: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "action": "email_batch",
        "recipients": list(recipients),
        "subject": SUBJECT,
        "instructions": "Tell them the office closes at 5pm on Friday",
        **fields,
    }


def _named(*people: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"kind": "employee", "value": name} for name, _ in people]


def _card(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "proposal", result
    assert result["proposal"]["kind"] == "plan"
    return result["proposal"]  # type: ignore[no-any-return]


def _answer(response: httpx.Response) -> str:
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "answer", result
    return result["markdown"]  # type: ignore[no-any-return]


async def _steps(w: SessionWorld, proposal_id: str) -> list[dict[str, Any]]:
    rows = await w.q("SELECT payload FROM console_proposals WHERE id=%s", UUID(proposal_id))
    return rows[0]["payload"]["steps"]  # type: ignore[no-any-return]


async def _recipients(w: SessionWorld, steps: list[dict[str, Any]]) -> list[str]:
    ids = [UUID(str(step["payload"]["draft_id"])) for step in steps]
    rows = await w.q("SELECT id, to_addresses FROM email_drafts WHERE id = ANY(%s)", ids)
    by_id = {row["id"]: row["to_addresses"] for row in rows}
    return [address for draft_id in ids for address in by_id[draft_id]]


async def _draft_count(w: SessionWorld) -> int:
    return (await w.q("SELECT count(*) n FROM email_drafts"))[0]["n"]  # type: ignore[no-any-return]


def _clean_drafter(w: SessionWorld) -> BatchModel:
    """A drafter whose drafts need no staff input, so a send step is possible."""
    model = BatchModel()
    w.app.state.email_service.batch_model = model
    return model


# --------------------------------------------------------------------------- email_batch


async def test_email_batch_turn_creates_a_plan_card_of_email_steps(planned) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    recipients = [*_named(AINA, BAKAR), {"kind": "address", "value": TYPED}]
    model.reply(_batch(*recipients, mode="shared"))
    body = f"Draft an email to Aina Example, Bakar Example and {TYPED} that we close at 5pm"
    proposal = _card(await _turn(w, browser, thread, body, "email"))
    assert proposal["status"] == "requested"
    card = proposal["card"]
    assert card["title"] == "Confirm 3 steps"
    assert [field["label"] for field in card["fields"]] == ["Save an email to Gmail drafts"] * 3
    steps = await _steps(w, proposal["id"])
    assert [step["kind"] for step in steps] == ["email_export"] * 3  # "Draft": never a send
    assert [field["value"] for field in card["fields"]] == [
        step["payload"]["draft_id"] for step in steps
    ]
    assert await _recipients(w, steps) == [AINA[1], BAKAR[1], TYPED]
    rows = await w.q("SELECT action, page_key FROM console_workspace_turns")
    assert rows == [{"action": "email_batch", "page_key": "email"}]
    origin = await w.q("SELECT origin, thread_id FROM console_proposals")
    assert origin == [{"origin": "assistant", "thread_id": UUID(thread)}]


async def test_a_send_word_gives_send_steps_and_no_send_word_only_exports(planned) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    # The planner says no send; the user's own word "Send" decides (the owner's rule).
    model.reply(_batch(*_named(AINA, BAKAR), send=False))
    sent = _card(await _turn(w, browser, thread, "Send the notice to Aina and Bakar", "email"))
    assert [s["kind"] for s in await _steps(w, sent["id"])] == ["email_send"] * 2
    assert [f["label"] for f in sent["card"]["fields"]] == ["Send an email"] * 2
    # The planner says send; without a send word in the user's words it stays a draft.
    model.reply(_batch(*_named(AINA, BAKAR), send=True))
    kept = _card(await _turn(w, browser, thread, "Prepare the notice for Aina and Bakar", "email"))
    assert [s["kind"] for s in await _steps(w, kept["id"])] == ["email_export"] * 2


async def test_an_answer_turn_reads_only_the_users_own_words_for_send(planned) -> None:
    """The folded body holds the planner's question with "send": it can never make a send."""
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    first = "Prepare the office notice for Aina Example and Bakar Example"
    question = "Should I send the notice to both of them now?"
    model.reply({"action": "clarification", "question": question})
    asked = await _turn(w, browser, thread, first, "email")
    assert asked.status_code == 201 and asked.json()["result"]["action"] == "clarification"
    model.reply(_batch(*_named(AINA, BAKAR), send=True))
    answer = f"It is about Friday\n(Answer to “{question}” for: {first})"
    proposal = _card(await _turn(w, browser, thread, answer, "email"))
    assert [s["kind"] for s in await _steps(w, proposal["id"])] == ["email_export"] * 2
    # The same answer with the user's own "send" typed is a send.
    model.reply({"action": "clarification", "question": question})
    await _turn(w, browser, thread, first, "email")
    model.reply(_batch(*_named(AINA, BAKAR), send=False))
    typed = f"Yes, send it\n(Answer to “{question}” for: {first})"
    sent = _card(await _turn(w, browser, thread, typed, "email"))
    assert [s["kind"] for s in await _steps(w, sent["id"])] == ["email_send"] * 2


async def test_the_rest_resumes_after_the_stored_count_and_never_repeats_anyone(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model = planned
    monkeypatch.setattr(email_batch, "MAX_LIMIT", 2)  # a page of 2 instead of 20
    browser = await _mail_ready(w, "hr", "subscriptions")
    _clean_drafter(w)
    await _employee(w, AINA)
    subscribers = [f"subscriber{i}@example.test" for i in range(3)]
    for i, address in enumerate(subscribers):
        await _subscriber(w, f"Subscriber Example {i}", address)
    thread = await _thread(w, browser)
    batch = _batch(*_named(AINA), {"kind": "group", "group": "active_subscribers"}, send=True)
    model.reply(batch)
    body = "Send the renewal notice to Aina Example and all active subscribers"
    first = _card(await _turn(w, browser, thread, body, "email"))
    one = await _steps(w, first["id"])
    assert [s["kind"] for s in one] == ["email_send"] * 2
    page_one = await _recipients(w, one)
    assert page_one[0] == AINA[1] and page_one[1] in subscribers
    assert first["card"]["summary"].endswith(REST)
    stored = await w.q("SELECT result FROM console_workspace_turns ORDER BY id")
    assert stored[-1]["result"]["resume_offset"] == 1  # a count, nothing else

    def rest(facts: dict[str, Any]) -> dict[str, Any]:
        assert facts["history"][-1]["resume_offset"] == 1
        return {**batch, "offset": facts["history"][-1]["resume_offset"]}

    model.respond(rest)
    second = _card(await _turn(w, browser, thread, "Now the rest please", "email"))
    two = await _steps(w, second["id"])
    page_two = await _recipients(w, two)
    assert AINA[1] not in page_two and page_one[1] not in page_two  # nobody twice
    assert sorted([*page_one[1:], *page_two]) == sorted(subscribers)
    # No send word this time: nobody gets a second message, and these wait as drafts.
    assert [s["kind"] for s in two] == ["email_export"] * 2
    assert REST not in second["card"]["summary"]
    assert "resume_offset" not in (await w.q(
        "SELECT result FROM console_workspace_turns ORDER BY id DESC LIMIT 1"
    ))[0]["result"]  # fmt: skip
    # An offset the thread never stored is not followed: no new draft.
    drafts = await _draft_count(w)
    model.reply({**batch, "offset": 7})
    refused = _answer(await _turn(w, browser, thread, "And the rest again", "email"))
    assert refused == RESUME_UNKNOWN
    assert await _draft_count(w) == drafts


async def test_batch_without_the_email_package_is_refused_before_any_draft(planned) -> None:
    w, model = planned
    drafter = _clean_drafter(w)
    tenant = w.tenants[0]
    await activate(w, tenant, "hr")
    await _employee(w, AINA)
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply(_batch(*_named(AINA)))
    refused = await _turn(w, browser, thread, "Send the notice to Aina Example", "email")
    assert refused.status_code == 403 and refused.json()["detail"] == "package_inactive"
    # The package is on but no mailbox is connected: refused the same way, before any draft.
    await activate_only(w, tenant, "staff_email")
    await w.q("UPDATE email_settings SET enabled=true WHERE client_id=%s", tenant)
    model.reply(_batch(*_named(AINA)))
    unconnected = await _turn(w, browser, thread, "Send the notice to Aina Example", "email")
    assert unconnected.status_code == 409, unconnected.text
    assert unconnected.json()["detail"] == "mailbox_not_connected"
    assert await _draft_count(w) == 0 and await _proposal_count(w) == 0
    assert drafter.calls == [] and w.app.state.email_service.model.calls == []
    assert await w.q("SELECT 1 FROM console_workspace_turns") == []


async def test_a_typed_batch_address_must_be_one_the_user_wrote(planned) -> None:
    """Review Focus 4 on the batch path: an address only the planner wrote is never mailed."""
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    thread = await _thread(w, browser)
    unrequested = {"kind": "address", "value": "mallory@hostile.example"}
    model.reply(_batch({"kind": "address", "value": TYPED}, unrequested))
    model.reply(_batch({"kind": "address", "value": TYPED}, unrequested))
    turn = await _turn(w, browser, thread, f"Send the notice to {TYPED} and the others", "email")
    assert turn.status_code == 201, turn.text
    assert turn.json()["result"]["action"] == "clarification"  # the recipient fallback (M7)
    assert await _draft_count(w) == 0 and await _proposal_count(w) == 0


async def test_batch_card_turn_row_and_audit_rows_hold_no_address_name_subject_or_body(
    planned,
) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    recipients = [*_named(AINA, BAKAR), {"kind": "employee", "value": "Nobody Known"}]
    model.reply(_batch(*recipients, {"kind": "address", "value": TYPED}))
    body = f"Send the notice to Aina Example, Bakar Example, Nobody Known and {TYPED}"
    response = await _turn(w, browser, thread, body, "email")
    proposal = _card(response)
    assert "Not included: 1 (1 not found)." in proposal["card"]["summary"]
    secrets = (AINA[0], AINA[1], BAKAR[0], BAKAR[1], TYPED, SUBJECT, BODY_MARK, "Nobody Known")
    shown = json.dumps(response.json()["result"])
    cards = json.dumps(
        await w.q("SELECT payload, card, result FROM console_proposals"), default=str
    )
    turns = await w.q("SELECT body, result FROM console_workspace_turns")
    results = json.dumps([row["result"] for row in turns])
    audits = json.dumps(await w.q("SELECT target_id, details FROM console_audit"), default=str)
    for secret in secrets:
        assert secret not in shown, secret
        assert secret not in cards, secret
        assert secret not in results, secret
        assert secret not in audits, secret
    assert TYPED not in turns[0]["body"]  # the stored message is redacted as every turn's is
    # The drafts themselves hold the recipients, readable by their owner only.
    assert TYPED in json.dumps(await _recipients(w, await _steps(w, proposal["id"])))


async def test_nobody_found_answers_with_counts_and_writes_no_draft(planned) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    thread = await _thread(w, browser)
    nobody = (
        {"kind": "employee", "value": "Nobody Known"},
        {"kind": "employee", "value": "Nobody Else"},
    )
    model.reply(_batch(*nobody))
    body = "Send it to Nobody Known and Nobody Else"
    text = _answer(await _turn(w, browser, thread, body, "email"))
    assert text == "I couldn't find anyone to email. Not included: 2 (2 not found)."
    assert await _draft_count(w) == 0 and await _proposal_count(w) == 0


async def test_batch_over_the_cap_is_refused_with_the_split_message(planned) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    thread = await _thread(w, browser)
    addresses = [f"person{i}@example.test" for i in range(21)]
    model.reply(_batch(*({"kind": "address", "value": a} for a in addresses)))
    text = _answer(await _turn(w, browser, thread, "Send it to " + ", ".join(addresses), "email"))
    assert text == SPLIT["en"]
    assert await _draft_count(w) == 0 and await _proposal_count(w) == 0


# --------------------------------------------------------------------------- plan


@pytest.fixture
async def team(planned) -> Team:
    w, _ = planned
    w.app.include_router(build_bookings_router())
    for tenant in w.tenants:
        await activate_only(w, tenant, "bookings")
        await w.q("UPDATE clients SET timezone = 'UTC' WHERE id = %s", tenant)
    return await Team(w).open()


def _book(**fields: Any) -> dict[str, Any]:
    base = {"change": "book", "title": "Weekly sync", "minutes": 30, "day": DAY, "time": "15:00"}
    return {"action": "booking_action", **base, **fields}


async def _bookings(w: SessionWorld) -> list[dict[str, Any]]:
    return await w.q("SELECT id, starts_at FROM bookings WHERE client_id=%s", w.tenants[0])


async def test_plan_of_two_bookings_resolves_each_and_prepares_one_plan(planned, team) -> None:
    w, model = planned
    await make_person(team, "Aina Salleh")
    await make_person(team, "Bakar Salleh")
    await make_resource(team, "Room A")
    await make_resource(team, "Room B")
    thread = await _thread(w, team.staff)
    model.reply(
        {
            "action": "plan",
            "steps": [
                _book(people=["Aina"], rooms=["Room A"]),
                _book(people=["Bakar"], rooms=["Room B"], time="16:00"),
            ],
        }
    )
    body = "Book Room A at 3 pm with Aina and Room B at 4 pm with Bakar"
    proposal = _card(await _turn(w, team.staff, thread, body, "bookings"))
    card = proposal["card"]
    assert card["title"] == "Confirm 2 steps"
    assert [f["label"] for f in card["fields"]] == ["Book, move or cancel a booking"] * 2
    for name in ("Aina", "Bakar", "Room A", "Room B", "Weekly sync"):
        assert name not in json.dumps(card), name
    steps = await _steps(w, proposal["id"])
    assert [s["kind"] for s in steps] == ["booking_action"] * 2
    assert all(s["version"] for s in steps)
    assert await _bookings(w) == []  # nothing is booked until Confirm
    rows = await w.q("SELECT action FROM console_workspace_turns")
    assert rows == [{"action": "plan"}]
    done = await w.mutate(team.staff, f"{PROPOSALS}/{proposal['id']}/confirm")
    assert done.status_code == 200, done.text
    assert [s["status"] for s in done.json()["result"]["steps"]] == ["done", "done"]
    assert len(await _bookings(w)) == 2


async def test_unresolvable_step_is_left_off_and_counted(planned, team) -> None:
    w, model = planned
    await make_person(team, "Aina Salleh")
    await make_person(team, "Aina Tan")
    await make_person(team, "Bakar Salleh")
    thread = await _thread(w, team.staff)
    model.reply(
        {
            "action": "plan",
            "steps": [
                _book(people=["Aina"]),  # two Ainas: a question, so it is left off
                _book(people=["Zulkifli"]),  # nobody of that name
                _book(people=["Bakar"], time="16:00"),
            ],
        }
    )
    body = "Book Aina at 3, Zulkifli at 3 and Bakar at 4"
    proposal = _card(await _turn(w, team.staff, thread, body, "bookings"))
    card = proposal["card"]
    assert card["title"] == "Confirm 1 step"
    left = " Not included: 2 (1 needs more details, 1 could not be prepared)."
    assert card["summary"].endswith(left)
    assert len(await _steps(w, proposal["id"])) == 1
    stored = json.dumps(await w.q("SELECT result FROM console_workspace_turns"))
    for name in ("Aina", "Zulkifli", "Bakar", "Salleh"):
        assert name not in json.dumps(card) and name not in stored, name


async def test_a_plan_where_nothing_resolves_is_the_first_steps_own_answer(planned, team) -> None:
    w, model = planned
    thread = await _thread(w, team.staff)
    model.reply({"action": "plan", "steps": [_book(people=["Zulkifli"]), _book(people=["Zainal"])]})
    text = _answer(await _turn(w, team.staff, thread, "Book Zulkifli and Zainal", "bookings"))
    assert text == "I couldn't find Zulkifli among the people who can be booked."
    assert await _proposal_count(w) == 0


async def test_plan_over_the_cap_is_refused_with_the_split_message(planned, team) -> None:
    w, model = planned
    await make_person(team, "Aina Salleh")
    thread = await _thread(w, team.staff)
    eleven = [_book(people=["Aina"], time=f"{8 + i:02d}:00") for i in range(11)]
    model.reply({"action": "plan", "steps": eleven})
    text = _answer(await _turn(w, team.staff, thread, "Book Aina every hour from 8", "bookings"))
    assert text == SPLIT["en"]
    model.reply({"action": "plan", "steps": eleven})
    malay = "Tolong tempah Aina setiap jam dari pukul 8 pagi"
    assert _answer(await _turn(w, team.staff, thread, malay, "bookings")) == SPLIT["ms"]
    # Two email batches in one plan are refused too, before any read or draft.
    two = [_batch({"kind": "address", "value": TYPED}), _batch({"kind": "address", "value": TYPED})]
    model.reply({"action": "plan", "steps": two})
    text = _answer(await _turn(w, team.staff, thread, f"Send two notes to {TYPED}", "bookings"))
    assert text == ONE_BATCH
    assert await _proposal_count(w) == 0 and await _draft_count(w) == 0


async def test_a_plan_mixes_a_booking_and_an_email_batch(planned, team) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await make_person(team, "Aina Salleh")
    thread = await _thread(w, browser)
    model.reply(
        {
            "action": "plan",
            "steps": [
                _book(people=["Aina"]),
                _batch(*_named(AINA), {"kind": "address", "value": TYPED}),
            ],
        }
    )
    body = f"Book Aina at 3 and send the notice to Aina Example and {TYPED}"
    proposal = _card(await _turn(w, browser, thread, body, "bookings"))
    steps = await _steps(w, proposal["id"])
    assert [s["kind"] for s in steps] == ["booking_action", "email_send", "email_send"]
    assert proposal["card"]["title"] == "Confirm 3 steps"
