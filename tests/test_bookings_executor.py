"""The staff assistant books, moves and cancels as a card to confirm, and reads free times.

The planner is scripted (as in tests/test_workspace_planner.py). Every name, time, right and
version is resolved and checked by the server, through the same service paths the Bookings tab
uses, so these tests also pin that a rule the tab has is one the assistant has.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest
from csa.bookings.api import build_bookings_router
from csa.bookings.bot import format_slot
from csa.bookings.executor import BookingActionExecutor
from csa.console.mailer import Mailer
from fastapi import HTTPException

from tests.conftest import requires_docker
from tests.test_bookings_api import (
    booked,
    meeting,
    person_on_leave,
    soon_tuesday,
)
from tests.test_bookings_mail import SENDER, FakeTransport
from tests.test_bookings_setup import BASE, Team, make_person, make_resource, make_type
from tests.test_console_sessions import SessionWorld
from tests.test_customer_replies import activate_only
from tests.test_workspace_planner import (  # noqa: F401 - fixture import
    PROPOSALS,
    THREADS,
    _proposal_count,
    _thread,
    _turn,
    planned,  # noqa: F811
)
from tests.test_workspace_registry import proposals_app  # noqa: F401 - fixture import

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

DAY = soon_tuesday()
SLOT_TAKEN = "That time was just taken."
NOT_AVAILABLE = "Not available that day."


def at(hour: int, minute: int = 0, day: str = DAY) -> str:
    return f"{day}T{hour:02d}:{minute:02d}:00Z"


def when(hour: int, minute: int = 0, day: str = DAY) -> str:
    """A time as the card writes it: the company's zone is UTC in these tests."""
    return format_slot(datetime.fromisoformat(at(hour, minute, day)), ZoneInfo("UTC"))


@pytest.fixture(autouse=True)
async def bookings_on(planned) -> None:  # noqa: F811
    w, _ = planned
    w.app.include_router(build_bookings_router())
    for tenant in w.tenants:
        await activate_only(w, tenant, "bookings")
        await w.q("UPDATE clients SET timezone = 'UTC' WHERE id = %s", tenant)


@pytest.fixture
async def team(planned) -> Team:  # noqa: F811
    w, _ = planned
    return await Team(w).open()


def plan(**fields: Any) -> dict[str, Any]:
    return {"action": "booking_action", **fields}


def sync(**fields: Any) -> dict[str, Any]:
    """A meeting of 30 minutes on DAY at 15:00 unless said otherwise."""
    base = {"change": "book", "title": "Weekly sync", "minutes": 30, "day": DAY, "time": "15:00"}
    return plan(**{**base, **fields})


async def say(
    w: SessionWorld,
    model: Any,
    who: httpx.AsyncClient,
    proposal: dict[str, Any],
    body: str = "Book it for me",
) -> httpx.Response:
    model.reply(proposal)
    return await _turn(w, who, await _thread(w, who), body, "bookings")


def card_of(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "proposal", result
    assert result["proposal"]["kind"] == "booking_action"
    return result["proposal"]  # type: ignore[no-any-return]


def answer_of(response: httpx.Response) -> str:
    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["action"] == "answer", result
    return result["markdown"]  # type: ignore[no-any-return]


def fields_of(proposal: dict[str, Any]) -> dict[str, str]:
    return {f["label"]: f["value"] for f in proposal["card"]["fields"]}


async def confirm(w: SessionWorld, who: httpx.AsyncClient, proposal: dict[str, Any]) -> Any:
    return await w.mutate(who, f"{PROPOSALS}/{proposal['id']}/confirm")


async def bookings(w: SessionWorld) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id, status, source, created_by, starts_at, ends_at, version, contact_id"
        " FROM bookings WHERE client_id = %s ORDER BY created_at",
        w.tenants[0],
    )


async def contact(w: SessionWorld, name: str, email: str | None = None) -> str:
    made = await w.q(
        "INSERT INTO contacts (client_id, full_name, email) VALUES (%s, %s, %s) RETURNING id",
        w.tenants[0],
        name,
        email,
    )
    return str(made[0]["id"])


