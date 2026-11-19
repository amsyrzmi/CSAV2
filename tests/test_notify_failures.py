"""Bell notices for dead notification deliveries (disposable Postgres, synthetic data)."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
import structlog
from csa.core.tenancy import tx
from csa.notify import failures, prefs
from csa.notify.failures import (
    clear_person_failure,
    clear_target_failure,
    report_dead_person_email,
)
from csa.notify.mail_sender import (
    reap_stale_person_emails,
    record_person_failure,
    record_person_unavailable,
)
from csa.notify.people import notify_people
from csa.notify.prefs import Prefs, save_prefs
from csa.notify.sender import MAX_NOTIFICATION_ATTEMPTS, reap_stale_notifications, record_failure
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_notify_mail_sender import (
    FakeTransport,
    _mailer,
    _outbox,
    _person,
    _row,
    _target,
    owner_pool,  # noqa: F401
)
from tests.test_notify_people import _prefs
from tests.test_order_agent import World, world  # noqa: F401
from tests.test_staff_path import (  # noqa: F401
    _crypto_key,
    _quiesce_other_tenants,
    _seed_endpoint,
    _sender,
)

pytestmark = [requires_docker, pytest.mark.asyncio]


def _notices(world: World, kind: str) -> list[tuple[Any, ...]]:  # noqa: F811
    return world.q(
        "SELECT user_id, priority, message_key, params, link, dedupe_key FROM console_notifications"
        " WHERE client_id=%s AND kind=%s ORDER BY user_id",
        world.client_id,
        kind,
    )


def _flag(world: World, target_id: uuid.UUID) -> Any:  # noqa: F811
    return world.q(
        "SELECT failure_notified_at FROM staff_notification_targets WHERE id=%s", target_id
    )[0][0]


def _person_flag(world: World, user_id: str) -> Any:  # noqa: F811
    return world.q(
        "SELECT failure_notified_at FROM console_notification_prefs"
        " WHERE client_id=%s AND user_id=%s",
        world.client_id,
        user_id,
    )[0][0]


def _delivering(world: World, notification_id: uuid.UUID, attempts: int = 5) -> dict[str, Any]:  # noqa: F811
    """Put a row in the claimed state at ``attempts``; return the claim-shaped row."""
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notifications SET status='delivering', attempts=%s, locked_at=now()"
            " WHERE id=%s",
            (attempts, notification_id),
        )
    id_, client_id, target_id, event, tries = world.q(
        "SELECT id, client_id, target_id, event, attempts FROM staff_notifications WHERE id=%s",
        notification_id,
    )[0]
    return {
        "id": id_,
        "client_id": client_id,
        "target_id": target_id,
        "event": event,
        "attempts": tries,
    }


def _outbox_delivering(world: World, outbox_id: uuid.UUID, attempts: int = 5) -> dict[str, Any]:  # noqa: F811
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE console_email_outbox SET status='delivering', attempts=%s, locked_at=now()"
            " WHERE id=%s",
            (attempts, outbox_id),
        )
    id_, client_id, user_id, kind, tries = world.q(
        "SELECT id, client_id, user_id, kind, attempts FROM console_email_outbox WHERE id=%s",
        outbox_id,
    )[0]
    return {"id": id_, "client_id": client_id, "user_id": user_id, "kind": kind, "attempts": tries}


def _lease_expired(world: World, table: str, row_id: uuid.UUID, attempts: int) -> None:  # noqa: F811
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            f"UPDATE {table} SET status='delivering', attempts=%s,"  # type: ignore[arg-type]
            " locked_at = now() - interval '1 hour' WHERE id=%s",
            (attempts, row_id),
        )


async def _kill(world: World, target_id: uuid.UUID) -> uuid.UUID:  # noqa: F811
    """One more row of the target, claimed at the cap and failed by the sender's own path."""
    notification_id = _row(world, target_id)
    await record_failure(world.pool, _delivering(world, notification_id), "smtp_error", None)
    assert world.q("SELECT status FROM staff_notifications WHERE id=%s", notification_id) == [
        ("dead",)
    ]
    return notification_id


