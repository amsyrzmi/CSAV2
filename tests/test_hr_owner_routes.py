"""The two Team routes that read and move the HR owner, and the emails after the move commits.

Real identity routes, real definer functions, a disposable database and a fake mail relay. Reuses
the mail_world fixture of tests/test_console_identity_gateway.py, whose platform admin is a real
non-member: HrWorld's "platform" user has a membership, which would hide that bug class.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, LiteralString
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest
from csa.console.identity import IdentityService
from csa.console.mailer import Mailer, OutgoingMail
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_identity_gateway import ORIGIN, SENDER, MailWorld, mail_world

__all__ = ["mail_world"]
pytestmark = [requires_docker]

PATH = "/console/v1/team/hr-owner"
ADMIN_ONLY = {"detail": "tenant administrator required"}
POWER = "The HR owner controls who has HR access and can see sensitive employee details."


async def person(world: MailWorld, name: str) -> dict[str, Any]:
    [row] = await world.q("SELECT id, email FROM console_users WHERE display_name=%s", name)
    return row


async def member(
    world: MailWorld, name: str, role: str = "tenant_staff"
) -> tuple[httpx.AsyncClient, str, str]:
    """A new member who has accepted their invitation: browser, user id and address."""
    response, email = await world.invite(world.owner, role=role, display_name=name)
    assert response.status_code == 201, response.text
    client, user_id = await world.accept(world.last_token())
    world.transport.sent.clear()
    return client, user_id, email


def move_body(world: MailWorld, new_owner: object, expected: object) -> dict[str, Any]:
    return {
        "client_id": world.hr_tenant,
        "new_owner_id": str(new_owner),
        "expected_owner_id": None if expected is None else str(expected),
    }


async def post_move(
    world: MailWorld,
    client: httpx.AsyncClient,
    new_owner: object,
    expected: object,
    *,
    key: UUID | None = None,
) -> httpx.Response:
    return await client.post(
        PATH,
        json=move_body(world, new_owner, expected),
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": client.cookies["csa_csrf"],
            "Idempotency-Key": str(key or uuid4()),
        },
    )


async def get_owner(
    world: MailWorld, client: httpx.AsyncClient, tenant: str | None = None
) -> httpx.Response:
    return await client.get(PATH, params={"client_id": tenant or world.hr_tenant})


async def owner_id(world: MailWorld) -> str | None:
    rows = await world.q(
        "SELECT user_id::text FROM hr_client_owners WHERE client_id=%s", world.hr_tenant
    )
    return rows[0]["user_id"] if rows else None


async def moves_recorded(world: MailWorld) -> int:
    rows = await world.q(
        "SELECT count(*) AS n FROM console_audit WHERE action='hr.owner_moved' AND client_id=%s",
        world.hr_tenant,
    )
    return rows[0]["n"]


async def remove_owner(world: MailWorld) -> None:
    await world.q("DELETE FROM hr_client_owners WHERE client_id=%s", world.hr_tenant)


def addresses(world: MailWorld) -> Counter[str]:
    return Counter(mail.to for mail in world.transport.sent)


async def test_get_for_admin_and_refused_for_staff_and_other_company(mail_world: MailWorld) -> None:
    owner = {"user_id": mail_world.owner_id, "display_name": "Synthetic Owner", "usable": True}
    for client in (mail_world.owner, mail_world.other, mail_world.platform):
        response = await get_owner(mail_world, client)
        assert response.status_code == 200, response.text
        assert response.json() == {"available": True, "owner": owner}
        assert response.headers["Cache-Control"] == "no-store"

    staff, _, _ = await member(mail_world, "Synthetic Staff")
    reader, _, _ = await member(mail_world, "Synthetic Reader", "read_only")
    for client in (staff, reader):
        refused = await get_owner(mail_world, client)
        assert refused.status_code == 403 and refused.json() == ADMIN_ONLY
    other_company = await get_owner(mail_world, mail_world.owner, mail_world.plain_tenant)
    assert other_company.status_code == 403 and other_company.json() == ADMIN_ONLY

    # An owner who can't act says so, and the panel can offer a new one.
    await mail_world.q(
        "UPDATE console_memberships SET role='read_only' WHERE user_id=%s", mail_world.owner_id
    )
    stuck = await get_owner(mail_world, mail_world.other)
    assert stuck.json()["owner"] == {**owner, "usable": False}

    await remove_owner(mail_world)
    assert (await get_owner(mail_world, mail_world.other)).json() == {
        "available": True,
        "owner": None,
    }


async def test_post_is_refused_for_staff_and_other_company(mail_world: MailWorld) -> None:
    staff, staff_id, _ = await member(mail_world, "Synthetic Staff")
    reader, _, _ = await member(mail_world, "Synthetic Reader", "read_only")
    for client in (staff, reader):
        refused = await post_move(mail_world, client, staff_id, mail_world.owner_id)
        assert refused.status_code == 403 and refused.json() == ADMIN_ONLY
    outside = await mail_world.owner.post(
        PATH,
        json={**move_body(mail_world, staff_id, None), "client_id": mail_world.plain_tenant},
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": mail_world.owner.cookies["csa_csrf"],
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert outside.status_code == 403 and outside.json() == ADMIN_ONLY
    assert await owner_id(mail_world) == mail_world.owner_id
    assert mail_world.transport.sent == []


async def test_get_when_package_off_says_unavailable(mail_world: MailWorld) -> None:
    await mail_world.q(
        "UPDATE console_service_packages SET status='suspended' WHERE client_id=%s",
        mail_world.hr_tenant,
    )
    for client in (mail_world.other, mail_world.platform):
        response = await get_owner(mail_world, client)
        assert response.status_code == 200, response.text
        assert response.json() == {"available": False, "owner": None}

    # Authorisation comes first: staff learn nothing about the package.
    staff, staff_id, _ = await member(mail_world, "Synthetic Staff")
    assert (await get_owner(mail_world, staff)).status_code == 403

    refused = await post_move(mail_world, mail_world.other, staff_id, mail_world.owner_id)
    assert refused.status_code == 422 and refused.json() == {"detail": "hr_not_available"}
    assert await owner_id(mail_world) == mail_world.owner_id


async def test_post_needs_csrf_and_idempotency_key(mail_world: MailWorld) -> None:
    client = mail_world.other
    _, staff_id, _ = await member(mail_world, "Synthetic Staff")
    body = move_body(mail_world, staff_id, mail_world.owner_id)
    key = str(uuid4())
    csrf = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["csa_csrf"]}

    no_token = await client.post(PATH, json=body, headers={"Idempotency-Key": key})
    assert no_token.status_code == 403
    wrong_token = await client.post(
        PATH,
        json=body,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "synthetic-wrong", "Idempotency-Key": key},
    )
    assert wrong_token.status_code == 403
    no_key = await client.post(PATH, json=body, headers=csrf)
    assert no_key.status_code == 422
    bad_key = await client.post(PATH, json=body, headers={**csrf, "Idempotency-Key": "not-a-uuid"})
    assert bad_key.status_code == 422

    # "No owner yet" has to be said, so a client that forgets the field can't claim it.
    unsaid = {key_: value for key_, value in body.items() if key_ != "expected_owner_id"}
    missing = await client.post(PATH, json=unsaid, headers={**csrf, "Idempotency-Key": key})
    assert missing.status_code == 422

    assert await owner_id(mail_world) == mail_world.owner_id
    assert mail_world.transport.sent == []


async def test_tenant_admin_moves_and_each_address_gets_one_email(mail_world: MailWorld) -> None:
    other = await person(mail_world, "Synthetic Other")
    owner = await person(mail_world, "Synthetic Owner")
    _, new_id, new_email = await member(mail_world, "Synthetic New")
    await member(mail_world, "Synthetic Bystander")
    await member(mail_world, "Synthetic Reader", "read_only")
    _, gone_id, gone_email = await member(mail_world, "Synthetic Gone", "tenant_admin")
    await mail_world.q("UPDATE console_memberships SET is_enabled=false WHERE user_id=%s", gone_id)

    response = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "owner": {"user_id": new_id, "display_name": "Synthetic New", "usable": True},
        "mail": "sent",
    }
    assert await owner_id(mail_world) == new_id
    assert await moves_recorded(mail_world) == 1
    assert addresses(mail_world) == Counter([owner["email"], other["email"], new_email])
    assert gone_email not in addresses(mail_world)

    greeting = {
        owner["email"]: "Synthetic Owner",
        other["email"]: "Synthetic Other",
        new_email: "Synthetic New",
    }
    for mail in mail_world.transport.sent:
        assert mail.subject == "HR owner changed for Kedai Synthetic HR"
        assert mail.text.startswith(f"Hello {greeting[mail.to]},\n")
        match = re.search(
            r"Synthetic Other made Synthetic New the HR owner of Kedai Synthetic HR on "
            r"(\d+ \w+ \d+, \d\d:\d\d), replacing Synthetic Owner\.\n\n" + re.escape(POWER),
            mail.text,
        )
        assert match is not None, mail.text
        # In the company's own zone, not the server's.
        zone = ZoneInfo("Asia/Jakarta")
        stamped = datetime.strptime(match.group(1), "%d %b %Y, %H:%M").replace(tzinfo=zone)
        assert abs(datetime.now(zone) - stamped) < timedelta(minutes=2)
        assert "http" not in mail.text


async def test_admin_moves_to_self_gets_one_email(mail_world: MailWorld) -> None:
    other = await person(mail_world, "Synthetic Other")
    owner = await person(mail_world, "Synthetic Owner")
    response = await post_move(mail_world, mail_world.other, other["id"], mail_world.owner_id)
    assert response.status_code == 200, response.text
    assert response.json()["mail"] == "sent"
    assert addresses(mail_world) == Counter([other["email"], owner["email"]])


@pytest.mark.parametrize(
    "stuck",
    [
        "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s",
        "UPDATE console_users SET is_enabled=false WHERE id=%s",
        "UPDATE console_memberships SET role='read_only' WHERE user_id=%s",
    ],
    ids=["membership_disabled", "account_disabled", "read_only"],
)
async def test_disabled_old_owner_is_still_emailed(
    mail_world: MailWorld, stuck: LiteralString
) -> None:
    owner = await person(mail_world, "Synthetic Owner")
    _, new_id, _ = await member(mail_world, "Synthetic New")
    await mail_world.q(stuck, mail_world.owner_id)
    mail_world.transport.sent.clear()

    response = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id)

    assert response.status_code == 200, response.text
    assert await owner_id(mail_world) == new_id
    assert addresses(mail_world)[owner["email"]] == 1
    assert "replacing Synthetic Owner." in mail_world.transport.sent[0].text


async def test_retry_returns_repeat_and_sends_nothing(mail_world: MailWorld) -> None:
    _, new_id, _ = await member(mail_world, "Synthetic New")
    key = uuid4()
    first = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id, key=key)
    assert first.status_code == 200 and first.json()["mail"] == "sent"
    sent = len(mail_world.transport.sent)

    retry = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id, key=key)

    assert retry.status_code == 200, retry.text
    assert retry.json() == {"owner": first.json()["owner"], "mail": "repeat"}
    assert len(mail_world.transport.sent) == sent
    assert await moves_recorded(mail_world) == 1


async def test_retry_after_a_later_move_names_who_holds_the_title_now(
    mail_world: MailWorld,
) -> None:
    _, first_id, _ = await member(mail_world, "Synthetic First")
    _, second_id, _ = await member(mail_world, "Synthetic Second")
    key = uuid4()
    moved = await post_move(mail_world, mail_world.other, first_id, mail_world.owner_id, key=key)
    assert moved.status_code == 200
    assert (await post_move(mail_world, mail_world.other, second_id, first_id)).status_code == 200
    sent = len(mail_world.transport.sent)

    retry = await post_move(mail_world, mail_world.other, first_id, mail_world.owner_id, key=key)

    assert retry.status_code == 200, retry.text
    assert retry.json()["mail"] == "repeat"
    assert retry.json()["owner"]["user_id"] == second_id
    assert await owner_id(mail_world) == second_id
    assert len(mail_world.transport.sent) == sent


async def test_a_key_reused_for_another_move_is_refused(mail_world: MailWorld) -> None:
    _, new_id, _ = await member(mail_world, "Synthetic New")
    _, another_id, _ = await member(mail_world, "Synthetic Another")
    other = await person(mail_world, "Synthetic Other")
    key = uuid4()
    first = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id, key=key)
    assert first.status_code == 200, first.text
    sent = len(mail_world.transport.sent)

    # Another person, then the same person with another expected owner: the fingerprint covers both.
    for chosen, expected in ((another_id, mail_world.owner_id), (new_id, other["id"])):
        refused = await post_move(mail_world, mail_world.other, chosen, expected, key=key)
        assert refused.status_code == 409 and refused.json() == {"detail": "idempotency_conflict"}

    assert await owner_id(mail_world) == new_id
    assert await moves_recorded(mail_world) == 1
    assert len(mail_world.transport.sent) == sent


@pytest.mark.parametrize("failure", ["absent", "disabled", "failing", "one_fails", "raises"])
async def test_mail_off_or_failing_keeps_the_move_and_says_not_sent(
    mail_world: MailWorld, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    owner = await person(mail_world, "Synthetic Owner")
    _, new_id, _ = await member(mail_world, "Synthetic New")
    transport = mail_world.transport
    if failure == "absent":
        mail_world.app.state.mailer = None
    elif failure == "disabled":
        mail_world.app.state.mailer = Mailer(None, SENDER)
    elif failure == "failing":
        transport.failure = TimeoutError("synthetic relay timeout")
    elif failure == "one_fails":
        real_send = transport.send

        def send(mail: OutgoingMail, sender: str) -> None:
            if mail.to == owner["email"]:
                raise TimeoutError("synthetic relay timeout")
            real_send(mail, sender)

        monkeypatch.setattr(transport, "send", send)
    else:
        monkeypatch.setattr(
            IdentityService, "members", AsyncMock(side_effect=RuntimeError("synthetic outage"))
        )

    response = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id)

    assert response.status_code == 200, response.text
    assert response.json()["mail"] == "not_sent"
    assert response.json()["owner"]["user_id"] == new_id
    assert await owner_id(mail_world) == new_id
    assert await moves_recorded(mail_world) == 1


async def test_bad_timezone_does_not_block(mail_world: MailWorld) -> None:
    _, new_id, _ = await member(mail_world, "Synthetic New")
    await mail_world.q(
        "UPDATE clients SET timezone='Synthetic/Nowhere' WHERE id=%s", mail_world.hr_tenant
    )
    response = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id)
    assert response.status_code == 200, response.text
    assert response.json()["mail"] == "sent"
    assert await owner_id(mail_world) == new_id
    assert len(mail_world.transport.sent) == 3


async def test_refusal_codes_map_to_409(mail_world: MailWorld) -> None:
    other = await person(mail_world, "Synthetic Other")
    admin, owner = mail_world.other, mail_world.owner_id
    _, reader_id, _ = await member(mail_world, "Synthetic Reader", "read_only")
    _, leaving_id, _ = await member(mail_world, "Synthetic Leaving")
    _, first_password_id, _ = await member(mail_world, "Synthetic First Password")
    _, staff_id, _ = await member(mail_world, "Synthetic Staff")
    employee = uuid4()
    await mail_world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,is_active) VALUES (%s,%s,%s,false)",
        mail_world.hr_tenant,
        employee,
        "Synthetic Leaving",
    )
    await mail_world.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        mail_world.hr_tenant,
        leaving_id,
        employee,
    )
    await mail_world.q(
        "UPDATE console_users SET must_change_password=true WHERE id=%s", first_password_id
    )

    for chosen, expected, code in (
        (reader_id, owner, "owner_read_only"),
        (leaving_id, owner, "owner_leaving"),
        (first_password_id, owner, "owner_unavailable"),
        (owner, owner, "owner_unchanged"),
        (staff_id, other["id"], "owner_changed"),
        (staff_id, None, "owner_changed"),
    ):
        refused = await post_move(mail_world, admin, chosen, expected)
        assert refused.status_code == 409, (code, refused.text)
        assert refused.json() == {"detail": code}

    assert await owner_id(mail_world) == owner
    assert await moves_recorded(mail_world) == 0
    assert mail_world.transport.sent == []


async def test_the_function_refusing_the_actor_is_the_team_403(
    mail_world: MailWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second check, inside the function, for a right lost after the route's own check."""
    staff, staff_id, _ = await member(mail_world, "Synthetic Staff")
    monkeypatch.setattr(IdentityService, "require_member_admin", staticmethod(lambda *_: None))
    for response in (
        await get_owner(mail_world, staff),
        await post_move(mail_world, staff, staff_id, mail_world.owner_id),
    ):
        assert response.status_code == 403 and response.json() == ADMIN_ONLY
    assert await owner_id(mail_world) == mail_world.owner_id


