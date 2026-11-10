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
from csa.bookings import executor as booking_executor
from csa.bookings.api import build_bookings_router
from csa.workspace import email_batch
from fastapi import HTTPException

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


async def _subscriber(
    w: SessionWorld, name: str, address: str, contact: UUID | None = None
) -> None:
    contact, now = contact or uuid4(), datetime.now(UTC)
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


async def test_a_batch_without_mode_is_drafted_once_for_the_same_email(planned) -> None:
    """The planner left mode out (the staging smoke failure): "the same email" in the user's
    words drafts one shared text, copied to each; other words draft each person."""
    w, model = planned
    browser = await _mail_ready(w, "hr")
    drafter = _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    model.reply(_batch(*_named(AINA, BAKAR)))
    body = "Draft the same email to Aina Example and Bakar Example: we close at 5pm"
    shared = _card(await _turn(w, browser, thread, body, "email"))
    assert len(await _steps(w, shared["id"])) == 2
    assert len(drafter.named("DraftProposal")) == 1
    other = await _thread(w, browser)
    model.reply(_batch(*_named(AINA, BAKAR)))
    body = "Draft an email to Aina Example and Bakar Example that we close at 5pm"
    each = _card(await _turn(w, browser, other, body, "email"))
    assert len(await _steps(w, each["id"])) == 2
    assert len(drafter.named("DraftProposal")) == 3


async def test_a_send_word_gives_send_steps_and_no_send_word_only_exports(planned) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    # The planner says no send; the user's own word "Send" decides (the owner's rule).
    model.reply(_batch(*_named(AINA, BAKAR), send=False))
    body = "Send the notice to Aina Example and Bakar Example"
    sent = _card(await _turn(w, browser, thread, body, "email"))
    assert [s["kind"] for s in await _steps(w, sent["id"])] == ["email_send"] * 2
    assert [f["label"] for f in sent["card"]["fields"]] == ["Send an email"] * 2
    # The planner says send; without a send word in the user's words it stays a draft.
    model.reply(_batch(*_named(AINA, BAKAR), send=True))
    body = "Prepare the notice for Aina Example and Bakar Example"
    kept = _card(await _turn(w, browser, thread, body, "email"))
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
    # The page already offered is never drafted again, nor an offset the thread never stored.
    drafts = await _draft_count(w)
    for offset in (1, 7):
        model.reply({**batch, "offset": offset})
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


# ------------------------------------------- fix round 1: recipients tied to the user's words

RECIPIENT_FALLBACK = "I couldn't work out who to send that to. Type their email address."


async def test_a_group_the_user_never_named_is_refused(planned) -> None:
    """The planner alone cannot add a group of customers: no group word, no group (M7)."""
    w, model = planned
    browser = await _mail_ready(w, "hr", "subscriptions")
    drafter = _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    await _subscriber(w, "Subscriber Example", "subscriber@example.test")
    thread = await _thread(w, browser)
    added = _batch(*_named(AINA, BAKAR), {"kind": "group", "group": "active_subscribers"})
    model.reply(added)
    model.reply(added)
    body = "Email the notice to Aina Example and Bakar Example"
    turn = await _turn(w, browser, thread, body, "email")
    assert turn.status_code == 201, turn.text
    result = turn.json()["result"]
    assert result["action"] == "clarification" and result["question"] == RECIPIENT_FALLBACK
    assert await _draft_count(w) == 0 and await _proposal_count(w) == 0
    assert drafter.calls == []
    # The user's own group word lets the same group through.
    model.reply(added)
    named = "Email the notice to Aina Example, Bakar Example and all our subscribers"
    assert len(await _steps(w, _card(await _turn(w, browser, thread, named, "email"))["id"])) == 3


async def test_a_customer_name_the_user_never_wrote_is_refused(planned) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr", "subscriptions")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _subscriber(w, "Dora Customer", "dora@example.test")
    thread = await _thread(w, browser)
    added = _batch(*_named(AINA), {"kind": "customer", "value": "Dora Customer"})
    model.reply(added)
    model.reply(added)
    turn = await _turn(w, browser, thread, "Email the notice to Aina Example and the team", "email")
    assert turn.status_code == 201, turn.text
    assert turn.json()["result"]["question"] == RECIPIENT_FALLBACK
    assert await _draft_count(w) == 0 and await _proposal_count(w) == 0