async def test_first_dead_notifies_every_admin_once(world: World) -> None:  # noqa: F811
    admins = {_person(world, role="tenant_admin"), _person(world, role="tenant_admin")}
    _person(world, role="tenant_staff")
    target_id = _target(world)
    await _kill(world, target_id)
    rows = _notices(world, "notify.target_failed")
    assert {str(r[0]) for r in rows} == admins and len(rows) == 2
    for _user, priority, key, params, link, dedupe in rows:
        assert priority == "high" and key == "notify.target_failed"
        assert params == {"target_id": str(target_id), "channel": "email", "attempts": 5}
        assert link == {"route": "notifications"}
        assert dedupe.startswith(f"notify.target_failed:{target_id}:")
    assert _flag(world, target_id) is not None
    await _kill(world, target_id)  # a second dead row of the same target
    assert len(_notices(world, "notify.target_failed")) == 2


async def test_params_carry_no_address(world: World) -> None:  # noqa: F811
    _person(world, role="tenant_admin")
    target_id = _target(world)
    address = world.q("SELECT address FROM staff_notification_targets WHERE id=%s", target_id)[0][0]
    await _kill(world, target_id)
    assert address not in str(_notices(world, "notify.target_failed"))


async def test_a_disabled_target_raises_no_notice_and_an_enabled_one_still_does(
    world: World,  # noqa: F811
) -> None:
    """Rows queued for a target that is switched off die quietly; the admins asked for that."""
    _person(world, role="tenant_admin")
    off = _target(world)
    queued = _row(world, off)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute("UPDATE staff_notification_targets SET is_enabled = false WHERE id=%s", (off,))
    await record_failure(world.pool, _delivering(world, queued), "target_unavailable", None)
    assert world.q("SELECT status FROM staff_notifications WHERE id=%s", queued) == [("dead",)]
    assert _notices(world, "notify.target_failed") == []
    assert _flag(world, off) is None
    on = _target(world)
    await _kill(world, on)
    rows = _notices(world, "notify.target_failed")
    assert len(rows) == 1 and rows[0][3]["target_id"] == str(on)
    assert _flag(world, on) is not None


async def test_success_clears_flag_so_next_failure_notifies_again(
    world: World,  # noqa: F811
    owner_pool: Any,  # noqa: F811
) -> None:
    _person(world, role="tenant_admin")
    target_id = _target(world)
    await _kill(world, target_id)
    assert _flag(world, target_id) is not None
    _row(world, target_id)
    assert await _mailer(world, owner_pool, FakeTransport()).do_work()  # delivers the new row
    assert _flag(world, target_id) is None
    await _kill(world, target_id)
    assert len(_notices(world, "notify.target_failed")) == 2


async def test_telegram_success_clears_flag(world: World, owner_pool: Any) -> None:  # noqa: F811
    _seed_endpoint(world)
    target_id = _target(world, channel="telegram")
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE staff_notification_targets SET failure_notified_at = now() WHERE id=%s",
            (target_id,),
        )
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO staff_notifications (client_id, target_id, event, priority, payload,"
            " idempotency_key) VALUES (%s,%s,'escalation','normal',%s,%s)",
            (world.client_id, target_id, Jsonb({"customer": "Synthetic"}), uuid.uuid4().hex),
        )
    sent: list[dict[str, Any]] = []
    assert await _sender(world, owner_pool, sent).process_once()
    assert len(sent) == 1 and _flag(world, target_id) is None