class ForcedError:
    """Makes the function fail at its hr_operations insert, with a chosen SQLSTATE and message."""

    def __init__(self, world: MailWorld, state: LiteralString, message: LiteralString) -> None:
        self.world = world
        self.state: LiteralString = state
        self.message: LiteralString = message

    async def __aenter__(self) -> None:
        await self.world.q(
            "CREATE FUNCTION public.synthetic_forced_error() RETURNS trigger "
            f"LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '{self.message}' "
            f"USING ERRCODE='{self.state}'; END $$"
        )
        await self.world.q(
            "CREATE TRIGGER synthetic_forced_error BEFORE INSERT ON public.hr_operations "
            "FOR EACH ROW EXECUTE FUNCTION public.synthetic_forced_error()"
        )

    async def __aexit__(self, *_: object) -> None:
        await self.world.q("DROP TRIGGER synthetic_forced_error ON public.hr_operations")
        await self.world.q("DROP FUNCTION public.synthetic_forced_error()")


async def test_deadlock_answers_try_again_and_changes_nothing(mail_world: MailWorld) -> None:
    _, new_id, _ = await member(mail_world, "Synthetic New")
    key = uuid4()
    async with ForcedError(mail_world, "40P01", "deadlock detected"):
        refused = await post_move(
            mail_world, mail_world.other, new_id, mail_world.owner_id, key=key
        )
    assert refused.status_code == 409 and refused.json() == {"detail": "try_again"}
    assert await owner_id(mail_world) == mail_world.owner_id
    assert await moves_recorded(mail_world) == 0
    assert mail_world.transport.sent == []

    # Nothing was spent, so the same key goes through when tried again.
    retried = await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id, key=key)
    assert retried.status_code == 200 and retried.json()["mail"] == "sent"