# --- booking ----------------------------------------------------------------------------------


async def test_book_card_then_confirm_books_as_the_assistant(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    await make_resource(team, "Room A")
    response = await say(w, model, team.staff, sync(people=["Aina"], rooms=["Room A"]))
    card = card_of(response)
    assert card["card"]["title"] == "Book Weekly sync"
    assert fields_of(card) == {
        "What": "Weekly sync",
        "When": when(15),
        "Length": "30 minutes",
        "With": "Aina Salleh",
        "Room": "Room A",
    }
    assert card["card"]["language"] == "en"
    assert card["card"]["expected_version"].startswith("book;ack=;people=")
    assert await bookings(w) == []  # nothing is booked until Confirm
    done = await confirm(w, team.staff, card)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed"
    receipt = done.json()["result"]
    assert receipt["change"] == "book" and receipt["status"] == "confirmed"
    (made,) = await bookings(w)
    assert (
        made["source"] == "assistant" and str(made["created_by"]) == team.staff_info["user"]["id"]
    )
    assert made["starts_at"].isoformat() == f"{DAY}T15:00:00+00:00"
    holds = await w.q("SELECT subject_kind FROM booking_holds WHERE booking_id = %s", made["id"])
    assert sorted(h["subject_kind"] for h in holds) == ["person", "resource"]
    assert str(made["id"]) == receipt["booking_id"] and aina["id"]
    audit = await w.q("SELECT 1 FROM console_audit WHERE action = 'bookings.booking.created'")
    assert len(audit) == 1
    # Confirming again is a replay: the stored receipt, and no second booking.
    again = await confirm(w, team.staff, card)
    assert again.status_code == 200 and again.json() == done.json()
    assert len(await bookings(w)) == 1


async def test_a_type_booking_shows_the_place_and_customer_and_mails_the_customer(
    planned,  # noqa: F811
    team: Team,
) -> None:
    w, model = planned
    transport = FakeTransport()
    w.app.state.mailer = Mailer(transport, SENDER)
    await make_person(team, "Aina Salleh")
    await make_type(
        team, name="Haircut", location_kind="in_person", address="12 Jalan Contoh, Kuala Lumpur"
    )
    await contact(w, "Mei Ling", "mei.ling@example.test")
    ask = plan(
        change="book", booking_type="haircut", people=["Aina"], customer="Mei Ling", day=DAY,
        time="11:00",
    )  # fmt: skip
    card = card_of(await say(w, model, team.staff, ask))
    assert card["card"]["title"] == "Book Haircut"
    assert fields_of(card) == {
        "What": "Haircut",
        "When": when(11),
        "Length": "30 minutes",
        "With": "Aina Salleh",
        "Where": "12 Jalan Contoh, Kuala Lumpur",
        "Customer": "Mei Ling",
    }
    assert "example.test" not in json.dumps(card)  # no address on a card
    done = await confirm(w, team.staff, card)
    assert done.status_code == 200, done.text
    assert done.json()["result"]["email"] == "sent"
    assert transport.to() == ["mei.ling@example.test"]  # the tab's own mail step
    (made,) = await bookings(w)
    assert made["source"] == "assistant" and made["contact_id"] is not None


async def test_the_card_is_in_malay_when_the_user_writes_malay(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    await make_person(team, "Aina Salleh")
    await make_resource(team, "Room A")
    body = "Tolong tempah bilik A esok pukul 3 petang dengan Aina untuk mesyuarat"
    card = card_of(await say(w, model, team.staff, sync(people=["Aina"], rooms=["Room A"]), body))
    assert card["card"]["language"] == "ms"
    assert card["card"]["title"] == "Tempah Weekly sync"
    assert fields_of(card) == {
        "Apa": "Weekly sync",
        "Bila": when(15),
        "Tempoh": "30 minit",
        "Bersama": "Aina Salleh",
        "Bilik": "Room A",
    }


async def test_missing_or_ambiguous_details_ask_and_store_nothing(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    first = await make_person(team, "Aina Salleh")
    second = await make_person(team, "Aina Tan")
    await make_type(team, name="Haircut")
    thread = await _thread(w, team.staff)

    async def ask(proposal: dict[str, Any]) -> dict[str, Any]:
        model.reply(proposal)
        response = await _turn(w, team.staff, thread, "Book something", "bookings")
        assert response.status_code == 201, response.text
        return response.json()["result"]  # type: ignore[no-any-return]

    no_time = await ask(plan(change="book", title="Sync", minutes=30, people=["Aina Tan"]))
    assert no_time["action"] == "clarification" and "tomorrow at 3 pm" in no_time["question"]
    nothing = await ask(plan(change="book", day=DAY, time="15:00", people=["Aina Tan"]))
    assert nothing["action"] == "clarification"
    assert [o["label"] for o in nothing["options"]] == ["Haircut"]
    no_length = await ask(plan(change="book", title="Sync", day=DAY, time="15:00"))
    assert no_length["action"] == "clarification" and "How long" in no_length["question"]
    nobody = await ask(sync())
    assert nobody["action"] == "clarification" and "Who is it with" in nobody["question"]
    which = await ask(sync(people=["Aina"]))
    assert which["action"] == "clarification" and which["question"] == "Which Aina do you mean?"
    assert {o["value"] for o in which["options"]} == {first["id"], second["id"]}
    assert all(len(o["label"]) <= 100 and "@" not in o["label"] for o in which["options"])
    assert await _proposal_count(w) == 0
    # The picked option's id is the name: the user chose, so one card follows.
    picked = await ask(sync(people=[second["id"]]))
    assert picked["action"] == "proposal"
    assert fields_of(picked["proposal"])["With"] == "Aina Tan"
    unknown = await ask(sync(people=["Zulkifli"]))
    assert unknown["action"] == "answer" and "Zulkifli" in unknown["markdown"]
    assert await _proposal_count(w) == 1


async def test_hours_and_a_closed_day_show_as_a_warning_and_confirming_acknowledges(
    planned,  # noqa: F811
    team: Team,
) -> None:
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    late = card_of(await say(w, model, team.staff, sync(people=["Aina"], time="19:00")))
    assert fields_of(late)["Heads up"] == "Outside working hours."
    assert late["card"]["expected_version"].startswith("book;ack=outside_hours;")
    done = await confirm(w, team.staff, late)
    assert done.status_code == 200, done.text
    assert len(await bookings(w)) == 1
    closed = await team.send(team.admin, "/closed-days", {"day": DAY})
    assert closed.status_code == 201, closed.text
    both = card_of(await say(w, model, team.staff, sync(people=["Aina"], time="20:00")))
    assert fields_of(both)["Heads up"] == "This is a closed day. Outside working hours."
    # A warning that was not on the card makes it stale: the user saw only what they confirm.
    plain_day = "2031-03-11"
    model_day = card_of(
        await say(w, model, team.staff, sync(people=["Aina"], day=plain_day, time="10:00"))
    )
    assert "Heads up" not in fields_of(model_day)
    added = await team.send(team.admin, "/closed-days", {"day": plain_day})
    assert added.status_code == 201, added.text
    stale = await confirm(w, team.staff, model_day)
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    assert len(await bookings(w)) == 1 and aina["id"]


@pytest.mark.parametrize("who", ["staff", "admin"])
async def test_leave_is_a_plain_refusal_and_never_a_card(planned, team: Team, who: str) -> None:  # noqa: F811
    w, model = planned
    away = await person_on_leave(team, DAY, DAY)
    browser = team.staff if who == "staff" else team.admin
    response = await say(w, model, browser, sync(people=[away["display_name"]], time="10:00"))
    text = answer_of(response)
    assert text == NOT_AVAILABLE
    assert "leave" not in response.text.lower() and "annual" not in response.text.lower()
    assert await _proposal_count(w) == 0 and await bookings(w) == []


async def test_a_time_taken_before_confirm_says_so_and_books_nothing_more(
    planned,  # noqa: F811
    team: Team,
) -> None:
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    card = card_of(await say(w, model, team.staff, sync(people=["Aina"], time="10:00")))
    taken = await team.send(
        team.admin, "/bookings", meeting(aina, start=at(10), title="Rival", minutes=30)
    )
    assert taken.status_code == 201, taken.text
    late = await confirm(w, team.staff, card)
    assert late.status_code == 409, late.text
    assert late.json()["detail"] == "slot_taken" and late.json()["message"] == SLOT_TAKEN
    assert len(await bookings(w)) == 1
    # Asked for afresh, the same time is a plain sentence and no card.
    assert answer_of(await say(w, model, team.staff, sync(people=["Aina"], time="10:00"))) == (
        SLOT_TAKEN
    )


async def test_the_database_has_the_last_word_when_the_slot_goes_at_execute(
    planned,  # noqa: F811
    team: Team,
) -> None:
    """A booking that lands between the card and the write is still `slot_taken`, in the
    assistant's own words."""
    w, _ = planned
    aina = await make_person(team, "Aina Salleh")
    await booked(team, team.admin, meeting(aina, start=at(10)))
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=w.pool)))
    actor = _actor(team.staff_info)
    payload = {"change": "book", "title": "Late", "minutes": 30, "starts_at": at(10),
               "people": [aina["id"]]}  # fmt: skip
    with pytest.raises(Exception) as caught:
        await BookingActionExecutor().execute(
            request,
            actor,
            UUID(w.tenants[0]),
            payload,
            proposal_id=uuid.uuid4(),
            expected_version=f"book;ack=;people={aina['id']};rooms=",
            origin="assistant",
        )
    assert getattr(caught.value, "content", {}).get("message") == SLOT_TAKEN