async def test_a_name_in_the_users_words_passes_in_any_case(planned) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr", "subscriptions")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _subscriber(w, "Dora Customer", "dora@example.test")
    thread = await _thread(w, browser)
    model.reply(_batch(*_named(AINA), {"kind": "customer", "value": "Dora Customer"}))
    body = "send the notice to aina example and DORA CUSTOMER"
    steps = await _steps(w, _card(await _turn(w, browser, thread, body, "email"))["id"])
    assert await _recipients(w, steps) == [AINA[1], "dora@example.test"]


async def test_a_name_only_in_the_planners_question_is_refused_and_a_clicked_one_passes(
    planned,
) -> None:
    """On an answer turn the folded question is the planner's text: a name only there is not the
    user's. A name the user picked from the options offered is."""
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    first = "Prepare the office notice email for my two colleagues"
    question = "Should I write to Aina Example and Bakar Example?"
    model.reply({"action": "clarification", "question": question})
    asked = await _turn(w, browser, thread, first, "email")
    assert asked.json()["result"]["action"] == "clarification"
    named = _batch(*_named(AINA, BAKAR))
    model.reply(named)
    model.reply(named)
    answer = f"Yes please\n(Answer to “{question}” for: {first})"
    turn = await _turn(w, browser, thread, answer, "email")
    assert turn.status_code == 201, turn.text
    assert turn.json()["result"]["question"] == RECIPIENT_FALLBACK
    assert await _draft_count(w) == 0 and await _proposal_count(w) == 0
    # A name the user clicked among the options offered is theirs.
    pick = "Who should get the notice?"
    options = [{"label": AINA[0], "value": AINA[0]}, {"label": BAKAR[0], "value": BAKAR[0]}]
    model.reply({"action": "clarification", "question": pick, "options": options})
    await _turn(w, browser, thread, first, "email")
    model.reply(_batch(*_named(AINA)))
    clicked = f"{AINA[0]}\n(Answer to “{pick}” for: {first})"
    steps = await _steps(w, _card(await _turn(w, browser, thread, clicked, "email"))["id"])
    assert await _recipients(w, steps) == [AINA[1]]


# ------------------------------------------- fix round 1: a batch refusal in a mixed plan


async def test_booking_and_batch_without_the_email_package_keeps_the_booking(planned, team) -> None:
    w, model = planned
    await make_person(team, "Aina Salleh")
    drafter = _clean_drafter(w)
    thread = await _thread(w, team.staff)
    model.reply({"action": "plan", "steps": [_batch(*_named(AINA)), _book(people=["Aina"])]})
    body = "Send the notice to Aina Example and book Aina at 3"
    proposal = _card(await _turn(w, team.staff, thread, body, "bookings"))
    assert proposal["card"]["title"] == "Confirm 1 step"
    assert proposal["card"]["summary"].endswith(" Not included: 1 (1 could not be prepared).")
    assert [s["kind"] for s in await _steps(w, proposal["id"])] == ["booking_action"]
    assert await _draft_count(w) == 0 and drafter.calls == []