async def test_test_send_and_edit_clear_flag(world: World) -> None:  # noqa: F811
    _person(world, role="tenant_admin")
    target_id = _target(world)
    await _kill(world, target_id)
    async with tx(world.pool, str(world.client_id)) as conn:
        await clear_target_failure(conn, world.client_id, target_id)
    assert _flag(world, target_id) is None
    async with tx(world.pool, str(world.client_id)) as conn:
        await clear_target_failure(conn, world.client_id, target_id)
    assert _flag(world, target_id) is None


async def test_dead_telegram_target_also_notifies(world: World) -> None:  # noqa: F811
    admin = _person(world, role="tenant_admin")
    target_id = _target(world, channel="telegram")
    await _kill(world, target_id)
    rows = _notices(world, "notify.target_failed")
    assert [str(r[0]) for r in rows] == [admin]
    assert rows[0][3]["channel"] == "telegram"


async def test_reaped_dead_row_notifies(world: World, owner_pool: Any) -> None:  # noqa: F811
    _quiesce_other_tenants(world)
    admin = _person(world, role="tenant_admin")
    target_id = _target(world)
    notification_id = _row(world, target_id)
    _lease_expired(world, "staff_notifications", notification_id, MAX_NOTIFICATION_ATTEMPTS)
    assert await reap_stale_notifications(owner_pool, world.pool) >= 1
    assert [str(r[0]) for r in _notices(world, "notify.target_failed")] == [admin]
    # A reaped row that only goes back to pending is not a death.
    other = _target(world)
    pending = _row(world, other)
    _lease_expired(world, "staff_notifications", pending, 1)
    await reap_stale_notifications(owner_pool, world.pool)
    assert len(_notices(world, "notify.target_failed")) == 1 and _flag(world, other) is None


async def test_hook_only_when_update_returned_a_row(world: World, owner_pool: Any) -> None:  # noqa: F811
    _person(world, role="tenant_admin")
    target_id = _target(world)
    notification_id = _row(world, target_id)
    row = _delivering(world, notification_id)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:  # another worker got there
        conn.execute("UPDATE staff_notifications SET status='delivered' WHERE id=%s", (row["id"],))
    await record_failure(world.pool, row, "smtp_error", None)
    assert _notices(world, "notify.target_failed") == [] and _flag(world, target_id) is None
    # The same for the person path: the mark-failed UPDATE matches no row.
    user = _person(world)
    _prefs(world.owner_dsn, str(world.client_id), user, email_enabled=True)
    outbox_id = _outbox(world, user)
    outbox_row = _outbox_delivering(world, outbox_id)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute("UPDATE console_email_outbox SET status='delivered' WHERE id=%s", (outbox_id,))
    await record_person_failure(world.pool, outbox_row, "smtp_error")
    assert _notices(world, "notify.email_failed") == []
    assert _person_flag(world, user) is None
    # And the reaper reports nothing for a row that never reached the cap.
    pending = _row(world, target_id)
    _lease_expired(world, "staff_notifications", pending, 2)
    await reap_stale_notifications(owner_pool, world.pool)
    assert _notices(world, "notify.target_failed") == []


async def test_dead_person_email_notifies_only_that_person_and_no_email_row(
    world: World,  # noqa: F811
) -> None:
    _person(world, role="tenant_admin")
    user = _person(world)
    bystander = _person(world)
    _prefs(world.owner_dsn, str(world.client_id), user, email_enabled=True)
    _prefs(world.owner_dsn, str(world.client_id), bystander, email_enabled=True)
    outbox_id = _outbox(world, user)
    await record_person_failure(world.pool, _outbox_delivering(world, outbox_id), "smtp_error")
    rows = _notices(world, "notify.email_failed")
    assert [str(r[0]) for r in rows] == [user]
    assert rows[0][1] == "high" and rows[0][4] == {"route": "notifications"}
    assert _person_flag(world, user) is not None and _person_flag(world, bystander) is None
    # Bell only: the original dead row is still the only outbox row.
    assert world.q(
        "SELECT count(*) FROM console_email_outbox WHERE client_id=%s", world.client_id
    ) == [(1,)]
    # One notice per episode.
    again = _outbox(world, user)
    await record_person_failure(world.pool, _outbox_delivering(world, again), "smtp_error")
    assert len(_notices(world, "notify.email_failed")) == 1