def _actor(me: dict[str, Any]) -> Any:
    from csa.console.contracts import ConsoleActor, MembershipOut, Role

    user = me["user"]
    return ConsoleActor(
        user_id=UUID(user["id"]),
        email=user["email"],
        display_name=user["display_name"],
        platform_admin=user["is_platform_admin"],
        must_change_password=user["must_change_password"],
        session_id=uuid.uuid4(),
        client_id=UUID(me["active_client_id"]),
        role=Role(me["role"]),
        memberships=[MembershipOut.model_validate(m) for m in me["memberships"]],
        csrf_hash="synthetic",
    )


async def test_executing_a_proposal_twice_books_once(planned, team: Team) -> None:  # noqa: F811
    w, _ = planned
    aina = await make_person(team, "Aina Salleh")
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=w.pool)))
    actor, proposal = _actor(team.staff_info), uuid.uuid4()
    payload = {"change": "book", "title": "Once", "minutes": 30, "starts_at": at(13),
               "people": [aina["id"]]}  # fmt: skip
    version = f"book;ack=;people={aina['id']};rooms="

    async def run() -> dict[str, Any]:
        return await BookingActionExecutor().execute(
            request, actor, UUID(w.tenants[0]), payload,
            proposal_id=proposal, expected_version=version, origin="assistant",
        )  # fmt: skip

    first = await run()
    second = await run()
    # The replay is the receipt without the mail outcome: it was sent once, by the first run.
    assert first["change"] == "book" and first["email"] == "none"
    assert second == {k: v for k, v in first.items() if k not in ("email", "customer_told")}
    assert len(await bookings(w)) == 1


