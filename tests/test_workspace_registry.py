"""Schema contract for the workspace assistant: proposals, directory view, relaxed turns."""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

pytestmark = requires_docker

INSERT_PROPOSAL = (
    "INSERT INTO console_proposals (client_id,user_id,login_session_id,kind,payload,"
    "payload_sha256,status,expires_at) "
    "VALUES (%s,%s,%s,%s,'{}',%s,'requested',now()+interval '1 minute')"
)
TURN_COLUMNS = (
    "INSERT INTO console_workspace_turns (client_id,thread_id,owner_user_id,session_id,"
    "request_key,fingerprint,body,action,page_key,result) "
)


async def test_proposals_table_rls_grants_and_checks(sessions: SessionWorld) -> None:
    t = sessions.tenants[0]
    rows = await sessions.q(
        "SELECT has_table_privilege('csa_ro','public.console_proposals','SELECT') r,"
        " has_table_privilege('csa_app','public.console_proposals','UPDATE') u,"
        " has_table_privilege('csa_identity','public.console_proposals','SELECT') i,"
        " (SELECT relrowsecurity FROM pg_class WHERE oid='public.console_proposals'::regclass) rls"
    )
    assert rows[0]["r"] is False and rows[0]["u"] is True
    assert rows[0]["i"] is False and rows[0]["rls"] is True
    with pytest.raises(Exception, match="console_proposals_kind_check|violates check"):
        await sessions.q(INSERT_PROPOSAL, t, sessions.admin_id, uuid4(), "nope", "0" * 64)
    with pytest.raises(Exception, match="payload_sha256|violates check"):
        await sessions.q(INSERT_PROPOSAL, t, sessions.admin_id, uuid4(), "email_export", "xyz")
    with pytest.raises(Exception, match="expires_at|violates check"):
        await sessions.q(
            "INSERT INTO console_proposals (client_id,user_id,login_session_id,kind,payload,"
            "payload_sha256,status,expires_at) VALUES (%s,%s,%s,'email_export','{}',%s,"
            "'requested',now()+interval '16 minutes')",
            t,
            sessions.admin_id,
            uuid4(),
            "0" * 64,
        )
    # Valid shape passes CHECKs and is stopped only by the session foreign key.
    with pytest.raises(Exception, match="foreign key"):
        await sessions.q(INSERT_PROPOSAL, t, sessions.admin_id, uuid4(), "email_export", "0" * 64)


async def test_company_directory_view_is_tenant_scoped(
    sessions: SessionWorld, app_dsn: str
) -> None:
    t0, t1 = sessions.tenants
    ids = [uuid4(), uuid4(), uuid4(), uuid4()]
    await sessions.q(
        "INSERT INTO hr_employees (client_id,id,display_name,job_title,work_email,is_active)"
        " VALUES (%s,%s,'Ada Synthetic','Engineer','ada@example.test',true),"
        " (%s,%s,'Bo Synthetic',NULL,NULL,true),"
        " (%s,%s,'Gone Synthetic',NULL,NULL,false),"
        " (%s,%s,'Other Tenant',NULL,NULL,true)",
        t0, ids[0], t0, ids[1], t0, ids[2], t1, ids[3],
    )  # fmt: skip
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT set_config('app.client_id', %s, true)", (t0,))
            await cur.execute("SELECT 1 FROM hr_employees")
            direct = await cur.fetchall()
            await cur.execute("SELECT * FROM company_directory ORDER BY display_name")
            rows = await cur.fetchall()
        async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT 1 FROM company_directory")
            none = await cur.fetchall()
    assert direct == []  # base table stays hidden without app.hr_user_id
    assert [r["display_name"] for r in rows] == ["Ada Synthetic", "Bo Synthetic"]
    assert {str(r["client_id"]) for r in rows} == {t0}
    assert rows[0]["job_title"] == "Engineer" and rows[0]["work_email"] == "ada@example.test"
    assert set(rows[0]) == {
        "client_id", "employee_id", "user_id", "display_name", "job_title", "work_email",
    }  # fmt: skip
    assert none == []


async def test_workspace_turn_checks_accept_new_actions_and_reject_unknown(
    sessions: SessionWorld,
) -> None:
    for name in ("console_workspace_turns_action_check", "console_workspace_turns_page_key_check"):
        rows = await sessions.q(
            "SELECT count(*) n FROM pg_constraint WHERE conname=%s"
            " AND conrelid='public.console_workspace_turns'::regclass",
            name,
        )
        assert rows[0]["n"] == 1
    rows = await sessions.q(
        "SELECT count(*) n FROM pg_constraint WHERE contype='c'"
        " AND conrelid='public.console_workspace_turns'::regclass"
        " AND pg_get_constraintdef(oid) LIKE '%%resource_id%%'"
    )
    assert rows[0]["n"] == 0
    with pytest.raises(Exception, match="console_workspace_turns_action_check"):
        await sessions.q(
            TURN_COLUMNS + "VALUES (%s,%s,%s,%s,%s,%s,'x','bogus','chat','{}')",
            sessions.tenants[0], uuid4(), sessions.admin_id, uuid4(), uuid4(), "0" * 64,
        )  # fmt: skip
    with pytest.raises(Exception, match="console_workspace_turns_page_key_check"):
        await sessions.q(
            TURN_COLUMNS + "VALUES (%s,%s,%s,%s,%s,%s,'x','navigate','bogus','{}')",
            sessions.tenants[0], uuid4(), sessions.admin_id, uuid4(), uuid4(), "0" * 64,
        )  # fmt: skip