async def test_a_batch_listed_first_writes_no_draft_when_a_later_booking_fails(
    planned, team, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    drafter = _clean_drafter(w)
    await _employee(w, AINA)
    await make_person(team, "Aina Salleh")

    async def broken(*_: Any, **__: Any) -> Any:
        raise HTTPException(503, "bookings_unavailable")

    monkeypatch.setattr(booking_executor, "resolve", broken)
    thread = await _thread(w, browser)
    model.reply({"action": "plan", "steps": [_batch(*_named(AINA)), _book(people=["Aina"])]})
    body = "Send the notice to Aina Example and book Aina at 3"
    failed = await _turn(w, browser, thread, body, "bookings")
    assert failed.status_code == 503, failed.text
    assert await _draft_count(w) == 0 and drafter.calls == []
    assert await _proposal_count(w) == 0


async def test_a_mixed_plan_keeps_the_planners_step_order(planned, team) -> None:
    w, model = planned
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await make_person(team, "Aina Salleh")
    thread = await _thread(w, browser)
    model.reply({"action": "plan", "steps": [_batch(*_named(AINA)), _book(people=["Aina"])]})
    body = "Send the notice to Aina Example and book Aina at 3"
    proposal = _card(await _turn(w, browser, thread, body, "bookings"))
    kinds = [s["kind"] for s in await _steps(w, proposal["id"])]
    assert kinds == ["email_send", "booking_action"]


# ------------------------------------------- fix round 1: "the rest" never repeats anyone


async def _ordered_subscribers(w: SessionWorld, size: int) -> list[str]:
    """Subscribers ``Subscriber Example 0..size-1`` whose ids sort in that order (the group's
    page order), returning their addresses in that order."""
    ids = sorted(uuid4() for _ in range(size))
    addresses = [f"subscriber{i}@example.test" for i in range(size)]
    for i, (contact, address) in enumerate(zip(ids, addresses, strict=True)):
        await _subscriber(w, f"Subscriber Example {i}", address, contact)
    return addresses


async def test_page_two_never_drafts_a_named_customer_or_typed_address_again(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model = planned
    monkeypatch.setattr(email_batch, "MAX_LIMIT", 3)
    browser = await _mail_ready(w, "hr", "subscriptions")
    _clean_drafter(w)
    members = await _ordered_subscribers(w, 4)
    thread = await _thread(w, browser)
    batch = _batch(
        {"kind": "customer", "value": "Subscriber Example 3"},
        {"kind": "address", "value": members[2]},
        {"kind": "group", "group": "active_subscribers"},
        send=True,
    )
    model.reply(batch)
    body = f"Send the renewal notice to Subscriber Example 3, {members[2]} and all subscribers"
    first = _card(await _turn(w, browser, thread, body, "email"))
    page_one = await _recipients(w, await _steps(w, first["id"]))
    assert page_one == [members[3], members[2], members[0]]
    model.respond(lambda facts: {**batch, "offset": facts["history"][-1]["resume_offset"]})
    second = _card(await _turn(w, browser, thread, "Send the rest please", "email"))
    page_two = await _recipients(w, await _steps(w, second["id"]))
    assert page_two == [members[1]]  # members 2 and 3 were named on page one
    assert "Not included: 2 (2 listed twice)." in second["card"]["summary"]


async def test_the_rest_continues_when_the_planner_forgets_the_offset(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model = planned
    monkeypatch.setattr(email_batch, "MAX_LIMIT", 2)
    browser = await _mail_ready(w, "hr", "subscriptions")
    _clean_drafter(w)
    await _employee(w, AINA)
    members = await _ordered_subscribers(w, 3)
    thread = await _thread(w, browser)
    batch = _batch(*_named(AINA), {"kind": "group", "group": "active_subscribers"})
    model.reply(batch)
    body = "Draft the renewal notice to Aina Example and all active subscribers"
    first = _card(await _turn(w, browser, thread, body, "email"))
    assert await _recipients(w, await _steps(w, first["id"])) == [AINA[1], members[0]]
    model.reply({**batch, "offset": 0})  # the model forgot the stored resume count
    second = _card(await _turn(w, browser, thread, "Now the rest please", "email"))
    assert await _recipients(w, await _steps(w, second["id"])) == members[1:]
    # In Malay too: "selebihnya" continues from the stored count, never page one again.
    model.reply(batch)
    await _turn(w, browser, thread, body, "email")
    model.reply({**batch, "offset": 0})
    rest = _card(await _turn(w, browser, thread, "Tolong draf selebihnya", "email"))
    assert await _recipients(w, await _steps(w, rest["id"])) == members[1:]


# ------------------------------------- fix round 2: a resumed page ties recipients to the user

GENERAL_FALLBACK = "I couldn't work that out. Try rephrasing or say what you want to do."
RENEWING = {"kind": "group", "group": "renewing_soon", "days": 30}
SUBSCRIBERS = {"kind": "group", "group": "active_subscribers"}


async def _resume_world(
    planned: tuple[SessionWorld, Any], monkeypatch: pytest.MonkeyPatch
) -> tuple[SessionWorld, Any, Any, BatchModel, list[str], str]:
    """Pages of 2, two employees and three subscribers renewing within 30 days."""
    w, model = planned
    monkeypatch.setattr(email_batch, "MAX_LIMIT", 2)
    browser = await _mail_ready(w, "hr", "subscriptions")
    drafter = _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    members = await _ordered_subscribers(w, 3)
    return w, model, browser, drafter, members, await _thread(w, browser)


async def _page_one(
    w: SessionWorld, model: Any, browser: Any, thread: str, batch: dict[str, Any], body: str
) -> dict[str, Any]:
    """Page one of a group batch; returns the stored turn row's result (it holds the resume)."""
    model.reply(batch)
    assert _card(await _turn(w, browser, thread, body, "email"))["card"]["summary"].endswith(REST)
    rows = await w.q("SELECT result FROM console_workspace_turns ORDER BY id DESC LIMIT 1")
    return rows[0]["result"]  # type: ignore[no-any-return]


async def _refused(
    w: SessionWorld, model: Any, browser: Any, thread: str, reply: dict[str, Any], body: str
) -> dict[str, Any]:
    """The planner writes ``reply``, and the same again after the M7 correction: no card, no
    draft and so nothing sent."""
    drafts, proposals = await _draft_count(w), await _proposal_count(w)
    model.reply(reply)
    model.reply(reply)
    turn = await _turn(w, browser, thread, body, "email")
    assert turn.status_code == 201, turn.text
    assert model.replies == []  # the corrected plan was asked for too (M7)
    assert await _draft_count(w) == drafts and await _proposal_count(w) == proposals
    result = turn.json()["result"]
    assert result["action"] in ("answer", "clarification"), result
    return result  # type: ignore[no-any-return]


async def test_a_new_name_and_the_others_after_a_renewal_batch_is_refused(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's example: "the others" with a stored count, a person page one never had and
    a group the user never said. Before, Aina was only a seed (never emailed) and the server
    paged subscribers it was never asked for."""
    w, model, browser, drafter, _, thread = await _resume_world(planned, monkeypatch)
    body = "Send the renewal reminder to everyone due for renewal in 30 days"
    resume = (await _page_one(w, model, browser, thread, _batch(RENEWING, send=True), body))[
        "resume_offset"
    ]
    calls = len(drafter.calls)
    example = "Send the notice to Aina Example and the others"
    for offset in (0, resume):  # the planner forgot the stored count, or copied it
        added = _batch(*_named(AINA), SUBSCRIBERS, send=True, offset=offset)
        result = await _refused(w, model, browser, thread, added, example)
        assert result["markdown"] == GENERAL_FALLBACK  # "Send ..." is not about email (M7)
    assert len(drafter.calls) == calls


async def test_a_bare_rest_turn_continues_with_the_stored_group(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model, browser, _, members, thread = await _resume_world(planned, monkeypatch)
    body = "Draft the renewal reminder to everyone due for renewal in 30 days"
    stored = await _page_one(w, model, browser, thread, _batch(RENEWING), body)
    # The group it was counted on is stored beside the count: an enum and a number, nobody.
    assert {k: stored[k] for k in ("resume_offset", "resume_group", "resume_days")} == {
        "resume_offset": 2,
        "resume_group": "renewing_soon",
        "resume_days": 30,
    }
    model.respond(lambda facts: _batch(RENEWING, offset=facts["history"][-1]["resume_offset"]))
    rest = _card(await _turn(w, browser, thread, "Now the rest please", "email"))
    assert await _recipients(w, await _steps(w, rest["id"])) == members[2:]
    last = await w.q("SELECT result FROM console_workspace_turns ORDER BY id DESC LIMIT 1")
    assert not {"resume_offset", "resume_group", "resume_days"} & set(last[0]["result"])
    # In Malay, with the offset forgotten: "selebihnya" continues the same stored group.
    await _page_one(w, model, browser, thread, _batch(RENEWING), body)
    model.reply(_batch(RENEWING, offset=0))
    rest = _card(await _turn(w, browser, thread, "Tolong draf selebihnya", "email"))
    assert await _recipients(w, await _steps(w, rest["id"])) == members[2:]


async def test_a_different_group_on_a_resumed_page_is_refused(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume count was counted on one group: it never pages another, even when the stored
    message's words would fit both ("subscribers renewing")."""
    w, model, browser, _, members, thread = await _resume_world(planned, monkeypatch)
    body = "Draft the reminder to all subscribers renewing in the next 30 days"
    resume = (await _page_one(w, model, browser, thread, _batch(RENEWING), body))["resume_offset"]
    rest = "Now the rest please"
    for group in (SUBSCRIBERS, {**RENEWING, "days": 60}):
        await _refused(w, model, browser, thread, _batch(group, offset=resume), rest)
    proposal = _card(
        await _turn_with(w, model, browser, thread, _batch(RENEWING, offset=resume), rest)
    )
    assert await _recipients(w, await _steps(w, proposal["id"])) == members[2:]


async def test_a_group_word_on_a_resumed_page_comes_from_the_user_or_the_stored_message(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row stored without the group (older rows): the group's words must be in this turn's
    words or the latest batch's stored message."""
    w, model, browser, _, members, thread = await _resume_world(planned, monkeypatch)
    body = "Draft the reminder to everyone due for renewal in 30 days"
    resume = (await _page_one(w, model, browser, thread, _batch(RENEWING), body))["resume_offset"]
    await w.q(
        "UPDATE console_workspace_turns SET result = result - 'resume_group' - 'resume_days'"
        " WHERE id = (SELECT max(id) FROM console_workspace_turns)"
    )
    rest = "Now the rest please"
    await _refused(w, model, browser, thread, _batch(SUBSCRIBERS, offset=resume), rest)
    proposal = _card(
        await _turn_with(w, model, browser, thread, _batch(RENEWING, offset=resume), rest)
    )
    assert await _recipients(w, await _steps(w, proposal["id"])) == members[2:]


async def test_a_name_on_a_resumed_page_must_be_in_the_latest_batchs_message(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a resumed page a named person is only a seed and would never be emailed, so a person
    this turn names (or nobody named) is refused: a new recipient needs its own request."""
    w, model, browser, _, _, thread = await _resume_world(planned, monkeypatch)
    body = "Draft the reminder to all active subscribers"
    resume = (await _page_one(w, model, browser, thread, _batch(SUBSCRIBERS), body))[
        "resume_offset"
    ]
    copied = "Now the rest please, and Bakar Example too"
    await _refused(
        w, model, browser, thread, _batch(*_named(BAKAR), SUBSCRIBERS, offset=resume), copied
    )
    nobody = "Now the rest please"
    await _refused(
        w, model, browser, thread, _batch(*_named(BAKAR), SUBSCRIBERS, offset=resume), nobody
    )
    typed = f"Now the rest please, and {TYPED} too"
    address = {"kind": "address", "value": TYPED}
    await _refused(w, model, browser, thread, _batch(address, SUBSCRIBERS, offset=resume), typed)
    # The planner forgot the count: never continued as a seed, never page one again either.
    forgot = "Draft it to Bakar Example and the remaining subscribers"
    await _refused(w, model, browser, thread, _batch(*_named(BAKAR), SUBSCRIBERS), forgot)


async def test_a_name_in_the_latest_batchs_message_passes_as_a_seed_only(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model, browser, _, members, thread = await _resume_world(planned, monkeypatch)
    batch = _batch(*_named(AINA), SUBSCRIBERS)
    body = "Draft the reminder to Aina Example and all active subscribers"
    resume = (await _page_one(w, model, browser, thread, batch, body))["resume_offset"]
    assert resume == 1
    proposal = _card(
        await _turn_with(
            w, model, browser, thread, {**batch, "offset": resume}, "Now the rest please"
        )
    )
    assert await _recipients(w, await _steps(w, proposal["id"])) == members[1:]  # Aina: a seed


async def _turn_with(
    w: SessionWorld, model: Any, browser: Any, thread: str, reply: dict[str, Any], body: str
) -> httpx.Response:
    model.reply(reply)
    return await _turn(w, browser, thread, body, "email")


# ------------------------------- fix round 3: "the rest" keeps working on every page of a group

REST_PLEASE = "The rest please"


def _copied(batch: dict[str, Any]) -> Any:
    """The planner repeats page one's batch with the latest history turn's resume count."""
    return lambda facts: {**batch, "offset": facts["history"][-1]["resume_offset"]}


async def _chain_world(
    planned: tuple[SessionWorld, Any], monkeypatch: pytest.MonkeyPatch, *, page: int, size: int
) -> tuple[SessionWorld, Any, Any, list[str], str]:
    """Pages of ``page``, two employees and ``size`` active subscribers."""
    w, model = planned
    monkeypatch.setattr(email_batch, "MAX_LIMIT", page)
    browser = await _mail_ready(w, "hr", "subscriptions")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    members = await _ordered_subscribers(w, size)
    return w, model, browser, members, await _thread(w, browser)


async def _all_drafted(w: SessionWorld) -> list[str]:
    rows = await w.q("SELECT to_addresses FROM email_drafts")
    return sorted(address for row in rows for address in row["to_addresses"])


async def _latest_resume(w: SessionWorld) -> int | None:
    rows = await w.q("SELECT result FROM console_workspace_turns ORDER BY id DESC LIMIT 1")
    return rows[0]["result"].get("resume_offset")  # type: ignore[no-any-return]


AINA_AND_SUBSCRIBERS = _batch(*_named(AINA), SUBSCRIBERS)
AINA_BODY = "Draft the reminder to Aina Example and all active subscribers"


async def _two_pages(
    w: SessionWorld, model: Any, browser: Any, thread: str, members: list[str]
) -> int:
    """Pages one and two of Aina and the active subscribers (pages of 2); returns page two's
    resume count."""
    assert (await _page_one(w, model, browser, thread, AINA_AND_SUBSCRIBERS, AINA_BODY))[
        "resume_offset"
    ] == 1
    model.respond(_copied(AINA_AND_SUBSCRIBERS))
    second = _card(await _turn(w, browser, thread, "Now the rest please", "email"))
    assert await _recipients(w, await _steps(w, second["id"])) == members[1:3]
    assert second["card"]["summary"].endswith(REST)
    resume = await _latest_resume(w)
    assert resume == 3
    return resume


async def test_the_rest_works_on_page_three_and_nobody_is_drafted_twice(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five subscribers and Aina in pages of 2: three pages. Page three's message is page two's
    "the rest", whose words name no group and no Aina: the stored group and the chain of the
    group's pages carry them."""
    w, model, browser, members, thread = await _chain_world(planned, monkeypatch, page=2, size=5)
    await _two_pages(w, model, browser, thread, members)
    model.respond(_copied(AINA_AND_SUBSCRIBERS))
    third = _card(await _turn(w, browser, thread, REST_PLEASE, "email"))
    assert await _recipients(w, await _steps(w, third["id"])) == members[3:]
    assert not third["card"]["summary"].endswith(REST)  # the group is done
    assert await _latest_resume(w) is None
    # Every person once across the three pages; Aina (named on page one) only on page one.
    assert await _all_drafted(w) == sorted([AINA[1], *members])


async def test_page_three_continues_when_the_planner_forgets_the_offset(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pages of 3: Aina, a typed address and five subscribers. Pages two and three forget the
    offset; page one's people and address trace to page one across the chain."""
    w, model, browser, members, thread = await _chain_world(planned, monkeypatch, page=3, size=5)
    batch = _batch(*_named(AINA), {"kind": "address", "value": TYPED}, SUBSCRIBERS)
    body = f"Draft the reminder to Aina Example, {TYPED} and all active subscribers"
    assert (await _page_one(w, model, browser, thread, batch, body))["resume_offset"] == 1
    model.reply({**batch, "offset": 0})
    second = _card(await _turn(w, browser, thread, "Now the rest please", "email"))
    assert await _recipients(w, await _steps(w, second["id"])) == members[1:4]
    assert await _latest_resume(w) == 4
    model.reply({**batch, "offset": 0})
    third = _card(await _turn(w, browser, thread, "Tolong draf selebihnya", "email"))
    assert await _recipients(w, await _steps(w, third["id"])) == members[4:]
    assert await _all_drafted(w) == sorted([AINA[1], TYPED, *members])


async def test_page_three_with_a_different_group_is_refused(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model, browser, members, thread = await _chain_world(planned, monkeypatch, page=2, size=5)
    resume = await _two_pages(w, model, browser, thread, members)
    other = _batch(*_named(AINA), RENEWING, offset=resume)
    await _refused(w, model, browser, thread, other, REST_PLEASE)
    third = _card(
        await _turn_with(
            w, model, browser, thread, {**AINA_AND_SUBSCRIBERS, "offset": resume}, REST_PLEASE
        )
    )
    assert await _recipients(w, await _steps(w, third["id"])) == members[3:]


async def test_page_three_with_a_person_no_page_named_is_refused(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, model, browser, members, thread = await _chain_world(planned, monkeypatch, page=2, size=5)
    resume = await _two_pages(w, model, browser, thread, members)
    # Bakar alone: a second name would be over the page of 2 (the split answer, not M7).
    added = _batch(*_named(BAKAR), SUBSCRIBERS, offset=resume)
    await _refused(w, model, browser, thread, added, REST_PLEASE)
    await _refused(w, model, browser, thread, added, "The rest please, and Bakar Example too")
    forgot = _batch(*_named(BAKAR), SUBSCRIBERS)
    await _refused(w, model, browser, thread, forgot, REST_PLEASE)
    third = _card(
        await _turn_with(
            w, model, browser, thread, {**AINA_AND_SUBSCRIBERS, "offset": resume}, REST_PLEASE
        )
    )
    assert await _recipients(w, await _steps(w, third["id"])) == members[3:]


async def test_a_chain_past_the_history_window_refuses_names_but_continues_the_group(
    planned, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A history of two turns: on page four page one is out of sight, so Aina cannot be traced
    (fails closed), while a group-only "the rest" still continues through the stored group."""
    monkeypatch.setattr("csa.workspace.service.HISTORY_TURNS", 2)
    w, model, browser, members, thread = await _chain_world(planned, monkeypatch, page=2, size=7)
    await _two_pages(w, model, browser, thread, members)
    model.respond(_copied(AINA_AND_SUBSCRIBERS))  # page one is still in sight on page three
    third = _card(await _turn(w, browser, thread, REST_PLEASE, "email"))
    assert await _recipients(w, await _steps(w, third["id"])) == members[3:5]
    resume = await _latest_resume(w)
    assert resume == 5
    named = {**AINA_AND_SUBSCRIBERS, "offset": resume}
    await _refused(w, model, browser, thread, named, REST_PLEASE)
    fourth = _card(
        await _turn_with(w, model, browser, thread, _batch(SUBSCRIBERS, offset=resume), REST_PLEASE)
    )
    assert await _recipients(w, await _steps(w, fourth["id"])) == members[5:]
    assert await _all_drafted(w) == sorted([AINA[1], *members])


# ------------------------- final fix wave: "more" or "lagi" is a fresh request, not "the rest"

SUBSCRIBERS_BODY = "Draft the reminder to all active subscribers"
CONTINUED = {
    "en": " Continuing the group after 2 already offered.",
    "ms": " Menyambung kumpulan selepas 2 yang sudah ditawarkan.",
}


@pytest.mark.parametrize(
    "body",
    [
        "Send more details to all active subscribers",
        "Tolong draf emel kepada semua pelanggan aktif, jangan hantar lagi",
    ],
)
async def test_an_everyday_more_or_lagi_offers_page_one_again(
    planned, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """After a page that stored a count, a fresh request for the same group with an everyday
    "more" or "lagi" stays at offset 0: page one is offered again, never silently skipped."""
    w, model, browser, members, thread = await _chain_world(planned, monkeypatch, page=2, size=3)
    first = await _page_one(w, model, browser, thread, _batch(SUBSCRIBERS), SUBSCRIBERS_BODY)
    assert first["resume_offset"] == 2
    again = _card(await _turn_with(w, model, browser, thread, _batch(SUBSCRIBERS), body))
    assert await _recipients(w, await _steps(w, again["id"])) == members[:2]
    assert not any(note in again["card"]["summary"] for note in CONTINUED.values())


@pytest.mark.parametrize(
    ("body", "language"),
    [
        ("Now send the same reminder to the rest of them please", "en"),
        ("Tolong draf yang selebihnya", "ms"),
    ],
)
async def test_a_continued_page_says_so_on_its_card(
    planned, monkeypatch: pytest.MonkeyPatch, body: str, language: str
) -> None:
    """ "The rest of them" and "yang selebihnya" continue (the planner forgot the offset) and the
    card says it continues, with a count only; page one's card does not."""
    w, model, browser, members, thread = await _chain_world(planned, monkeypatch, page=2, size=3)
    model.reply(_batch(SUBSCRIBERS))
    first = _card(await _turn(w, browser, thread, SUBSCRIBERS_BODY, "email"))
    assert not any(note in first["card"]["summary"] for note in CONTINUED.values())
    rest = _card(await _turn_with(w, model, browser, thread, _batch(SUBSCRIBERS), body))
    assert await _recipients(w, await _steps(w, rest["id"])) == members[2:]
    assert CONTINUED[language] in rest["card"]["summary"]


# --------------------- final fix wave: a plan's export step checks the draft version it read


async def test_a_plan_export_step_whose_draft_was_edited_is_skipped_with_no_gmail_call(
    planned, proposals_app
) -> None:
    """A plan binds only its stored step versions, so the export executor must hand the step's
    draft version to ``export_draft``: a draft edited after the card is refused there
    (``draft_changed``, shown as changed) and nothing of it reaches Gmail."""
    w, model = planned
    _, gmail = proposals_app
    gmail.expected_thread_id, gmail.expected_to = None, AINA[1]  # a new email, to Aina only
    browser = await _mail_ready(w, "hr")
    _clean_drafter(w)
    await _employee(w, AINA)
    await _employee(w, BAKAR)
    thread = await _thread(w, browser)
    model.reply(_batch(*_named(AINA, BAKAR)))
    body = "Draft an email to Aina Example and Bakar Example that we close at 5pm"
    proposal = _card(await _turn(w, browser, thread, body, "email"))
    steps = await _steps(w, proposal["id"])
    assert [step["kind"] for step in steps] == ["email_export"] * 2
    edited_id = UUID(str(steps[1]["payload"]["draft_id"]))
    (draft,) = await w.q("SELECT to_addresses, subject FROM email_drafts WHERE id=%s", edited_id)
    edited = await browser.put(
        f"/console/v1/email/drafts/{edited_id}",
        json={"to": draft["to_addresses"], "subject": draft["subject"], "body": "Edited later."},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )
    assert edited.status_code == 200, edited.text
    done = await w.mutate(browser, f"{PROPOSALS}/{proposal['id']}/confirm")
    assert done.status_code == 200, done.text
    outcomes = [(step["status"], step.get("code")) for step in done.json()["result"]["steps"]]
    assert outcomes == [("done", None), ("skipped", "changed")]
    exports = [path for method, path, _ in gmail.requests if method == "POST"]
    assert len([path for path in exports if path.endswith("/drafts")]) == 1
    rows = await w.q("SELECT status FROM email_drafts WHERE id=%s", edited_id)
    assert rows == [{"status": "draft"}]