# --- who may ---------------------------------------------------------------------------------


async def test_read_only_members_get_no_card(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    await make_person(team, "Aina Salleh")
    # The dock is closed to them: no thread, so no turn.
    thread = await w.mutate(team.reader, THREADS, {"request_key": str(uuid.uuid4())})
    assert thread.status_code == 403, thread.text
    # The generic card route refuses them too, and so does the executor itself.
    made = await w.mutate(
        team.reader,
        PROPOSALS,
        {
            "kind": "booking_action",
            "payload": {"change": "cancel", "booking_id": str(uuid.uuid4())},
        },
    )
    assert made.status_code == 403, made.text
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=w.pool)))
    with pytest.raises(HTTPException) as refused:
        await BookingActionExecutor().gate(
            request, _actor(team.reader_info), UUID(w.tenants[0]), origin="assistant"
        )
    assert refused.value.status_code == 403
    assert await _proposal_count(w) == 0 and model.replies == []


async def test_staff_change_only_their_own_or_attended_bookings(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    other = await make_person(team, "Siti Aminah")
    theirs = await booked(team, team.admin, meeting(aina, start=at(10)))
    mine = await booked(team, team.staff, meeting(other, start=at(14)))
    move_theirs = plan(change="move", booking_id=theirs["id"], time="11:00")
    denied = await say(w, model, team.staff, move_theirs, "Move that booking")
    assert answer_of(denied) == "I couldn't find that booking."
    by_words = plan(change="cancel", on_day=DAY, at_time="10:00", about="Aina")
    assert answer_of(await say(w, model, team.staff, by_words)) == "I couldn't find that booking."
    assert await _proposal_count(w) == 0
    # An Admin may change both; staff change the booking they made.
    assert card_of(await say(w, model, team.admin, move_theirs))["card"]["title"]
    own = card_of(await say(w, model, team.staff, plan(change="cancel", booking_id=mine["id"])))
    assert own["card"]["title"] == "Cancel Weekly sync"
    # And at Confirm, whatever a stale or forged card says, the tab's own rule holds.
    forged = await w.mutate(
        team.staff,
        PROPOSALS,
        {"kind": "booking_action", "payload": {"change": "cancel", "booking_id": theirs["id"]}},
    )
    assert forged.status_code == 403 and forged.json()["detail"] == "own_bookings_only"


async def test_a_platform_admin_acts_only_for_the_chosen_company(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    mine, theirs = w.tenants
    await make_resource(team, "Room A")  # in the first company only
    other = await w.mutate(w.admin, f"{BASE}/resources?client_id={theirs}", {"name": "Room B"})
    assert other.status_code == 201, other.text

    async def turn(proposal: dict[str, Any]) -> httpx.Response:
        model.reply(proposal)
        thread = await w.mutate(
            w.admin, f"{THREADS}?client_id={theirs}", {"request_key": str(uuid.uuid4())}
        )
        return await w.mutate(
            w.admin,
            f"{THREADS}/{thread.json()['id']}/turns?client_id={theirs}",
            {"request_key": str(uuid.uuid4()), "body": "Book a room",
             "page_context": {"tab": "bookings"}},
        )  # fmt: skip

    elsewhere = answer_of(await turn(sync(rooms=["Room A"])))
    assert "Room A" in elsewhere and "couldn't find a room" in elsewhere
    card = card_of(await turn(sync(rooms=["Room B"])))
    done = await w.mutate(w.admin, f"{PROPOSALS}/{card['id']}/confirm?client_id={theirs}")
    assert done.status_code == 200, done.text
    rows = await w.q("SELECT client_id FROM bookings")
    assert [str(r["client_id"]) for r in rows] == [theirs] and mine != theirs


async def test_the_assistant_offers_bookings_only_while_the_tab_is_open(
    planned, team: Team
) -> None:  # noqa: F811
    w, model = planned
    await make_person(team, "Aina Salleh")
    model.reply({"action": "answer", "markdown": "Fine."})
    open_ = await _turn(
        w, team.staff, await _thread(w, team.staff), "Can you book a room?", "overview"
    )
    assert open_.status_code == 201, open_.text
    facts = model.facts()
    assert facts["bookings"]["timezone"] == "UTC" and facts["bookings"]["can_change"] is True
    assert len(facts["bookings"]["now_local"]) == 16
    model.reply({"action": "answer", "markdown": "Hello."})
    other = await _turn(w, team.staff, await _thread(w, team.staff), "Say hello", "overview")
    assert other.status_code == 201 and model.facts()["bookings"] is None
    await w.q("UPDATE console_service_packages SET status = 'suspended' WHERE package = 'bookings'")
    model.reply(sync(people=["Aina"]))
    closed = await _turn(w, team.staff, await _thread(w, team.staff), "Book Aina", "overview")
    assert "not part of this workspace's active services" in answer_of(closed)
    assert model.facts()["bookings"] is None and await _proposal_count(w) == 0


# --- move and cancel -------------------------------------------------------------------------


async def test_move_card_then_confirm_moves_and_a_changed_booking_is_stale(
    planned,  # noqa: F811
    team: Team,
) -> None:
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    one = await booked(team, team.staff, meeting(aina, start=at(10)))
    ask = plan(change="move", on_day=DAY, at_time="10:00", about="Aina", time="14:00")
    card = card_of(await say(w, model, team.staff, ask, "Move my 10 am with Aina to 2 pm"))
    assert card["card"]["title"] == "Move Weekly sync"
    assert fields_of(card) == {
        "What": "Weekly sync",
        "From": when(10),
        "To": when(14),
        "With": "Aina Salleh",
    }
    assert card["card"]["expected_version"] == f"{one['version']};ack="
    done = await confirm(w, team.staff, card)
    assert done.status_code == 200, done.text
    assert done.json()["result"]["change"] == "move"
    (row,) = await bookings(w)
    assert row["starts_at"].isoformat() == f"{DAY}T14:00:00+00:00"
    assert row["version"] == one["version"] + 1 and row["source"] == "staff"
    # A card made from a version that has since changed is refused, and nothing moves.
    second = card_of(
        await say(w, model, team.staff, plan(change="move", booking_id=one["id"], time="16:00"))
    )
    change = {"version": row["version"], "starts_at": at(12)}
    moved = await w.mutate(team.admin, f"{BASE}/bookings/{one['id']}", change, method="PATCH")
    assert moved.status_code == 200, moved.text
    stale = await confirm(w, team.staff, second)
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    assert (await bookings(w))[0]["starts_at"].isoformat() == f"{DAY}T12:00:00+00:00"


async def test_a_move_that_needs_a_second_look_binds_its_warning(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    one = await booked(team, team.staff, meeting(aina, start=at(10)))
    card = card_of(
        await say(w, model, team.staff, plan(change="move", booking_id=one["id"], time="19:00"))
    )
    assert fields_of(card)["Heads up"] == "Outside working hours."
    assert card["card"]["expected_version"] == f"{one['version']};ack=outside_hours"
    assert (await confirm(w, team.staff, card)).status_code == 200


async def test_several_matches_ask_which_and_the_picked_id_is_the_booking(
    planned,  # noqa: F811
    team: Team,
) -> None:
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    first = await booked(team, team.staff, meeting(aina, start=at(10)))
    second = await booked(team, team.staff, meeting(aina, start=at(14)))
    asked = await say(w, model, team.staff, plan(change="cancel", on_day=DAY, about="Aina"))
    result = asked.json()["result"]
    assert result["action"] == "clarification"
    assert [o["value"] for o in result["options"]] == [first["id"], second["id"]]
    assert all("Weekly sync" in o["label"] and len(o["label"]) <= 100 for o in result["options"])
    picked = card_of(
        await say(w, model, team.staff, plan(change="cancel", booking_id=second["id"]))
    )
    assert fields_of(picked)["When"] == when(14)


async def test_cancel_card_then_confirm_frees_the_time_and_replays(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    await make_resource(team, "Room A")
    one = await booked(team, team.staff, meeting(aina, start=at(10)))
    card = card_of(await say(w, model, team.staff, plan(change="cancel", booking_id=one["id"])))
    assert card["card"]["title"] == "Cancel Weekly sync"
    assert card["card"]["expected_version"] == str(one["version"])
    done = await confirm(w, team.staff, card)
    assert done.status_code == 200, done.text
    assert done.json()["result"]["status"] == "cancelled"
    assert (await bookings(w))[0]["status"] == "cancelled"
    assert await w.q("SELECT 1 FROM booking_holds WHERE booking_id = %s", one["id"]) == []
    again = await confirm(w, team.staff, card)
    assert again.status_code == 200 and again.json() == done.json()
    gone = await say(w, model, team.staff, plan(change="cancel", booking_id=one["id"]))
    assert answer_of(gone) == "I couldn't find that booking."


# --- private meetings, and free times --------------------------------------------------------


async def test_a_private_meeting_of_someone_else_is_never_found_or_shown(
    planned, team: Team
) -> None:  # noqa: F811
    w, model = planned
    aina = await make_person(team, "Aina Salleh")
    secret = await booked(
        team, team.admin, meeting(aina, start=at(10), minutes=60, title="Secret plan", private=True)
    )
    for ask in (
        plan(change="cancel", booking_id=secret["id"]),
        plan(change="cancel", on_day=DAY, about="Secret"),
        plan(change="move", on_day=DAY, at_time="10:00", time="11:00"),
    ):
        response = await say(w, model, team.staff, ask)
        assert answer_of(response) == "I couldn't find that booking."
        assert "Secret" not in response.text.replace("I couldn't", "")
    # Its own creator may cancel it, and the card (readable company-wide) says nothing more.
    own = card_of(await say(w, model, team.admin, plan(change="cancel", booking_id=secret["id"])))
    assert fields_of(own) == {"What": "Private meeting", "When": when(10)}
    assert "Secret plan" not in json.dumps(own)
    # A free-times answer treats it as busy, whatever it is.
    model.reply(
        {
            "action": "answer",
            "markdown": "Aina's free times:",
            "reads": ["booking_availability"],
            "availability": {"person": "Aina", "day": DAY},
        }
    )
    free = await _turn(
        w, team.staff, await _thread(w, team.staff), "When is Aina free?", "bookings"
    )
    text = answer_of(free)
    assert f"Aina Salleh is free from {when(9)} to 10:00 am." in text
    assert f"Aina Salleh is free from {when(11)} to 6:00 pm." in text
    assert "Secret" not in text and "Busy" not in text and "private" not in text.lower()


async def test_free_times_come_from_the_rules_and_never_say_why_someone_is_busy(
    planned,  # noqa: F811
    team: Team,
) -> None:
    w, model = planned
    away = await person_on_leave(team, DAY, DAY)
    await make_resource(team, "Room A")

    async def free(who: dict[str, Any], body: str = "When is she free?") -> str:
        model.reply(
            {
                "action": "answer",
                "markdown": "Here you go:",
                "reads": ["booking_availability"],
                "availability": {**who, "day": DAY, "minutes": 60},
            }
        )
        return answer_of(await _turn(w, team.staff, await _thread(w, team.staff), body, "bookings"))

    leave = await free({"person": away["display_name"]})
    assert leave.endswith("Away Person has no free time that day.")
    assert "leave" not in leave.lower() and "annual" not in leave.lower()
    room = await free({"room": "Room A"})
    assert f"Room A is free from {when(9)} to 6:00 pm." in room
    nobody = await free({"person": "Zulkifli"})
    assert "couldn't find Zulkifli" in nobody


async def test_free_times_in_malay_use_the_turns_language(planned, team: Team) -> None:  # noqa: F811
    w, model = planned
    await make_person(team, "Aina Salleh")
    model.reply(
        {
            "action": "answer",
            "markdown": "Ini masa lapang:",
            "reads": ["booking_availability"],
            "availability": {"person": "Aina", "day": DAY},
        }
    )
    response = await _turn(
        w, team.staff, await _thread(w, team.staff), "Bila Aina ada masa kosong esok?", "bookings"
    )
    text = answer_of(response)
    assert response.json()["language"] == "ms"
    assert f"Aina Salleh lapang dari {when(9)} hingga 6:00 pm." in text


# --- the generic card route -------------------------------------------------------------------


async def test_the_card_route_makes_an_english_card_for_a_tab_origin(planned, team: Team) -> None:  # noqa: F811
    w, _ = planned
    aina = await make_person(team, "Aina Salleh")
    made = await w.mutate(
        team.staff,
        PROPOSALS,
        {
            "kind": "booking_action",
            "payload": {"change": "book", "title": "Tab", "minutes": 30, "starts_at": at(9),
                        "people": [aina["id"]]},
        },
    )  # fmt: skip
    assert made.status_code == 201, made.text
    assert made.json()["card"]["title"] == "Book Tab"
    bad = await w.mutate(
        team.staff, PROPOSALS, {"kind": "booking_action", "payload": {"change": "cancel"}}
    )
    assert bad.status_code == 422