async def test_person_without_a_prefs_row_gets_nothing(world: World) -> None:  # noqa: F811
    user = _person(world)
    async with tx(world.pool, str(world.client_id)) as conn:
        assert await report_dead_person_email(conn, world.client_id, uuid.UUID(user)) is False
    assert _notices(world, "notify.email_failed") == []


async def test_reaped_dead_person_email_notifies(world: World, owner_pool: Any) -> None:  # noqa: F811
    _quiesce_other_tenants(world)
    user = _person(world)
    _prefs(world.owner_dsn, str(world.client_id), user, email_enabled=True)
    outbox_id = _outbox(world, user)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE console_email_outbox SET status='dead' WHERE client_id <> %s"
            " AND status IN ('pending', 'delivering')",
            (world.client_id,),
        )
    _lease_expired(world, "console_email_outbox", outbox_id, MAX_NOTIFICATION_ATTEMPTS)
    assert await reap_stale_person_emails(owner_pool, world.pool) >= 1
    assert [str(r[0]) for r in _notices(world, "notify.email_failed")] == [user]


async def test_delivered_person_email_clears_flag(world: World, owner_pool: Any) -> None:  # noqa: F811
    user = _person(world)
    _prefs(world.owner_dsn, str(world.client_id), user, email_enabled=True)
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE console_notification_prefs SET failure_notified_at=now() WHERE user_id=%s",
            (user,),
        )
    _outbox(world, user)
    transport = FakeTransport()
    assert await _mailer(world, owner_pool, transport).do_work()
    assert len(transport.sent) == 1 and _person_flag(world, user) is None


async def test_unavailable_recipient_raises_no_notice(world: World) -> None:  # noqa: F811
    user = _person(world)
    _prefs(world.owner_dsn, str(world.client_id), user, email_enabled=True)
    outbox_id = _outbox(world, user)
    await record_person_unavailable(world.pool, _outbox_delivering(world, outbox_id, attempts=1))
    assert world.q("SELECT status FROM console_email_outbox WHERE id=%s", outbox_id) == [("dead",)]
    assert _notices(world, "notify.email_failed") == [] and _person_flag(world, user) is None


async def test_save_prefs_clears_person_flag(world: World) -> None:  # noqa: F811
    user = _person(world)
    _prefs(world.owner_dsn, str(world.client_id), user, email_enabled=True)
    async with tx(world.pool, str(world.client_id)) as conn:
        assert await report_dead_person_email(conn, world.client_id, uuid.UUID(user)) is True
    assert _person_flag(world, user) is not None
    async with tx(world.pool, str(world.client_id)) as conn:
        await save_prefs(
            conn,
            world.client_id,
            uuid.UUID(user),
            Prefs(email_enabled=True, kinds=prefs.KINDS, min_priority="high"),
        )
    assert _person_flag(world, user) is None
    async with tx(world.pool, str(world.client_id)) as conn:
        await clear_person_failure(conn, world.client_id, uuid.UUID(user))  # idempotent


async def test_failure_kinds_are_not_emailable() -> None:
    assert "notify.target_failed" not in prefs.KINDS
    assert "notify.email_failed" not in prefs.KINDS


async def test_failure_kinds_never_enter_an_outbox(world: World) -> None:  # noqa: F811
    user = uuid.UUID(_person(world, role="tenant_admin"))
    _prefs(world.owner_dsn, str(world.client_id), str(user), email_enabled=True, min_priority="low")
    async with tx(world.pool, str(world.client_id)) as conn:
        for kind in ("notify.target_failed", "notify.email_failed"):
            result = await notify_people(
                conn,
                world.client_id,
                recipients=[user],
                kind=kind,
                priority="urgent",
                message_key=kind,
                params={},
                link={"route": "notifications"},
                dedupe_key=f"{kind}:probe",
            )
            assert (result.console_rows, result.email_rows) == (1, 0)
    assert world.q(
        "SELECT count(*) FROM console_email_outbox WHERE client_id=%s", world.client_id
    ) == [(0,)]


