"""Synthetic count transactions, not application SQL or real identity integration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal, LiteralString
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
import pytest
import pytest_asyncio
from csa.inventory.contracts import (
    InventoryAccountKey,
    StockChangeAuditEvent,
    StockChangeCommand,
    StockChangeDecision,
    StockChangeRecord,
    TenantAdminEvidence,
    require_exact_stock_change_replay,
)
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def count_db(pg_container: object) -> AsyncIterator[str]:
    dsn = owner_dsn_for(pg_container)
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute(
            """
            CREATE SCHEMA count_trial;
            CREATE TABLE count_trial.actors (
                id text PRIMARY KEY, enabled boolean NOT NULL
            );
            INSERT INTO count_trial.actors VALUES
                ('submitter',true), ('reviewer',true), ('other',true);
            CREATE TABLE count_trial.account (
                id integer PRIMARY KEY CHECK (id=1),
                version bigint NOT NULL, authority bigint NOT NULL,
                on_hand integer NOT NULL CHECK (on_hand>=0),
                reserved integer NOT NULL CHECK (reserved>=0)
            );
            INSERT INTO count_trial.account VALUES (1,0,1,5,0);
            CREATE TABLE count_trial.commands (
                id text PRIMARY KEY,
                submitter text NOT NULL REFERENCES count_trial.actors(id),
                proposed integer NOT NULL CHECK (proposed>=0),
                expected bigint NOT NULL, authority bigint NOT NULL,
                before_qty integer NOT NULL,
                state text NOT NULL CHECK (state IN ('pending','recorded','approved','rejected')),
                reviewer text REFERENCES count_trial.actors(id), reason text,
                fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
                command_payload jsonb NOT NULL,
                decision_payload jsonb,
                resulting_version bigint,
                record_payload jsonb NOT NULL
            );
            CREATE UNIQUE INDEX one_pending ON count_trial.commands ((true))
                WHERE state='pending';
            CREATE TABLE count_trial.events (
                source text PRIMARY KEY,
                actor text NOT NULL,
                kind text NOT NULL CHECK (
                    kind IN ('pending','recorded','approved','rejected','reserve','fulfil')
                ),
                before_qty integer NOT NULL, after_qty integer NOT NULL,
                version_before bigint NOT NULL, version_after bigint NOT NULL,
                observed_authority bigint NOT NULL,
                event_id uuid UNIQUE NOT NULL,
                payload jsonb
            );
            CREATE FUNCTION count_trial.guard_command_transition()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.state NOT IN ('pending','recorded') THEN
                        RAISE EXCEPTION 'count command must start pending or recorded'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF OLD.state <> 'pending'
                    OR NEW.state NOT IN ('approved','rejected')
                    OR NEW.id IS DISTINCT FROM OLD.id
                    OR NEW.submitter IS DISTINCT FROM OLD.submitter
                    OR NEW.proposed IS DISTINCT FROM OLD.proposed
                    OR NEW.expected IS DISTINCT FROM OLD.expected
                    OR NEW.authority IS DISTINCT FROM OLD.authority
                    OR NEW.before_qty IS DISTINCT FROM OLD.before_qty
                    OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint
                    OR NEW.command_payload IS DISTINCT FROM OLD.command_payload THEN
                    RAISE EXCEPTION 'invalid count command transition'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER command_transition_guard
                BEFORE INSERT OR UPDATE ON count_trial.commands
                FOR EACH ROW EXECUTE FUNCTION count_trial.guard_command_transition();

            CREATE FUNCTION count_trial.require_command_audit()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM count_trial.events e
                    WHERE e.source = NEW.id || ':' || NEW.state
                      AND e.kind = NEW.state
                      AND e.event_id::text = e.payload->>'event_id'
                      AND e.payload->>'kind' = NEW.state
                      AND e.payload->>'fingerprint' = NEW.fingerprint
                      AND e.payload->'command' = NEW.command_payload
                      AND COALESCE(e.payload->'decision', 'null'::jsonb)
                          IS NOT DISTINCT FROM
                          COALESCE(NEW.decision_payload, 'null'::jsonb)
                ) THEN
                    RAISE EXCEPTION 'count command transition requires matching audit event'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'count_command_audit_required';
                END IF;
                RETURN NULL;
            END;
            $$;
            CREATE CONSTRAINT TRIGGER command_audit_required
                AFTER INSERT OR UPDATE OF state ON count_trial.commands
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION count_trial.require_command_audit();

            CREATE FUNCTION count_trial.reject_event_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'count audit events are append-only'
                    USING ERRCODE = '42501';
            END;
            $$;
            CREATE TRIGGER events_append_only
                BEFORE UPDATE OR DELETE OR TRUNCATE ON count_trial.events
                FOR EACH STATEMENT EXECUTE FUNCTION count_trial.reject_event_mutation();

            CREATE ROLE count_trial_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                NOREPLICATION NOBYPASSRLS NOINHERIT;
            GRANT USAGE ON SCHEMA count_trial TO count_trial_app;
            GRANT SELECT, INSERT, UPDATE ON count_trial.commands TO count_trial_app;
            GRANT SELECT, INSERT ON count_trial.events TO count_trial_app;
            GRANT SELECT, UPDATE ON count_trial.account TO count_trial_app;
            """
        )
    try:
        yield dsn
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            await conn.execute("DROP SCHEMA count_trial CASCADE")
            await conn.execute("DROP OWNED BY count_trial_app")
            await conn.execute("DROP ROLE count_trial_app")


async def _act(
    dsn: str,
    action: Literal["request", "approve", "reject", "reserve", "fulfil", "revoke"],
    *,
    key: str = "count",
    actor: str = "submitter",
    proposed: int = 4,
    expected: int = 0,
    reason: str = "Synthetic review",
    before_on_hand: int | None = None,
    authority_revision: int = 1,
    barrier: asyncio.Barrier | None = None,
    locked: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
    fail_after: Literal["account", "command", "event"] | None = None,
    omit_event: bool = False,
    wrong_event_payload: bool = False,
) -> str:
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '5s'")
        if barrier is not None:
            await asyncio.wait_for(barrier.wait(), 10)
        cursor = await conn.execute(
            "SELECT enabled FROM count_trial.actors WHERE id=%s FOR UPDATE", (actor,)
        )
        identity = await cursor.fetchone()
        if identity is None or not identity[0]:
            raise PermissionError("synthetic actor disabled")
        actor_evidence = TenantAdminEvidence(
            client_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            user_id=uuid5(NAMESPACE_URL, f"count-trial:user:{actor}"),
            session_id=uuid5(NAMESPACE_URL, f"count-trial:session:{actor}"),
            role="tenant_admin",
            verified_at=datetime(2026, 10, 19, tzinfo=UTC),
        )
        if locked is not None:
            locked.set()
        if release is not None:
            await asyncio.wait_for(release.wait(), 10)
        if action == "revoke":
            await conn.execute("UPDATE count_trial.actors SET enabled=false WHERE id=%s", (actor,))
            return "revoked"
        cursor = await conn.execute(
            "SELECT version,authority,on_hand,reserved FROM count_trial.account "
            "WHERE id=1 FOR UPDATE"
        )
        account = await cursor.fetchone()
        assert account is not None
        version, authority, on_hand, reserved = account
        cursor = await conn.execute(
            "SELECT submitter,proposed,expected,authority,before_qty,state,reviewer,reason,"
            "fingerprint,command_payload,decision_payload,resulting_version,record_payload "
            "FROM count_trial.commands WHERE id=%s FOR UPDATE",
            (key,),
        )
        command = await cursor.fetchone()
        if action == "request":
            command_model = StockChangeCommand(
                command_id=uuid5(NAMESPACE_URL, f"count-trial:command:{key}"),
                account=InventoryAccountKey(
                    client_id=actor_evidence.client_id,
                    product_id=UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"),
                    warehouse_id=UUID("aaaaaaaa-4444-4444-8444-aaaaaaaaaaaa"),
                ),
                submitter=actor_evidence,
                kind="physical_count",
                reason=reason,
                expected_version=expected,
                expected_authority_revision=authority_revision,
                before_on_hand=on_hand if before_on_hand is None else before_on_hand,
                value=proposed,
            )
            if command is None and command_model.before_on_hand != on_hand:
                raise ValueError("stale before quantity")
        else:
            command_model = None
        after, next_version = on_hand, version
        audit_model = None
        if action == "request":
            if command_model is None:
                raise AssertionError("request command was not constructed")
            if command is not None:
                stored_command = StockChangeCommand.model_validate(command[9])
                require_exact_stock_change_replay(stored_command, command_model)
                if command[8] != command_model.fingerprint():
                    raise ValueError("stored fingerprint mismatch")
                stored_record = StockChangeRecord.model_validate(command[12])
                stored_record.replay(command_model)
                return command[5]
            if version != expected or authority != authority_revision:
                raise ValueError("stale request")
            cursor = await conn.execute("SELECT id FROM count_trial.commands WHERE state='pending'")
            if await cursor.fetchone() is not None:
                raise ValueError("pending count")
            outcome = "recorded" if not command_model.requires_second_admin else "pending"
            record = StockChangeRecord(
                command=command_model,
                state=outcome,
                resulting_version=version if outcome == "recorded" else None,
            )
            await conn.execute(
                "INSERT INTO count_trial.commands "
                "(id,submitter,proposed,expected,authority,before_qty,state,fingerprint,"
                "command_payload,resulting_version,record_payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    key,
                    actor,
                    command_model.proposed_on_hand,
                    expected,
                    authority,
                    command_model.before_on_hand,
                    outcome,
                    command_model.fingerprint(),
                    Jsonb(command_model.model_dump(mode="json")),
                    record.resulting_version,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
            audit_kind = outcome
            audit_model = StockChangeAuditEvent(
                event_id=uuid5(NAMESPACE_URL, f"count-trial:event:{key}:{outcome}"),
                command=command_model,
                kind=audit_kind,
                actor=actor_evidence,
                occurred_at=datetime(2026, 10, 19, 1, tzinfo=UTC),
                fingerprint=command_model.fingerprint(),
                observed_version=version,
                observed_authority_revision=authority,
                before_on_hand=on_hand,
                after_on_hand=on_hand,
                resulting_version=version,
            )
        elif action in {"approve", "reject"}:
            if command is None:
                raise ValueError("missing command")
            stored_command = StockChangeCommand.model_validate(command[9])
            outcome = "approved" if action == "approve" else "rejected"
            if command[5] != "pending":
                stored_record = StockChangeRecord.model_validate(command[12])
                if stored_record.state == "recorded":
                    raise ValueError("decision replay differs")
                stored_decision = stored_record.decision
                if stored_decision is None:
                    raise AssertionError("terminal count record is missing its decision")
                candidate_decision = StockChangeDecision(
                    command=stored_command,
                    reviewer=actor_evidence,
                    outcome=outcome,
                    reason=reason,
                )
                if (
                    stored_decision.reviewer.user_id != candidate_decision.reviewer.user_id
                    or stored_decision.outcome != candidate_decision.outcome
                    or stored_decision.reason != candidate_decision.reason
                ):
                    raise ValueError("decision replay differs")
                return outcome
            if actor_evidence.user_id == stored_command.submitter.user_id:
                raise ValueError("self review")
            candidate_decision = StockChangeDecision(
                command=stored_command,
                reviewer=actor_evidence,
                outcome=outcome,
                reason=reason,
            )
            if action == "approve":
                if (
                    version,
                    authority,
                    on_hand,
                ) != (
                    command[2],
                    command[3],
                    command[4],
                ):
                    raise ValueError("stale approval")
                after, next_version = stored_command.proposed_on_hand, version + 1
                await conn.execute(
                    "UPDATE count_trial.account SET on_hand=%s,version=%s WHERE id=1",
                    (after, next_version),
                )
                if fail_after == "account":
                    raise RuntimeError("injected after account")
                audit_before = on_hand
                audit_after = after
                audit_version = version
                audit_authority = authority
            else:
                audit_before = on_hand
                audit_after = on_hand
                audit_version = version
                audit_authority = authority
            record = StockChangeRecord(
                command=stored_command,
                state=outcome,
                decision=candidate_decision,
                resulting_version=next_version if outcome == "approved" else None,
            )
            await conn.execute(
                "UPDATE count_trial.commands SET state=%s,reviewer=%s,reason=%s,"
                "decision_payload=%s,resulting_version=%s,record_payload=%s WHERE id=%s",
                (
                    outcome,
                    actor,
                    candidate_decision.reason,
                    Jsonb(candidate_decision.model_dump(mode="json")),
                    record.resulting_version,
                    Jsonb(record.model_dump(mode="json")),
                    key,
                ),
            )
            audit_model = StockChangeAuditEvent(
                event_id=uuid5(NAMESPACE_URL, f"count-trial:event:{key}:{outcome}"),
                command=stored_command,
                kind=outcome,
                actor=actor_evidence,
                occurred_at=datetime(2026, 10, 19, 2, tzinfo=UTC),
                fingerprint=stored_command.fingerprint(),
                observed_version=audit_version,
                observed_authority_revision=audit_authority,
                before_on_hand=audit_before,
                after_on_hand=audit_after,
                resulting_version=next_version if outcome == "approved" else audit_version,
                decision=candidate_decision,
            )
        else:
            outcome = action
            if action == "reserve":
                cursor = await conn.execute(
                    "SELECT id FROM count_trial.commands WHERE state='pending'"
                )
                if await cursor.fetchone() is not None:
                    raise ValueError("count freeze")
                if on_hand - reserved < 1:
                    raise ValueError("unavailable")
                reserved += 1
            else:
                if reserved < 1 or on_hand < 1:
                    raise ValueError("no claim")
                reserved -= 1
                after -= 1
            next_version += 1
            await conn.execute(
                "UPDATE count_trial.account SET on_hand=%s,reserved=%s,version=%s WHERE id=1",
                (after, reserved, next_version),
            )
        if fail_after == "command":
            raise RuntimeError("injected after command")
        if not omit_event:
            audit_payload = audit_model.model_dump(mode="json") if audit_model is not None else None
            if wrong_event_payload and audit_payload is not None:
                audit_payload["fingerprint"] = "0" * 64
            await conn.execute(
                "INSERT INTO count_trial.events "
                "(source,actor,kind,before_qty,after_qty,version_before,version_after,"
                "observed_authority,event_id,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"{key}:{outcome}",
                    actor,
                    "invalid"
                    if fail_after == "event"
                    else (audit_model.kind if audit_model is not None else outcome),
                    on_hand,
                    after,
                    version,
                    next_version,
                    authority,
                    audit_model.event_id
                    if audit_model is not None
                    else uuid5(NAMESPACE_URL, f"count-trial:event:{key}:{outcome}"),
                    Jsonb(audit_payload) if audit_payload is not None else None,
                ),
            )
        return outcome


async def _snapshot(dsn: str) -> tuple[list[tuple], list[tuple], list[tuple]]:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cursor = await conn.execute("SELECT * FROM count_trial.account")
        account = await cursor.fetchall()
        cursor = await conn.execute("SELECT * FROM count_trial.commands ORDER BY id")
        commands = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT source,actor,kind,before_qty,after_qty,version_before,version_after,"
            "observed_authority FROM count_trial.events ORDER BY source"
        )
        return account, commands, await cursor.fetchall()


async def _audit_payloads(dsn: str) -> list[tuple]:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cursor = await conn.execute(
            "SELECT source,event_id,payload FROM count_trial.events "
            "WHERE payload IS NOT NULL ORDER BY source"
        )
        return await cursor.fetchall()


async def _assert_persisted_contracts(dsn: str) -> None:
    records: dict[UUID, StockChangeRecord] = {}
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cursor = await conn.execute(
            "SELECT fingerprint,command_payload,decision_payload,resulting_version,record_payload "
            "FROM count_trial.commands"
        )
        for (
            fingerprint,
            command_payload,
            decision_payload,
            resulting_version,
            record_payload,
        ) in await cursor.fetchall():
            command = StockChangeCommand.model_validate(command_payload)
            assert fingerprint == command.fingerprint()
            record = StockChangeRecord.model_validate(record_payload)
            assert record.command == command
            assert record.resulting_version == resulting_version
            records[command.command_id] = record
            assert record.decision == (
                StockChangeDecision.model_validate(decision_payload)
                if decision_payload is not None
                else None
            )
        for _source, event_id, payload in await _audit_payloads(dsn):
            event = StockChangeAuditEvent.model_validate(payload)
            assert event.event_id == event_id
            assert event.fingerprint == event.command.fingerprint()
            record = records[event.command.command_id]
            assert event.command == record.command
            if event.kind in {"approved", "rejected"}:
                assert event.kind == record.state
                assert event.decision == record.decision
            elif event.kind == "recorded":
                assert record.state == "recorded"
            else:
                assert event.kind == "pending"
                assert event.decision is None


async def _race(*operations) -> list:
    return await asyncio.wait_for(asyncio.gather(*operations, return_exceptions=True), 20)


async def test_request_and_reservation_serialize_without_pending_quantity_funding(count_db: str):
    barrier = asyncio.Barrier(2)
    results = await _race(
        _act(count_db, "request", barrier=barrier),
        _act(count_db, "reserve", actor="other", key="line", barrier=barrier),
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    account, commands, events = await _snapshot(count_db)
    if results[0] == "pending":
        assert account == [(1, 0, 1, 5, 0)]
        assert commands[0][6] == "pending"
        assert events == [("count:pending", "submitter", "pending", 5, 5, 0, 0, 1)]
    else:
        assert results[1] == "reserve"
        assert account == [(1, 1, 1, 5, 1)]
        assert commands == []
        assert events == [("line:reserve", "other", "reserve", 5, 5, 0, 1, 1)]
    await _assert_persisted_contracts(count_db)


@pytest.mark.parametrize(
    ("action", "event_fault"),
    [
        ("request", "omit"),
        ("request", "mismatch"),
        ("approve", "omit"),
    ],
)
async def test_command_transition_cannot_commit_without_matching_audit(
    count_db: str,
    action: Literal["request", "approve"],
    event_fault: Literal["omit", "mismatch"],
):
    if action == "approve":
        await _act(count_db, "request")
    before = await _snapshot(count_db)
    with pytest.raises(psycopg.errors.CheckViolation) as error:
        await _act(
            count_db,
            action,
            actor="reviewer" if action == "approve" else "submitter",
            omit_event=event_fault == "omit",
            wrong_event_payload=event_fault == "mismatch",
        )
    assert error.value.diag.constraint_name == "count_command_audit_required"
    assert await _snapshot(count_db) == before


@pytest.mark.parametrize("mutation", ["update", "delete", "truncate"])
async def test_audit_events_reject_mutation(
    count_db: str, mutation: Literal["update", "delete", "truncate"]
):
    await _act(count_db, "request")
    before = await _snapshot(count_db)
    statements: dict[Literal["update", "delete", "truncate"], LiteralString] = {
        "update": "UPDATE count_trial.events SET actor='other' WHERE source='count:pending'",
        "delete": "DELETE FROM count_trial.events WHERE source='count:pending'",
        "truncate": "TRUNCATE count_trial.events",
    }
    async with await psycopg.AsyncConnection.connect(count_db) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute(statements[mutation])
    assert await _snapshot(count_db) == before


async def test_terminal_count_transition_cannot_be_replaced(count_db: str):
    await _act(count_db, "request")
    await _act(count_db, "approve", actor="reviewer")
    before = await _snapshot(count_db)
    async with await psycopg.AsyncConnection.connect(count_db) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE count_trial.commands SET state='rejected' WHERE id='count'"
                )
    assert await _snapshot(count_db) == before


async def test_count_command_fingerprint_and_payload_are_immutable(count_db: str):
    await _act(count_db, "request")
    before = await _snapshot(count_db)
    async with await psycopg.AsyncConnection.connect(count_db) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE count_trial.commands SET fingerprint=%s WHERE id='count'",
                    ("0" * 64,),
                )
    assert await _snapshot(count_db) == before


async def test_non_owner_writer_can_commit_audited_request_but_cannot_bypass_boundary(
    count_db: str,
):
    actor = TenantAdminEvidence(
        client_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        user_id=uuid5(NAMESPACE_URL, "count-trial:user:submitter"),
        session_id=uuid5(NAMESPACE_URL, "count-trial:session:submitter"),
        role="tenant_admin",
        verified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    command = StockChangeCommand(
        command_id=uuid5(NAMESPACE_URL, "count-trial:command:count"),
        account=InventoryAccountKey(
            client_id=actor.client_id,
            product_id=UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"),
            warehouse_id=UUID("aaaaaaaa-4444-4444-8444-aaaaaaaaaaaa"),
        ),
        submitter=actor,
        kind="physical_count",
        reason="Synthetic count",
        expected_version=0,
        expected_authority_revision=1,
        before_on_hand=5,
        value=4,
    )
    record = StockChangeRecord(command=command, state="pending")
    event = StockChangeAuditEvent(
        event_id=uuid5(NAMESPACE_URL, "count-trial:event:count:pending"),
        command=command,
        kind="pending",
        actor=actor,
        occurred_at=datetime(2026, 10, 19, 1, tzinfo=UTC),
        fingerprint=command.fingerprint(),
        observed_version=0,
        observed_authority_revision=1,
        before_on_hand=5,
        after_on_hand=5,
        resulting_version=0,
    )

    async def insert_command(conn: psycopg.AsyncConnection, key: str = "count") -> None:
        await conn.execute(
            "INSERT INTO count_trial.commands "
            "(id,submitter,proposed,expected,authority,before_qty,state,fingerprint,"
            "command_payload,record_payload) VALUES (%s,'submitter',4,0,1,5,'pending',%s,%s,%s)",
            (
                key,
                command.fingerprint(),
                Jsonb(command.model_dump(mode="json")),
                Jsonb(record.model_dump(mode="json")),
            ),
        )

    async with await psycopg.AsyncConnection.connect(count_db, autocommit=True) as conn:
        await conn.execute("SET ROLE count_trial_app")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("ALTER TABLE count_trial.events DISABLE TRIGGER ALL")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SET session_replication_role = replica")
        with pytest.raises(psycopg.errors.CheckViolation) as error:
            async with conn.transaction():
                await insert_command(conn)
        assert error.value.diag.constraint_name == "count_command_audit_required"
        assert await _snapshot(count_db) == ([(1, 0, 1, 5, 0)], [], [])
        async with conn.transaction():
            await insert_command(conn)
            await conn.execute(
                "INSERT INTO count_trial.events "
                "(source,actor,kind,before_qty,after_qty,version_before,version_after,"
                "observed_authority,event_id,payload) "
                "VALUES ('count:pending','submitter','pending',5,5,0,0,1,%s,%s)",
                (event.event_id, Jsonb(event.model_dump(mode="json"))),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE count_trial.events SET actor='other' WHERE source='count:pending'"
                )
    await _assert_persisted_contracts(count_db)


async def test_two_pending_requests_and_exact_request_replay(count_db: str):
    barrier = asyncio.Barrier(2)
    results = await _race(
        _act(count_db, "request", key="a", barrier=barrier),
        _act(count_db, "request", key="b", actor="other", barrier=barrier),
    )
    assert results.count("pending") == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    winner = ("a", "b")[results.index("pending")]
    before = await _snapshot(count_db)
    actor = "submitter" if winner == "a" else "other"
    assert await _act(count_db, "request", key=winner, actor=actor) == "pending"
    with pytest.raises(ValueError, match="replay differs"):
        await _act(count_db, "request", key=winner, actor=actor, proposed=3)
    assert await _snapshot(count_db) == before
    await _assert_persisted_contracts(count_db)


@pytest.mark.parametrize("actions", [("approve", "approve"), ("approve", "reject")])
async def test_decision_races_and_terminal_replay(count_db: str, actions):
    await _act(count_db, "request")
    barrier = asyncio.Barrier(2)
    results = await _race(
        *(_act(count_db, action, actor="reviewer", barrier=barrier) for action in actions)
    )
    account, commands, events = await _snapshot(count_db)
    state = commands[0][6]
    assert state in {"approved", "rejected"}
    assert len(events) == 2
    assert account == [(1, 1 if state == "approved" else 0, 1, 4 if state == "approved" else 5, 0)]
    assert results.count(state) == (2 if actions[0] == actions[1] else 1)
    assert await _act(count_db, actions[results.index(state)], actor="reviewer") == state
    before = await _snapshot(count_db)
    with pytest.raises(ValueError, match="replay differs"):
        await _act(
            count_db,
            "approve" if state == "approved" else "reject",
            actor="reviewer",
            reason="Conflicting reason",
        )
    assert await _snapshot(count_db) == before
    assert events == sorted(
        [
            ("count:pending", "submitter", "pending", 5, 5, 0, 0, 1),
            (f"count:{state}", "reviewer", state, 5, account[0][3], 0, account[0][1], 1),
        ]
    )
    await _assert_persisted_contracts(count_db)


async def test_fulfilment_can_stale_count_and_rejection_clears_freeze(count_db: str):
    await _act(count_db, "reserve", key="line")
    await _act(count_db, "request", expected=1)
    await _act(count_db, "fulfil", key="line")
    before = await _snapshot(count_db)
    with pytest.raises(ValueError, match="stale approval"):
        await _act(count_db, "approve", actor="reviewer")
    assert await _snapshot(count_db) == before
    with pytest.raises(ValueError, match="count freeze"):
        await _act(count_db, "reserve", key="new")
    await _act(count_db, "reject", actor="reviewer")
    assert await _act(count_db, "request", expected=1, before_on_hand=5) == "rejected"
    account, commands, events = await _snapshot(count_db)
    assert account == before[0]
    assert commands[0][6] == "rejected"
    assert ("count:rejected", "reviewer", "rejected", 4, 4, 2, 2, 1) in events
    assert await _act(count_db, "reserve", key="new") == "reserve"
    await _assert_persisted_contracts(count_db)


async def test_approval_vs_fulfilment_preserves_both_or_rejects_stale(count_db: str):
    await _act(count_db, "reserve", key="line")
    await _act(count_db, "request", expected=1)
    barrier = asyncio.Barrier(2)
    results = await _race(
        _act(count_db, "approve", actor="reviewer", barrier=barrier),
        _act(count_db, "fulfil", actor="other", key="line", barrier=barrier),
    )
    assert results[1] == "fulfil"
    account, commands, events = await _snapshot(count_db)
    if results[0] == "approved":
        assert account == [(1, 3, 1, 3, 0)]
        assert commands[0][6] == "approved"
        assert len(events) == 4
    else:
        assert isinstance(results[0], ValueError) and str(results[0]) == "stale approval"
        assert account == [(1, 2, 1, 4, 0)]
        assert commands[0][6] == "pending"
        assert len(events) == 3


async def test_conflicting_reviewer_race_and_self_approval(count_db: str):
    await _act(count_db, "request")
    before = await _snapshot(count_db)
    with pytest.raises(ValueError, match="self review"):
        await _act(count_db, "approve")
    assert await _snapshot(count_db) == before
    barrier = asyncio.Barrier(2)
    results = await _race(
        _act(count_db, "approve", actor="reviewer", barrier=barrier),
        _act(count_db, "approve", actor="other", barrier=barrier),
    )
    assert results.count("approved") == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    account, commands, events = await _snapshot(count_db)
    assert account == [(1, 1, 1, 4, 0)]
    assert commands[0][7] == ("reviewer", "other")[results.index("approved")]
    assert len(events) == 2


async def test_zero_count_records_without_freeze_or_stock_version_change(count_db: str):
    assert await _act(count_db, "request", proposed=5) == "recorded"
    before = await _snapshot(count_db)
    assert before[0] == [(1, 0, 1, 5, 0)]
    assert before[2] == [("count:recorded", "submitter", "recorded", 5, 5, 0, 0, 1)]
    await _assert_persisted_contracts(count_db)
    assert await _act(count_db, "request", proposed=5) == "recorded"
    with pytest.raises(ValueError, match="replay differs"):
        await _act(count_db, "approve", actor="reviewer")
    assert await _snapshot(count_db) == before
    assert await _act(count_db, "reserve", key="line") == "reserve"


@pytest.mark.parametrize("fence", ["version", "authority", "on_hand"])
async def test_each_approval_fence_preserves_pending_until_explicit_rejection(
    count_db: str, fence: str
):
    await _act(count_db, "request")
    async with await psycopg.AsyncConnection.connect(count_db) as conn:
        if fence == "version":
            await conn.execute("UPDATE count_trial.account SET version=1")
        elif fence == "authority":
            await conn.execute("UPDATE count_trial.account SET authority=2")
        else:
            await conn.execute("UPDATE count_trial.account SET on_hand=6")
    before = await _snapshot(count_db)
    with pytest.raises(ValueError, match="stale approval"):
        await _act(count_db, "approve", actor="reviewer")
    assert await _snapshot(count_db) == before
    assert (
        await _act(count_db, "request", expected=0, authority_revision=1, before_on_hand=5)
        == "pending"
    )
    assert await _act(count_db, "reject", actor="reviewer") == "rejected"
    after = await _snapshot(count_db)
    assert after[0] == before[0]
    assert after[1][0][6:9] == ("rejected", "reviewer", "Synthetic review")
    _, version, authority, on_hand, _ = after[0][0]
    assert (
        "count:rejected",
        "reviewer",
        "rejected",
        on_hand,
        on_hand,
        version,
        version,
        authority,
    ) in after[2]


@pytest.mark.parametrize("first", ["approve", "revoke"])
async def test_revocation_and_approval_order_is_serialized(count_db: str, first):
    await _act(count_db, "request")
    locked, release = asyncio.Event(), asyncio.Event()
    first_task = asyncio.create_task(
        _act(count_db, first, actor="reviewer", locked=locked, release=release)
    )
    try:
        await asyncio.wait_for(locked.wait(), 10)
        second = asyncio.create_task(
            _act(count_db, "revoke" if first == "approve" else "approve", actor="reviewer")
        )
        release.set()
        results = await _race(first_task, second)
    finally:
        release.set()
        await asyncio.gather(first_task, return_exceptions=True)
    account, commands, events = await _snapshot(count_db)
    if first == "approve":
        assert results == ["approved", "revoked"]
        assert account == [(1, 1, 1, 4, 0)]
        assert len(events) == 2
    else:
        assert results[0] == "revoked" and isinstance(results[1], PermissionError)
        assert account == [(1, 0, 1, 5, 0)]
        assert commands[0][6] == "pending"
        assert len(events) == 1
    with pytest.raises(PermissionError):
        await _act(count_db, "approve", actor="reviewer")


@pytest.mark.parametrize(
    ("action", "failure"),
    [
        ("request", "command"),
        ("request", "event"),
        ("approve", "account"),
        ("approve", "command"),
        ("approve", "event"),
        ("reject", "command"),
        ("reject", "event"),
    ],
)
async def test_coupled_failure_rolls_back_and_retry_has_one_effect(count_db: str, action, failure):
    if action != "request":
        await _act(count_db, "request")
    actor = "submitter" if action == "request" else "reviewer"
    before = await _snapshot(count_db)
    with pytest.raises((RuntimeError, psycopg.errors.CheckViolation)):
        await _act(count_db, action, actor=actor, fail_after=failure)
    assert await _snapshot(count_db) == before
    result = await _act(count_db, action, actor=actor)
    after = await _snapshot(count_db)
    assert await _act(count_db, action, actor=actor) == result
    assert await _snapshot(count_db) == after
    assert len(after[2]) == len(before[2]) + 1
    assert after[0] == [(1, 1 if action == "approve" else 0, 1, 4 if action == "approve" else 5, 0)]
    assert after[1][0][6] == result
    assert (
        f"count:{result}",
        actor,
        result,
        5,
        4 if action == "approve" else 5,
        0,
        1 if action == "approve" else 0,
        1,
    ) in after[2]
    await _assert_persisted_contracts(count_db)