async def test_an_unknown_database_refusal_is_not_dressed_up_as_a_code(
    mail_world: MailWorld,
) -> None:
    _, new_id, _ = await member(mail_world, "Synthetic New")
    async with ForcedError(mail_world, "23514", "synthetic_other_check"):
        with pytest.raises(psycopg.errors.CheckViolation):
            await post_move(mail_world, mail_world.other, new_id, mail_world.owner_id)
    assert await owner_id(mail_world) == mail_world.owner_id


async def test_platform_admin_is_named_fourstacks_admin(mail_world: MailWorld) -> None:
    assert (
        await mail_world.q(
            "SELECT 1 FROM console_memberships WHERE user_id=%s", mail_world.platform_id
        )
        == []
    ), "this test needs a platform admin who is not a member"
    _, new_id, _ = await member(mail_world, "Synthetic New")

    response = await post_move(mail_world, mail_world.platform, new_id, mail_world.owner_id)

    assert response.status_code == 200, response.text
    assert response.json()["mail"] == "sent"
    [row] = await mail_world.q(
        "SELECT user_id::text, approved_by::text FROM hr_client_owners WHERE client_id=%s",
        mail_world.hr_tenant,
    )
    assert row == {"user_id": new_id, "approved_by": mail_world.platform_id}
    assert "synthetic-admin@example.test" not in addresses(mail_world)
    assert mail_world.transport.sent
    for mail in mail_world.transport.sent:
        assert "FourStacks admin made Synthetic New the HR owner" in mail.text
        assert "Synthetic Admin" not in mail.text and "Synthetic Admin" not in mail.html


