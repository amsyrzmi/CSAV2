"""Migration 0484: email sending columns, threading headers, sync paging and direct-report reads.

Runs against a disposable migrated Postgres with synthetic rows only.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.usefixtures("migrated_dsn")]

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "migrations" / "0484_email_management.sql"
BASIC_FIELDS = ["assigned_manager", "department", "display_name", "job_title", "work_email"]


@pytest.fixture
def owner(pg_container: object) -> Any:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        yield conn


def make_tenant(owner: psycopg.Connection[Any]) -> uuid.UUID:
    client = uuid.uuid4()
    owner.execute(
        "INSERT INTO clients(id,slug,name,platform_version) "
        "VALUES (%s,%s,'Synthetic email','b-test')",
        (client, f"email-schema-{client}"),
    )
    return client


def make_member(
    owner: psycopg.Connection[Any], client: uuid.UUID, role: str = "tenant_staff"
) -> uuid.UUID:
    user = uuid.uuid4()
    owner.execute(
        "INSERT INTO console_users(id,email,display_name) VALUES (%s,%s,'Synthetic member')",
        (user, f"synthetic-{user}@example.test"),
    )
    owner.execute(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,%s)",
        (user, client, role),
    )
    return user


def make_mailbox(owner: psycopg.Connection[Any], client: uuid.UUID, user: uuid.UUID) -> uuid.UUID:
    mailbox = uuid.uuid4()
    owner.execute(
        "INSERT INTO email_mailboxes(client_id,user_id,id,status,address) "
        "VALUES (%s,%s,%s,'connected','owner@example.test')",
        (client, user, mailbox),
    )
    return mailbox


def insert_draft(
    owner: psycopg.Connection[Any],
    client: uuid.UUID,
    mailbox: uuid.UUID,
    *,
    status: str = "draft",
    to_addresses: list[str] | None = None,
    cc: list[str] | None = None,
) -> uuid.UUID:
    draft = uuid.uuid4()
    owner.execute(
        "INSERT INTO email_drafts(client_id,mailbox_id,id,recipient,subject,body,status,source,"
        "to_addresses,cc) VALUES (%s,%s,%s,'first@example.test','Subject','Body',%s,'human',%s,%s)",
        (client, mailbox, draft, status, to_addresses, cc or []),
    )
    return draft


def test_draft_columns_and_checks(owner: psycopg.Connection[Any]) -> None:
    client = make_tenant(owner)
    mailbox = make_mailbox(owner, client, make_member(owner, client))

    twenty = [f"p{n}@example.test" for n in range(20)]
    insert_draft(owner, client, mailbox, to_addresses=twenty[:12], cc=twenty[12:])
    with pytest.raises(errors.CheckViolation):
        insert_draft(owner, client, mailbox, to_addresses=twenty, cc=["extra@example.test"])
    with pytest.raises(errors.CheckViolation):
        insert_draft(owner, client, mailbox, to_addresses=[], cc=[])

    sent = insert_draft(owner, client, mailbox, status="sent", to_addresses=["a@example.test"])
    owner.execute(
        "UPDATE email_drafts SET sent_at=now(), gmail_message_id='gm-1', "
        "reply_to_message_id='m-0', needs_staff_input=true WHERE id=%s",
        (sent,),
    )
    row = owner.execute(
        "SELECT status, bcc, needs_staff_input FROM email_drafts WHERE id=%s", (sent,)
    ).fetchone()
    assert row == ("sent", [], True)
    with pytest.raises(errors.CheckViolation):
        insert_draft(owner, client, mailbox, status="bogus", to_addresses=["a@example.test"])

    owner.execute(
        "INSERT INTO email_threads(client_id,mailbox_id,thread_id,latest_rfc_message_id,"
        "latest_references,latest_reply_to,latest_from_owner) "
        "VALUES (%s,%s,'t1','<a@x>','<r@x>','reply@example.test',false)",
        (client, mailbox),
    )
    owner.execute(
        "UPDATE email_mailboxes SET sync_page_token='tok', last_synced_at=now() WHERE id=%s",
        (mailbox,),
    )


def test_existing_drafts_backfill_to_addresses(owner: psycopg.Connection[Any]) -> None:
    client = make_tenant(owner)
    mailbox = make_mailbox(owner, client, make_member(owner, client))
    # A draft written before 0484 has no to_addresses yet.
    legacy = insert_draft(owner, client, mailbox)
    explicit = insert_draft(owner, client, mailbox, to_addresses=["kept@example.test"])
    owner.execute("UPDATE email_drafts SET to_addresses=NULL WHERE id=%s", (legacy,))

    # The file is idempotent and never writes schema_migrations, so re-running it is safe.
    owner.execute(cast(LiteralString, MIGRATION.read_text()))

    def snapshot() -> dict[uuid.UUID, Any]:
        rows = owner.execute(
            "SELECT id, to_addresses, cc, bcc FROM email_drafts WHERE id = ANY(%s)",
            ([legacy, explicit],),
        ).fetchall()
        return {row[0]: row[1:] for row in rows}

    once = snapshot()
    assert once[legacy] == (["first@example.test"], [], [])
    assert once[explicit] == (["kept@example.test"], [], [])
    owner.execute(cast(LiteralString, MIGRATION.read_text()))
    assert snapshot() == once


def test_proposal_kind_and_turn_actions_include_email(owner: psycopg.Connection[Any]) -> None:
    kinds = owner.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='console_proposals_kind_check'"
    ).fetchone()
    assert kinds is not None
    for kind in (
        "email_export",
        "training_submit",
        "inbox_decision",
        "subscription_action",
        "email_send",
    ):
        assert f"'{kind}'" in kinds[0]
    actions = owner.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='console_workspace_turns_action_check'"
    ).fetchone()
    assert actions is not None
    for action in (
        "answer",
        "navigate",
        "directory_lookup",
        "email_compose",
        "training_submit",
        "inbox_decision",
        "clarification",
        "page_guidance",
        "local_order_read",
        "email_reply",
        "email_team",
        "inbox_summary",
        "email_send",
    ):
        assert f"'{action}'" in actions[0]


def add_employee(
    owner: psycopg.Connection[Any],
    client: uuid.UUID,
    name: str,
    *,
    manager: uuid.UUID | None = None,
    active: bool = True,
    email: str | None = "{}@example.test",
    user: uuid.UUID | None = None,
) -> uuid.UUID:
    employee = uuid.uuid4()
    owner.execute(
        "INSERT INTO hr_employees(client_id,id,display_name,work_email,manager_id,is_active) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (client, employee, name, email.format(name.lower()) if email else None, manager, active),
    )
    if user is not None:
        owner.execute(
            "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
            (client, user, employee),
        )
    return employee


def direct_reports(
    app_dsn: str, client: uuid.UUID, caller: uuid.UUID | None
) -> list[tuple[uuid.UUID, str, str | None]]:
    with psycopg.connect(app_dsn) as conn:
        if caller is not None:
            conn.execute("SELECT set_config('app.hr_user_id', %s, false)", (str(caller),))
        return cast(
            list[tuple[uuid.UUID, str, str | None]],
            conn.execute("SELECT * FROM email_my_direct_reports(%s)", (client,)).fetchall(),
        )


def test_direct_reports_only_for_approvers(owner: psycopg.Connection[Any], app_dsn: str) -> None:
    client, other = make_tenant(owner), make_tenant(owner)
    manager_user = make_member(owner, client)
    staff_user = make_member(owner, client)
    stranger = make_member(owner, other)
    manager = add_employee(owner, client, "Manager", user=manager_user)
    first = add_employee(owner, client, "First", manager=manager)
    second = add_employee(owner, client, "Second", manager=manager, email=None)
    add_employee(owner, client, "Leaver", manager=manager, active=False)
    add_employee(owner, client, "Unrelated")
    add_employee(owner, client, "Staff", manager=first, user=staff_user)
    foreign = add_employee(owner, other, "Foreign", user=stranger)
    add_employee(owner, other, "Foreign Report", manager=foreign)

    # Without the approves_team rows the manager sees nothing.
    assert direct_reports(app_dsn, client, manager_user) == []

    owner.execute(
        "INSERT INTO hr_grants(client_id,user_id,action,scope,fields) "
        "VALUES (%s,%s,'directory.read','assigned_manager',%s)",
        (client, manager_user, BASIC_FIELDS),
    )
    owner.execute(
        "INSERT INTO hr_grants(client_id,user_id,action,scope,fields) "
        "VALUES (%s,%s,'directory.read','assigned_manager',%s)",
        (other, stranger, BASIC_FIELDS),
    )
    rows = direct_reports(app_dsn, client, manager_user)
    assert sorted((row[0], row[1], row[2]) for row in rows) == sorted(
        [(first, "First", "first@example.test"), (second, "Second", None)]
    )

    # A staff member (who manages nobody) and a caller with no session user see none.
    assert direct_reports(app_dsn, client, staff_user) == []
    assert direct_reports(app_dsn, client, None) == []
    # Another tenant's caller, even holding the grant there, sees none of this tenant.
    assert direct_reports(app_dsn, client, stranger) == []

    # A disabled membership loses the read.
    owner.execute(
        "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s AND client_id=%s",
        (manager_user, client),
    )
    assert direct_reports(app_dsn, client, manager_user) == []


def test_direct_reports_function_is_not_public(owner: psycopg.Connection[Any]) -> None:
    row = owner.execute(
        "SELECT prosecdef, proconfig, "
        "has_function_privilege('csa_app', oid, 'EXECUTE'), "
        "has_function_privilege('csa_ro', oid, 'EXECUTE'), "
        "has_function_privilege('csa_identity', oid, 'EXECUTE'), "
        "has_function_privilege('public', oid, 'EXECUTE') "
        "FROM pg_proc WHERE oid = 'public.email_my_direct_reports(uuid)'::regprocedure"
    ).fetchone()
    assert row is not None
    assert row[0] is True
    assert row[1] == ["search_path=pg_catalog, public, pg_temp"]
    assert row[2:] == (True, False, False, False)


def test_0484_recorded_and_version_matches(owner: psycopg.Connection[Any]) -> None:
    recorded = owner.execute("SELECT 1 FROM schema_migrations WHERE version = '0484'").fetchone()
    assert recorded is not None
    row = owner.execute("SELECT max(version) FROM schema_migrations").fetchone()
    assert row is not None and row[0] == EXPECTED_SCHEMA_VERSION