async def _python_error(conn: Any) -> None:
    raise RuntimeError("hook broke")


async def _database_error(conn: Any) -> None:
    """A real SQL error on the caller's connection: aborts the transaction unless a savepoint
    around the hook catches it."""
    await conn.execute("SELECT * FROM no_such_table_for_the_hook_test")


_BREAKERS = pytest.mark.parametrize("breaker", [_python_error, _database_error])


def _break_after_the_flag(monkeypatch: pytest.MonkeyPatch, breaker: Any) -> None:
    """Make the notice-writing step fail, AFTER the flag-mark UPDATE has already run."""

    async def broken_notify_people(conn: Any, *_args: Any, **_kwargs: Any) -> None:
        await breaker(conn)

    monkeypatch.setattr(failures, "notify_people", broken_notify_people)


@_BREAKERS
async def test_a_failing_target_hook_does_not_mask_the_failure_or_roll_it_back(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    breaker: Any,
) -> None:
    _person(world, role="tenant_admin")
    _break_after_the_flag(monkeypatch, breaker)
    target_id = _target(world)
    with structlog.testing.capture_logs() as logs:
        await _kill(world, target_id)  # asserts the row committed as dead
    # The flag UPDATE ran before the break; the savepoint rolled it back with the notice.
    assert _flag(world, target_id) is None
    assert _notices(world, "notify.target_failed") == []
    events = [entry for entry in logs if entry["event"] == "target_failure_report_failed"]
    assert len(events) == 1 and events[0]["target_id"] == str(target_id)


@_BREAKERS
async def test_a_failing_person_hook_does_not_mask_the_failure_or_roll_it_back(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    breaker: Any,
) -> None:
    user = _person(world)
    _prefs(world.owner_dsn, str(world.client_id), user, email_enabled=True)
    _break_after_the_flag(monkeypatch, breaker)
    outbox_id = _outbox(world, user)
    with structlog.testing.capture_logs() as logs:
        await record_person_failure(world.pool, _outbox_delivering(world, outbox_id), "smtp_error")
    assert world.q("SELECT status FROM console_email_outbox WHERE id=%s", outbox_id) == [("dead",)]
    assert _person_flag(world, user) is None
    assert _notices(world, "notify.email_failed") == []
    events = [entry for entry in logs if entry["event"] == "person_failure_report_failed"]
    assert len(events) == 1 and events[0]["user_id"] == user


@_BREAKERS
async def test_the_transaction_is_usable_after_a_failing_hook(
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    breaker: Any,
) -> None:
    """The caller's transaction is not left aborted: later statements run and its work commits."""
    _person(world, role="tenant_admin")
    user = uuid.UUID(_person(world))
    _prefs(world.owner_dsn, str(world.client_id), str(user), email_enabled=True)
    _break_after_the_flag(monkeypatch, breaker)
    target_id = _target(world)
    with structlog.testing.capture_logs():
        async with tx(world.pool, str(world.client_id)) as conn:
            await failures.report_dead_target_safely(conn, world.client_id, target_id)
            await failures.report_dead_person_email_safely(conn, world.client_id, user)
            assert (await (await conn.execute("SELECT 1 AS one")).fetchone()) == {"one": 1}
            await conn.execute(
                "UPDATE staff_notification_targets SET version = 41 WHERE id=%s", (target_id,)
            )
    assert world.q("SELECT version FROM staff_notification_targets WHERE id=%s", target_id) == [
        (41,)
    ]
    assert _flag(world, target_id) is None and _person_flag(world, str(user)) is None
