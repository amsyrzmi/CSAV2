"""Migration 0485: email content written by the earlier email_export card is removed.

The migrated database already holds 0484 and 0485. Rows are then inserted in the shapes the
code before the counts-only cards stored (af89e81 executors.py, registry.py and service.py),
which is the state between applying 0484 and 0485 on staging, and 0485 is applied twice. The
file never writes schema_migrations, so re-running it is how a pre-0485 row meets the scrub.
Synthetic data only.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
import pytest
from csa.workspace.contracts import ProposalCard, ProposalResult
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.usefixtures("migrated_dsn")]

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "migrations" / "0485_email_scrub_old_cards.sql"
RUNBOOK = ROOT / "deploy" / "STAGING_PIPELINE.md"
# The rollout's step-4 check (deploy/STAGING_PIPELINE.md, section 11.1), verbatim: the scrub's
# own predicates, counted across both tables. 0 once 0485 has applied with old writers stopped.
CHECK_QUERY = """\
SELECT
  (SELECT count(*) FROM public.console_proposals
    WHERE kind = 'email_export'
      AND (card->>'title' = 'Save email draft to Gmail'
           OR strpos(card::text, '@') > 0
           OR jsonb_path_exists(card, '$.fields[*] ? (@.label == "To"
                  || (@.label == "Subject" && @.value != "(shown on open)"))')
           OR (jsonb_typeof(result) = 'object'
               AND result - ARRAY['draft_id', 'status', 'gmail_draft_id'] <> '{}'::jsonb)))
  + (SELECT count(*) FROM public.console_workspace_turns
    WHERE (result->>'action' = 'proposal' AND result#>>'{proposal,kind}' = 'email_export'
           AND (result#>>'{proposal,card,title}' = 'Save email draft to Gmail'
                OR strpos((result#>'{proposal,card}')::text, '@') > 0
                OR jsonb_path_exists(result, '$.proposal.card.fields[*] ? (@.label == "To"
                       || (@.label == "Subject" && @.value != "(shown on open)"))')
                OR (jsonb_typeof(result#>'{proposal,result}') = 'object'
                    AND (result#>'{proposal,result}')
                        - ARRAY['draft_id', 'status', 'gmail_draft_id'] <> '{}'::jsonb)))
       OR (result->>'action' = 'answer'
           AND result->>'markdown' ~ ('Email draft [0-9a-f-]{36}: subject ' || chr(8220) || '.*'
                                      || chr(8221) || ', status [a-z_]+\\.')))
  AS old_rows;
"""

OLD_TO = "scrub-recipient@example.test"
OLD_SUBJECT = "Scrub subject quarterly figures"
OLD_BODY = "Scrub body: the synthetic quarterly figures are attached."
CONTENT = (OLD_TO, OLD_SUBJECT, OLD_BODY, "@")
TYPED = "Email scrub-typed@example.test the quarterly figures"
NEW_TITLE = "Save email to Gmail drafts"
NEW_SUMMARY = "1 in To. Nothing is sent."
SUBJECT_FIELD = {"label": "Subject", "value": "(shown on open)"}
VERSION = "a" * 64
EXPIRES = "2026-10-28T09:15:00+00:00"


@pytest.fixture
def owner(pg_container: object) -> Any:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        yield conn


class World:
    def __init__(self, owner: psycopg.Connection[Any]) -> None:
        self.owner = owner
        self.client = uuid.uuid4()
        self.user = uuid.uuid4()
        self.session = uuid.uuid4()
        self.thread = uuid.uuid4()
        owner.execute(
            "INSERT INTO clients(id,slug,name,platform_version) "
            "VALUES (%s,%s,'Synthetic scrub','b-test')",
            (self.client, f"email-scrub-{self.client}"),
        )
        owner.execute(
            "INSERT INTO console_users(id,email,display_name) VALUES (%s,%s,'Synthetic member')",
            (self.user, f"synthetic-{self.user}@example.test"),
        )
        owner.execute(
            "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_staff')",
            (self.user, self.client),
        )
        owner.execute(
            "INSERT INTO console_sessions(id,user_id,token_hash,csrf_hash,client_id,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,now()+interval '1 hour')",
            (self.session, self.user, uuid.uuid4().hex * 2, uuid.uuid4().hex * 2, self.client),
        )
        owner.execute(
            "INSERT INTO console_workspace_threads(id,client_id,owner_user_id,create_request_key) "
            "VALUES (%s,%s,%s,%s)",
            (self.thread, self.client, self.user, uuid.uuid4()),
        )

    def proposal(
        self,
        kind: str,
        payload: dict[str, Any],
        card: dict[str, Any],
        *,
        status: str = "requested",
        result: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        proposal = uuid.uuid4()
        self.owner.execute(
            "INSERT INTO console_proposals(id,client_id,user_id,login_session_id,thread_id,kind,"
            "payload,payload_sha256,expected_version,card,origin,status,expires_at,executed_at,"
            "result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'assistant',%s,"
            "now()+interval '15 minutes',CASE WHEN %s THEN now() END,%s)",
            (
                proposal,
                self.client,
                self.user,
                self.session,
                self.thread,
                kind,
                Jsonb(payload),
                "b" * 64,
                VERSION,
                Jsonb(card),
                status,
                status == "executed",
                Jsonb(result) if result is not None else None,
            ),
        )
        return proposal

    def turn(self, action: str, result: dict[str, Any], body: str = TYPED) -> int:
        row = self.owner.execute(
            "INSERT INTO console_workspace_turns(client_id,thread_id,owner_user_id,session_id,"
            "request_key,fingerprint,body,action,page_key,result) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'email',%s) RETURNING id",
            (
                self.client,
                self.thread,
                self.user,
                self.session,
                uuid.uuid4(),
                "c" * 64,
                body,
                action,
                Jsonb(result),
            ),
        ).fetchone()
        assert row is not None
        return cast(int, row[0])

    def audit(self, action: str, target: str, details: dict[str, Any]) -> None:
        self.owner.execute(
            "INSERT INTO console_audit(actor_id,client_id,action,target_id,details) "
            "VALUES (%s,%s,%s,%s,%s)",
            (self.user, self.client, action, target, Jsonb(details)),
        )

    def proposals(self) -> dict[uuid.UUID, tuple[str, Any, Any, str]]:
        rows = self.owner.execute(
            "SELECT id, p::text, card, result, xmin::text FROM console_proposals p "
            "WHERE client_id=%s",
            (self.client,),
        ).fetchall()
        return {row[0]: row[1:] for row in rows}

    def turns(self) -> dict[int, tuple[str, str, Any, str]]:
        rows = self.owner.execute(
            "SELECT id, t::text, body, result, xmin::text FROM console_workspace_turns t "
            "WHERE client_id=%s",
            (self.client,),
        ).fetchall()
        return {row[0]: row[1:] for row in rows}

    def audits(self) -> list[tuple[str, str]]:
        rows = self.owner.execute(
            "SELECT a::text, xmin::text FROM console_audit a WHERE client_id=%s ORDER BY id",
            (self.client,),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]


def old_card() -> dict[str, Any]:
    """The card af89e81's EmailExportExecutor stored (registry.insert)."""
    return {
        "kind": "email_export",
        "title": "Save email draft to Gmail",
        "summary": f"Save the reply to {OLD_TO} in your Gmail drafts. Nothing is sent.",
        "fields": [{"label": "To", "value": OLD_TO}, {"label": "Subject", "value": OLD_SUBJECT}],
        "expected_version": VERSION,
    }


def old_result(draft: uuid.UUID) -> dict[str, Any]:
    """Af89e81's execute() result: the whole DraftOut."""
    return {
        "id": str(draft),
        "thread_id": "gmail-thread-1",
        "recipient": OLD_TO,
        "subject": OLD_SUBJECT,
        "body": OLD_BODY,
        "status": "exported",
        "source": "model",
        "gmail_draft_id": "gmail-draft-1",
        "created_at": "2026-10-28T09:00:00+00:00",
        "updated_at": "2026-10-28T09:05:00+00:00",
    }


def new_card(draft: uuid.UUID) -> dict[str, Any]:
    """What this branch's EmailExportExecutor stores."""
    return {
        "kind": "email_export",
        "title": "Save email to Gmail drafts for 2 people (1 outside your company)",
        "summary": "1 in To, 1 in CC. Nothing is sent.",
        "fields": [SUBJECT_FIELD, {"label": "Draft", "value": str(draft)}],
        "expected_version": VERSION,
    }


def embedded(proposal: uuid.UUID, card: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """A turn's stored ProposalResult: the ProposalOut registry.insert returned."""
    return {
        "action": "proposal",
        "proposal": {
            "id": str(proposal),
            "kind": card["kind"],
            "status": "requested",
            "card": {**card, "proposal_id": str(proposal), "expires_at": EXPIRES},
            "result": None,
            "created_at": "2026-10-28T09:00:00+00:00",
            "expires_at": EXPIRES,
            **extra,
        },
    }


def apply_migration(owner: psycopg.Connection[Any]) -> None:
    owner.execute(cast(LiteralString, MIGRATION.read_text()))


def old_rows(owner: psycopg.Connection[Any]) -> int:
    row = owner.execute(cast(LiteralString, CHECK_QUERY)).fetchone()
    assert row is not None
    return cast(int, row[0])


def test_runbook_check_query_is_the_tested_one() -> None:
    assert CHECK_QUERY in RUNBOOK.read_text(encoding="utf-8")
    assert CHECK_QUERY.isascii() and MIGRATION.read_text(encoding="utf-8").isascii()


def assert_no_content(text: str) -> None:
    for value in CONTENT:
        assert value not in text


def test_scrub_rewrites_old_email_rows_only(owner: psycopg.Connection[Any]) -> None:
    w = World(owner)
    draft, executed_draft, new_draft = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    requested = w.proposal("email_export", {"draft_id": str(draft)}, old_card())
    executed = w.proposal(
        "email_export",
        {"draft_id": str(executed_draft)},
        old_card(),
        status="executed",
        result=old_result(executed_draft),
    )
    current = w.proposal(
        "email_export",
        {"draft_id": str(new_draft)},
        new_card(new_draft),
        status="executed",
        result={"draft_id": str(new_draft), "status": "exported", "gmail_draft_id": "gd-2"},
    )
    # Other kinds keep their cards even where a field looks like email content.
    others = [
        w.proposal(
            "training_submit",
            {"title": "Scrub training"},
            {
                "kind": "training_submit",
                "title": "Submit a new training request",
                "summary": f"Create and submit “{OLD_SUBJECT}” for approval.",
                "fields": [{"label": "To", "value": OLD_TO}],
                "expected_version": None,
            },
        ),
        w.proposal(
            "inbox_decision",
            {"approval_id": 1, "conversation_id": str(uuid.uuid4()), "decision": "approve"},
            {
                "kind": "inbox_decision",
                "title": "Approve held reply",
                "summary": "Approve the held reply to this customer conversation.",
                "fields": [{"label": "Reply", "value": f"Write to {OLD_TO}"}],
                "expected_version": "3",
            },
            status="executed",
            result={"status": "approved", "subject": OLD_SUBJECT},
        ),
        w.proposal(
            "subscription_action",
            {"subscription_id": str(uuid.uuid4()), "action": "pause"},
            {
                "kind": "subscription_action",
                "title": "Save email draft to Gmail",
                "summary": f"Contact {OLD_TO}",
                "fields": [],
                "expected_version": "1",
            },
        ),
    ]

    linked = w.turn("email_compose", embedded(requested, old_card()))
    orphan_proposal = uuid.uuid4()  # its proposal row is gone: no draft id to recover
    orphan = w.turn("email_compose", embedded(orphan_proposal, old_card()))
    answer = w.turn(
        "answer",
        {
            "action": "answer",
            "markdown": (
                "Here is the open draft.\n\n"
                f"- Email draft {draft}: subject “{OLD_SUBJECT} “quoted”, too”, status draft."
            ),
        },
    )
    kept_turns = [
        w.turn("email_compose", embedded(current, new_card(new_draft))),
        w.turn("answer", {"action": "answer", "markdown": f"Training “{OLD_SUBJECT}”."}),
        w.turn(
            "directory_lookup",
            {
                "action": "directory_lookup",
                "candidates": [
                    {
                        "employee_id": str(uuid.uuid4()),
                        "display_name": "Synthetic Colleague",
                        "job_title": None,
                        "work_email": "colleague@example.test",
                    }
                ],
            },
        ),
        w.turn("training_submit", embedded(others[0], old_card() | {"kind": "training_submit"})),
    ]
    w.audit("workspace.proposal.created", str(requested), {"kind": "email_export"})
    w.audit(
        "workspace.turn.completed",
        str(w.thread),
        {"thread_id": str(w.thread), "action": "email_compose"},
    )

    before_p, before_t, before_a = w.proposals(), w.turns(), w.audits()
    # The synthetic rows really hold the earlier content before the scrub runs.
    assert OLD_TO in before_p[requested][0] and OLD_BODY in before_p[executed][0]
    assert OLD_SUBJECT in before_t[linked][0] and OLD_SUBJECT in before_t[answer][0]
    # The runbook check sees the five old-shape rows: two proposals, three turns.
    assert old_rows(owner) == 5
    apply_migration(owner)
    after_p, after_t = w.proposals(), w.turns()

    # Proposals: counts-only card with the draft id, result reduced to the three keys.
    for proposal, draft_id in ((requested, draft), (executed, executed_draft)):
        text, card, _, _ = after_p[proposal]
        assert_no_content(text)
        assert card == {
            "kind": "email_export",
            "title": NEW_TITLE,
            "summary": NEW_SUMMARY,
            "fields": [SUBJECT_FIELD, {"label": "Draft", "value": str(draft_id)}],
            "expected_version": VERSION,
        }
        ProposalCard.model_validate_json(
            json.dumps({**card, "proposal_id": str(proposal), "expires_at": EXPIRES})
        )
    assert after_p[requested][2] is None
    assert after_p[executed][2] == {
        "draft_id": str(executed_draft),
        "status": "exported",
        "gmail_draft_id": "gmail-draft-1",
    }
    for proposal in [current, *others]:
        assert after_p[proposal] == before_p[proposal]

    # Turns: the embedded card copy is rewritten the same way; the user's text is kept.
    for turn, draft_id in ((linked, draft), (orphan, None)):
        text, body, result, _ = after_t[turn]
        assert_no_content(text.replace(TYPED, ""))
        assert body == TYPED
        card = result["proposal"]["card"]
        assert card["title"] == NEW_TITLE and card["summary"] == NEW_SUMMARY
        fields = [SUBJECT_FIELD]
        if draft_id is not None:
            fields.append({"label": "Draft", "value": str(draft_id)})
        assert card["fields"] == fields
        assert card["expected_version"] == VERSION and card["expires_at"] == EXPIRES
        ProposalResult.model_validate_json(json.dumps(result))
    assert after_t[linked][2]["proposal"]["card"]["proposal_id"] == str(requested)
    assert after_t[orphan][2]["proposal"]["card"]["proposal_id"] == str(orphan_proposal)
    text, body, result, _ = after_t[answer]
    assert_no_content(text.replace(TYPED, ""))
    assert body == TYPED
    assert result == {
        "action": "answer",
        "markdown": f"Here is the open draft.\n\n- Email draft {draft}, status draft.",
    }
    for turn in kept_turns:
        assert after_t[turn] == before_t[turn]

    assert old_rows(owner) == 0

    # console_audit never held email content: nothing there changes.
    assert w.audits() == before_a

    # A second application rewrites nothing: every row keeps its value and its xmin.
    apply_migration(owner)
    assert w.proposals() == after_p
    assert w.turns() == after_t
    assert w.audits() == before_a


def test_scrub_recovers_draft_id_from_an_executed_turn_copy(owner: psycopg.Connection[Any]) -> None:
    w = World(owner)
    draft = uuid.uuid4()
    gone = uuid.uuid4()
    stored = embedded(gone, old_card())
    stored["proposal"] |= {"status": "executed", "result": old_result(draft)}
    turn = w.turn("email_compose", stored)
    apply_migration(owner)
    text, _, result, _ = w.turns()[turn]
    assert_no_content(text.replace(TYPED, ""))
    assert result["proposal"]["card"]["fields"] == [
        SUBJECT_FIELD,
        {"label": "Draft", "value": str(draft)},
    ]
    assert result["proposal"]["result"] == {
        "draft_id": str(draft),
        "status": "exported",
        "gmail_draft_id": "gmail-draft-1",
    }
    ProposalResult.model_validate_json(json.dumps(result))
