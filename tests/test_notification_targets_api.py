"""The notification targets and the person's email preferences API (inbox refinement part 3,
task 8): list and masking, edit with a version, add and test by email, delete, preferences, roles,
audit (disposable database, synthetic data and addresses).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from csa.console.access import reader_may_post
from csa.console.mailer import Mailer, OutgoingMail

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

SENDER = "FourStacks <no-reply@fourstacks.my>"
CHAT = "-1001234567890"
OFFERED = [
    "escalation",
    "approval_needed",
    "order_submitted",
    "dlq",
    "circuit_opened",
    "booking_pending",
    "inbox.overdue",
]
SIX = [
    "inbox.assigned",
    "inbox.overdue",
    "inbox.snooze_due",
    "inbox.stale_claim",
    "inbox.follow_up",
    "inbox.handback",
]
QUIET = {"start": "22:00", "end": "07:00", "tz": "Asia/Kuala_Lumpur"}


class FakeTransport:
    """Keeps every message; refuses the addresses in `fail`."""

    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []
        self.fail: set[str] = set()

    def send(self, mail: OutgoingMail, sender: str) -> None:
        if mail.to in self.fail:
            raise RuntimeError("refused")
        self.sent.append(mail)


@pytest.fixture
def relay(sessions: SessionWorld) -> FakeTransport:
    transport = FakeTransport()
    sessions.app.state.mailer = Mailer(transport, SENDER)
    return transport


async def member(w: SessionWorld, role: str = "tenant_staff") -> tuple[httpx.AsyncClient, Any]:
    """A signed-in member; the relay is off while inviting, so the invite shows its token."""
    mailer = getattr(w.app.state, "mailer", None)
    w.app.state.mailer = None
    try:
        return await w.member(role=role)
    finally:
        w.app.state.mailer = mailer


def path(w: SessionWorld, tail: str = "", tenant: int = 0) -> str:
    return f"/console/v1/notifications{tail}?client_id={w.tenants[tenant]}"


async def seed_target(
    w: SessionWorld,
    channel: str = "email",
    address: str | None = None,
    *,
    tenant: int = 0,
    verified: bool = True,
    flagged: bool = False,
    language: str = "en",
) -> str:
    row = await w.q(
        "INSERT INTO staff_notification_targets"
        " (client_id, channel, address, verified_at, language, failure_notified_at)"
        " VALUES (%s, %s, %s, CASE WHEN %s THEN now() END, %s, CASE WHEN %s THEN now() END)"
        " RETURNING id",
        w.tenants[tenant],
        channel,
        address or f"person-{uuid4().hex[:8]}@example.test",
        verified,
        language,
        flagged,
    )
    return str(row[0]["id"])


async def seed_dead(w: SessionWorld, target_id: str, *, tenant: int = 0, attempts: int = 5) -> None:
    await w.q(
        "INSERT INTO staff_notifications"
        " (client_id, target_id, event, payload, idempotency_key, status, attempts, last_error)"
        " VALUES (%s, %s, 'inbox.overdue', '{}'::jsonb, %s, 'dead', %s, 'smtp_refused')",
        w.tenants[tenant],
        target_id,
        f"dead-{uuid4()}",
        attempts,
    )


def put_body(version: int = 0, **changes: Any) -> dict[str, Any]:
    return {
        "events": ["escalation", "inbox.overdue"],
        "min_priority": "high",
        "quiet_hours": QUIET,
        "language": "ms",
        "is_enabled": True,
        "version": version,
    } | changes


async def listed(client: httpx.AsyncClient, w: SessionWorld, tenant: int = 0) -> dict[str, Any]:
    response = await client.get(path(w, "/targets", tenant))
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def stored(w: SessionWorld, target_id: str) -> dict[str, Any]:
    rows = await w.q("SELECT * FROM staff_notification_targets WHERE id = %s", target_id)
    return rows[0]


async def audited(w: SessionWorld, action: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT actor_id, target_id, details FROM console_audit"
        " WHERE client_id = %s AND action = %s",
        w.tenants[0],
        action,
    )


# --- list and masking -----------------------------------------------------


async def test_list_shape_and_masking(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    chat = await seed_target(w, "telegram", CHAT)
    mail = await seed_target(w, "email", "Jo.Tan@Example.test".lower(), flagged=True)
    await seed_dead(w, mail, attempts=5)
    await seed_target(w, "email", "other@example.test", tenant=1)
    staff, _ = await member(w)
    for client in (w.admin, staff):
        answer = await listed(client, w)
        assert answer["email_available"] is True
        assert answer["events_offered"] == OFFERED
        by_id = {t["id"]: t for t in answer["targets"]}
        assert set(by_id) == {chat, mail}
        telegram, email = by_id[chat], by_id[mail]
        assert telegram["address_masked"] == "7890"
        assert email["address_masked"] == "j***@example.test"
        assert telegram["channel"] == "telegram" and email["channel"] == "email"
        assert telegram["last_failure"] is None
        assert email["last_failure"]["attempts"] == 5
        assert email["last_failure"]["at"]
        assert set(email) == {
            "id",
            "channel",
            "address_masked",
            "events",
            "min_priority",
            "quiet_hours",
            "language",
            "is_enabled",
            "verified",
            "version",
            "last_failure",
        }
        assert email["verified"] is True and email["is_enabled"] is True
        assert email["version"] == 0 and email["quiet_hours"] is None
        assert "jo.tan" not in json.dumps(answer) and CHAT not in json.dumps(answer)


async def test_a_retired_telegram_chat_is_not_listed(sessions: SessionWorld) -> None:
    w = sessions
    await w.q(
        "INSERT INTO staff_notification_targets (client_id, channel, address, is_enabled,"
        " verified_at) VALUES (%s, 'telegram', '-100111', false, now() - interval '1 day')",
        w.tenants[0],
    )
    current = await seed_target(w, "telegram", CHAT)
    answer = await listed(w.admin, w)
    assert [t["id"] for t in answer["targets"]] == [current]


async def test_email_routes_503_when_mailer_disabled_but_list_works(sessions: SessionWorld) -> None:
    w = sessions
    mail = await seed_target(w, "email")
    staff, _ = await member(w)
    for mailer in (None, Mailer(None, SENDER)):
        w.app.state.mailer = mailer
        answer = await listed(w.admin, w)
        assert answer["email_available"] is False and len(answer["targets"]) == 1
        added = await w.mutate(w.admin, path(w, "/targets/email"), {"address": "new@example.test"})
        assert added.status_code == 503 and added.json()["detail"] == "email_not_configured"
        tested = await w.mutate(w.admin, path(w, f"/targets/{mail}/test"))
        assert tested.status_code == 503 and tested.json()["detail"] == "email_not_configured"
        on = await w.mutate(
            staff,
            path(w, "/preferences"),
            {"email_enabled": True, "kinds": SIX, "min_priority": "high"},
            method="PUT",
        )
        assert on.status_code == 503 and on.json()["detail"] == "email_not_configured"
    assert (
        await w.q(
            "SELECT count(*) AS n FROM staff_notification_targets WHERE client_id = %s",
            w.tenants[0],
        )
    )[0]["n"] == 1


# --- edit -----------------------------------------------------


async def test_put_target_updates_bumps_version_and_roundtrips(sessions: SessionWorld) -> None:
    w = sessions
    chat = await seed_target(w, "telegram", CHAT)
    answer = await w.mutate(
        w.admin, path(w, f"/targets/{chat}"), put_body(0, is_enabled=False), method="PUT"
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["version"] == 1 and body["is_enabled"] is False
    assert body["events"] == ["escalation", "inbox.overdue"]
    assert body["quiet_hours"] == QUIET and body["language"] == "ms"
    assert body["min_priority"] == "high" and body["address_masked"] == "7890"
    row = await stored(w, chat)
    assert row["quiet_hours"] == QUIET and row["version"] == 1 and row["is_enabled"] is False
    cleared = await w.mutate(
        w.admin, path(w, f"/targets/{chat}"), put_body(1, quiet_hours=None), method="PUT"
    )
    assert cleared.status_code == 200 and cleared.json()["quiet_hours"] is None
    assert cleared.json()["version"] == 2


async def test_put_target_stale_version_409(sessions: SessionWorld) -> None:
    w = sessions
    target = await seed_target(w)
    ok = await w.mutate(w.admin, path(w, f"/targets/{target}"), put_body(0), method="PUT")
    assert ok.status_code == 200, ok.text
    stale = await w.mutate(
        w.admin, path(w, f"/targets/{target}"), put_body(0, min_priority="low"), method="PUT"
    )
    assert stale.status_code == 409 and stale.json()["detail"] == "version_conflict"
    future = await w.mutate(w.admin, path(w, f"/targets/{target}"), put_body(9), method="PUT")
    assert future.status_code == 409
    row = await stored(w, target)
    assert row["version"] == 1 and row["min_priority"] == "high"


async def test_staff_put_403_but_get_ok(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    target = await seed_target(w)
    staff, _ = await member(w)
    reader, _ = await member(w, role="read_only")
    for client in (staff, reader):
        assert (await client.get(path(w, "/targets"))).status_code == 200
        for method, tail, body in (
            ("PUT", f"/targets/{target}", put_body(0)),
            ("POST", f"/targets/{target}/test", None),
            ("POST", "/targets/email", {"address": "new@example.test"}),
            ("DELETE", f"/targets/{target}", None),
        ):
            answer = await w.mutate(client, path(w, tail), body, method=method)
            assert answer.status_code == 403, (method, tail, answer.text)
    assert relay.sent == []
    assert (await stored(w, target))["version"] == 0


async def test_other_company_target_404(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    foreign = await seed_target(w, tenant=1)
    admin, _ = await member(w, role="tenant_admin")
    assert foreign not in {t["id"] for t in (await listed(admin, w))["targets"]}
    for method, tail, body in (
        ("PUT", f"/targets/{foreign}", put_body(0)),
        ("POST", f"/targets/{foreign}/test", None),
        ("DELETE", f"/targets/{foreign}", None),
    ):
        answer = await w.mutate(admin, path(w, tail), body, method=method)
        assert answer.status_code == 404, (method, answer.text)
        # Even a platform admin naming the wrong company cannot reach it.
        mixed = await w.mutate(w.admin, path(w, tail), body, method=method)
        assert mixed.status_code == 404, (method, mixed.text)
    assert relay.sent == []
    assert (await stored(w, foreign))["version"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"events": ["escalation", "nope"]},
        {"events": "escalation"},
        {"min_priority": "critical"},
        {"language": "fr"},
        {"quiet_hours": {"start": "22:00", "end": "22:00", "tz": "Asia/Kuala_Lumpur"}},
        {"quiet_hours": {"start": "25:00", "end": "07:00", "tz": "Asia/Kuala_Lumpur"}},
        {"quiet_hours": {"start": "22:0", "end": "07:00", "tz": "Asia/Kuala_Lumpur"}},
        {"quiet_hours": {"start": "22:00", "end": "07:00", "tz": "Mars/Olympus"}},
        {"quiet_hours": {"start": "22:00", "end": "07:00", "tz": "../etc/passwd"}},
        {"quiet_hours": {"start": "22:00", "end": "07:00"}},
        {"quiet_hours": {"start": "22:00", "end": "07:00", "tz": "UTC", "extra": 1}},
        {"is_enabled": "maybe"},
        {"version": -1},
    ],
)
async def test_invalid_quiet_hours_and_events_422(
    sessions: SessionWorld, changes: dict[str, Any]
) -> None:
    w = sessions
    target = await seed_target(w)
    answer = await w.mutate(
        w.admin, path(w, f"/targets/{target}"), {**put_body(0), **changes}, method="PUT"
    )
    assert answer.status_code == 422, answer.text
    assert (await stored(w, target))["version"] == 0


async def test_the_offered_events_are_accepted_for_either_channel(sessions: SessionWorld) -> None:
    w = sessions
    chat = await seed_target(w, "telegram", CHAT)
    answer = await w.mutate(
        w.admin, path(w, f"/targets/{chat}"), put_body(0, events=OFFERED[::-1]), method="PUT"
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["events"] == OFFERED


async def test_target_edit_clears_failure_flag(sessions: SessionWorld) -> None:
    w = sessions
    target = await seed_target(w, flagged=True)
    await seed_dead(w, target)
    assert (await listed(w.admin, w))["targets"][0]["last_failure"] is not None
    answer = await w.mutate(w.admin, path(w, f"/targets/{target}"), put_body(0), method="PUT")
    assert answer.status_code == 200 and answer.json()["last_failure"] is None
    assert (await stored(w, target))["failure_notified_at"] is None


# --- add an email target --------------------------------------------------------------------------


async def test_add_email_target_sends_test_first_and_saves_verified(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    answer = await w.mutate(
        w.admin, path(w, "/targets/email"), {"address": "  Jo.Tan@Example.TEST ", "language": "ms"}
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    assert body["channel"] == "email" and body["address_masked"] == "j***@example.test"
    assert body["is_enabled"] is True and body["verified"] is True
    assert body["events"] == OFFERED and body["language"] == "ms"
    assert body["min_priority"] == "normal" and body["quiet_hours"] is None
    assert body["version"] == 0 and body["last_failure"] is None
    assert [mail.to for mail in relay.sent] == ["jo.tan@example.test"]
    assert relay.sent[0].subject and relay.sent[0].text and relay.sent[0].html
    row = await stored(w, body["id"])
    assert row["address"] == "jo.tan@example.test"
    assert row["is_enabled"] is True and row["verified_at"] is not None
    assert "inbox.overdue" in row["events"] and "booking_pending" in row["events"]


async def test_add_email_target_with_settings(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    answer = await w.mutate(
        w.admin,
        path(w, "/targets/email"),
        {
            "address": "desk@example.test",
            "events": ["inbox.overdue"],
            "min_priority": "urgent",
            "quiet_hours": QUIET,
        },
    )
    assert answer.status_code == 201, answer.text
    assert answer.json()["events"] == ["inbox.overdue"]
    assert answer.json()["min_priority"] == "urgent" and answer.json()["quiet_hours"] == QUIET


async def test_add_email_relay_failure_saves_nothing_502(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    relay.fail.add("bad@example.test")
    answer = await w.mutate(w.admin, path(w, "/targets/email"), {"address": "bad@example.test"})
    assert answer.status_code == 502 and answer.json()["detail"] == "email_test_failed"
    assert (
        await w.q(
            "SELECT count(*) AS n FROM staff_notification_targets WHERE client_id = %s",
            w.tenants[0],
        )
    )[0]["n"] == 0
    assert await audited(w, "notifications.target.added") == []


@pytest.mark.parametrize(
    "address",
    [
        "",
        "nobody",
        "a@b",
        "two words@example.test",
        "a@example.test\nBcc: x@example.test",
        "a@@example.test",
        "<a@example.test>",
        "a@example.test,b@example.test",
        "a@-example.test",
        "x" * 250 + "@example.test",
    ],
)
async def test_add_email_rejects_a_bad_address_422(
    sessions: SessionWorld, relay: FakeTransport, address: str
) -> None:
    w = sessions
    answer = await w.mutate(w.admin, path(w, "/targets/email"), {"address": address})
    assert answer.status_code == 422, answer.text
    assert relay.sent == []


async def test_duplicate_address_409(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    first = await w.mutate(w.admin, path(w, "/targets/email"), {"address": "dup@example.test"})
    assert first.status_code == 201, first.text
    again = await w.mutate(w.admin, path(w, "/targets/email"), {"address": "DUP@example.test"})
    assert again.status_code == 409 and again.json()["detail"] == "duplicate_address"
    assert len(relay.sent) == 1  # the duplicate is refused before a second mail goes out
    other_company = await w.mutate(
        w.admin, path(w, "/targets/email", 1), {"address": "dup@example.test"}
    )
    assert other_company.status_code == 201, other_company.text


async def test_eleventh_email_target_409(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    for number in range(10):
        await seed_target(w, "email", f"seed{number}@example.test")
    await seed_target(w, "telegram", CHAT)  # a chat does not count
    answer = await w.mutate(w.admin, path(w, "/targets/email"), {"address": "eleven@example.test"})
    assert answer.status_code == 409 and answer.json()["detail"] == "too_many_email_targets"
    assert relay.sent == []
    # Another company has its own ten.
    other = await w.mutate(w.admin, path(w, "/targets/email", 1), {"address": "one@example.test"})
    assert other.status_code == 201, other.text


# --- test and delete ------------------------------------------------------------------------------


async def test_test_endpoint_verifies_and_clears_failure(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    target = await seed_target(w, "email", "check@example.test", verified=False, flagged=True)
    await seed_dead(w, target)
    answer = await w.mutate(w.admin, path(w, f"/targets/{target}/test"))
    assert answer.status_code == 200, answer.text
    assert answer.json()["verified"] is True and answer.json()["last_failure"] is None
    assert [mail.to for mail in relay.sent] == ["check@example.test"]
    row = await stored(w, target)
    assert row["verified_at"] is not None and row["failure_notified_at"] is None


async def test_test_endpoint_failure_changes_nothing(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    target = await seed_target(w, "email", "down@example.test", verified=False, flagged=True)
    relay.fail.add("down@example.test")
    answer = await w.mutate(w.admin, path(w, f"/targets/{target}/test"))
    assert answer.status_code == 502 and answer.json()["detail"] == "email_test_failed"
    row = await stored(w, target)
    assert row["verified_at"] is None and row["failure_notified_at"] is not None
    assert await audited(w, "notifications.target.tested") == []


async def test_test_endpoint_telegram_400(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    chat = await seed_target(w, "telegram", CHAT)
    answer = await w.mutate(w.admin, path(w, f"/targets/{chat}/test"))
    assert answer.status_code == 400 and answer.json()["detail"] == "test_not_supported"
    assert relay.sent == []


async def test_delete_email_ok_telegram_400(sessions: SessionWorld) -> None:
    w = sessions
    chat = await seed_target(w, "telegram", CHAT)
    mail = await seed_target(w, "email")
    await seed_dead(w, mail)  # a notice pointing at it does not stop the removal
    refused = await w.mutate(w.admin, path(w, f"/targets/{chat}"), method="DELETE")
    assert refused.status_code == 400, refused.text
    assert (await stored(w, chat))["id"] is not None
    gone = await w.mutate(w.admin, path(w, f"/targets/{mail}"), method="DELETE")
    assert gone.status_code == 204, gone.text
    assert await w.q("SELECT 1 FROM staff_notification_targets WHERE id = %s", mail) == []
    assert await w.q("SELECT 1 FROM staff_notifications WHERE target_id = %s", mail) == []
    again = await w.mutate(w.admin, path(w, f"/targets/{mail}"), method="DELETE")
    assert again.status_code == 404
    assert [t["id"] for t in (await listed(w.admin, w))["targets"]] == [chat]


async def test_a_removed_address_can_be_added_again(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    body = {"address": "back@example.test"}
    first = await w.mutate(w.admin, path(w, "/targets/email"), body)
    assert first.status_code == 201
    removed = await w.mutate(w.admin, path(w, f"/targets/{first.json()['id']}"), method="DELETE")
    assert removed.status_code == 204
    again = await w.mutate(w.admin, path(w, "/targets/email"), body)
    assert again.status_code == 201, again.text


# --- preferences ----------------------------------------------------------------------------------


async def test_preferences_roundtrip_read_only_member_allowed(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    reader, accepted = await member(w, role="read_only")
    staff, _ = await member(w)
    defaults = await reader.get(path(w, "/preferences"))
    assert defaults.status_code == 200, defaults.text
    assert defaults.json() == {"email_enabled": False, "kinds": SIX, "min_priority": "high"}
    body = {
        "email_enabled": True,
        "kinds": ["inbox.handback", "inbox.overdue"],
        "min_priority": "low",
    }
    saved = await w.mutate(reader, path(w, "/preferences"), body, method="PUT")
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "email_enabled": True,
        "kinds": ["inbox.overdue", "inbox.handback"],
        "min_priority": "low",
    }
    assert (await reader.get(path(w, "/preferences"))).json() == saved.json()
    # Last write wins and the next person is unaffected.
    assert (await staff.get(path(w, "/preferences"))).json() == defaults.json()
    off = await w.mutate(
        reader,
        path(w, "/preferences"),
        {"email_enabled": False, "kinds": [], "min_priority": "urgent"},
        method="PUT",
    )
    assert off.status_code == 200 and off.json()["kinds"] == []
    rows = await w.q(
        "SELECT user_id, email_enabled FROM console_notification_prefs WHERE client_id = %s",
        w.tenants[0],
    )
    assert [(str(r["user_id"]), r["email_enabled"]) for r in rows] == [
        (accepted["user"]["id"], False)
    ]
    # The exemption is for this one write: the same member still cannot edit a target.
    target = await seed_target(w)
    refused = await w.mutate(reader, path(w, f"/targets/{target}"), put_body(0), method="PUT")
    assert refused.status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"kinds": ["inbox.assigned", "escalation"]},
        {"kinds": "inbox.assigned"},
        {"min_priority": "critical"},
        {"email_enabled": "sometimes"},
    ],
)
async def test_preferences_invalid_422(
    sessions: SessionWorld, relay: FakeTransport, changes: dict[str, Any]
) -> None:
    w = sessions
    staff, _ = await member(w)
    body = {"email_enabled": False, "kinds": SIX, "min_priority": "high"} | changes
    answer = await w.mutate(staff, path(w, "/preferences"), body, method="PUT")
    assert answer.status_code == 422, answer.text
    assert (
        await w.q(
            "SELECT count(*) AS n FROM console_notification_prefs WHERE client_id = %s",
            w.tenants[0],
        )
    )[0]["n"] == 0


async def test_preferences_email_on_503_when_mailer_disabled_but_off_and_get_work(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await member(w)
    body = {"email_enabled": True, "kinds": SIX, "min_priority": "high"}
    on = await w.mutate(staff, path(w, "/preferences"), body, method="PUT")
    assert on.status_code == 503 and on.json()["detail"] == "email_not_configured"
    assert (await staff.get(path(w, "/preferences"))).status_code == 200
    off = await w.mutate(
        staff, path(w, "/preferences"), {**body, "email_enabled": False}, method="PUT"
    )
    assert off.status_code == 200, off.text


async def test_preferences_platform_admin_404(sessions: SessionWorld, relay: FakeTransport) -> None:
    w = sessions
    body = {"email_enabled": False, "kinds": SIX, "min_priority": "high"}
    assert (await w.admin.get(path(w, "/preferences"))).status_code == 404
    saved = await w.mutate(w.admin, path(w, "/preferences"), body, method="PUT")
    assert saved.status_code == 404
    assert (
        await w.q(
            "SELECT count(*) AS n FROM console_notification_prefs WHERE client_id = %s",
            w.tenants[0],
        )
    )[0]["n"] == 0


async def test_a_platform_admin_manages_the_named_companys_targets(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    target = await seed_target(w, tenant=1)
    answer = await w.mutate(w.admin, path(w, f"/targets/{target}", 1), put_body(0), method="PUT")
    assert answer.status_code == 200, answer.text
    added = await w.mutate(w.admin, path(w, "/targets/email", 1), {"address": "x@example.test"})
    assert added.status_code == 201, added.text
    assert (await w.admin.get("/console/v1/notifications/targets")).status_code == 400


# --- audit and routing ----------------------------------------------------------------------------


async def test_audit_rows_written_for_each_write(
    sessions: SessionWorld, relay: FakeTransport
) -> None:
    w = sessions
    staff, accepted = await member(w)
    added = await w.mutate(w.admin, path(w, "/targets/email"), {"address": "audit@example.test"})
    target = added.json()["id"]
    assert (
        await w.mutate(w.admin, path(w, f"/targets/{target}"), put_body(0), method="PUT")
    ).status_code == 200
    assert (await w.mutate(w.admin, path(w, f"/targets/{target}/test"))).status_code == 200
    assert (
        await w.mutate(
            staff,
            path(w, "/preferences"),
            {"email_enabled": True, "kinds": SIX, "min_priority": "high"},
            method="PUT",
        )
    ).status_code == 200
    assert (
        await w.mutate(w.admin, path(w, f"/targets/{target}"), method="DELETE")
    ).status_code == 204
    for action, subject in (
        ("notifications.target.added", target),
        ("notifications.target.updated", target),
        ("notifications.target.tested", target),
        ("notifications.target.removed", target),
        ("notifications.preferences.updated", accepted["user"]["id"]),
    ):
        rows = await audited(w, action)
        assert [r["target_id"] for r in rows] == [subject], action
        assert rows[0]["details"] == {}
    everything = json.dumps(
        await w.q(
            "SELECT action, target_id, details FROM console_audit WHERE client_id = %s",
            w.tenants[0],
        ),
        default=str,
    )
    assert "example.test" not in everything


async def test_the_bell_routes_are_not_shadowed(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await member(w)
    assert (await staff.get(path(w))).status_code == 200
    assert (await staff.get(path(w, "/count"))).json() == {"unread": 0}
    marked = await w.mutate(staff, path(w, "/read"), {"all": True})
    assert marked.status_code == 200 and marked.json() == {"updated": 0}


async def test_reader_exemption_adds_only_the_preferences_put() -> None:
    route, full = "/notifications/preferences", "/console/v1/notifications/preferences"
    assert reader_may_post("PUT", route, full)
    assert reader_may_post("PUT", "/console/v1" + route, full)
    assert not reader_may_post("POST", route, full)
    assert not reader_may_post("DELETE", route, full)
    assert not reader_may_post("PUT", route, full + "/")
    assert not reader_may_post("PUT", route, "/console/v1/notifications/targets")
    assert not reader_may_post("PUT", "/notifications/targets/{target_id}", full)
    target = f"/console/v1/notifications/targets/{UUID(int=1)}"
    assert not reader_may_post("PUT", "/notifications/targets/{target_id}", target)