@pytest.mark.parametrize("actor", ["platform", "tenant_admin"])
async def test_first_owner_email_names_no_old_owner(mail_world: MailWorld, actor: str) -> None:
    await remove_owner(mail_world)
    _, new_id, _ = await member(mail_world, "Synthetic First")
    client = mail_world.platform if actor == "platform" else mail_world.other
    assert (await get_owner(mail_world, client)).json()["owner"] is None

    response = await post_move(mail_world, client, new_id, None)

    assert response.status_code == 200, response.text
    assert response.json()["owner"]["user_id"] == new_id
    assert await owner_id(mail_world) == new_id
    assert mail_world.transport.sent
    for mail in mail_world.transport.sent:
        assert "replacing" not in mail.text


async def test_no_address_in_logs(
    mail_world: MailWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    owner = await person(mail_world, "Synthetic Owner")
    other = await person(mail_world, "Synthetic Other")
    _, first_id, first_email = await member(mail_world, "Synthetic First")
    _, second_id, second_email = await member(mail_world, "Synthetic Second")
    every_address = (owner["email"], other["email"], first_email, second_email)

    mail_world.transport.failure = TimeoutError(f"synthetic relay refused {owner['email']}")
    with capture_logs() as relay_logs:
        failed = await post_move(mail_world, mail_world.other, first_id, mail_world.owner_id)
    assert failed.json()["mail"] == "not_sent"
    assert "smtp_timeout" in repr(relay_logs)

    # An exception in the mail step itself: its text can carry an address, the log must not.
    monkeypatch.setattr(
        IdentityService,
        "members",
        AsyncMock(side_effect=RuntimeError(f"synthetic outage {second_email}")),
    )
    with capture_logs() as step_logs:
        raised = await post_move(mail_world, mail_world.other, second_id, first_id)
    assert raised.json()["mail"] == "not_sent"
    assert "unexpected_error" in repr(step_logs)

    captured = repr(relay_logs) + repr(step_logs) + caplog.text
    for address in every_address:
        assert address not in captured
    assert "synthetic outage" not in captured
