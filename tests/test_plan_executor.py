"""The plan executor: several existing proposals behind one card and one confirm.

Most cases drive ``PlanExecutor`` directly with fake child executors; the two race cases run
it inside a real ``ProposalRegistry`` on Docker Postgres.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import typing
from typing import Any
from uuid import UUID, uuid4

import pytest
from csa.console.contracts import ConsoleActor, Role
from csa.workspace import contracts
from csa.workspace import plan as plan_module
from csa.workspace.contracts import CardField, Language, PlanStep, ProposalKind
from csa.workspace.plan import (
    MAX_NON_EMAIL_STEPS,
    MAX_STEPS,
    STEP_KINDS,
    PlanExecutor,
    plan_payload,
    step_id,
)
from csa.workspace.registry import (
    CardContent,
    ProposalActionRefused,
    ProposalError,
    ProposalOrigin,
    ProposalRegistry,
)
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld
from tests.test_workspace_registry import _age_claim, _member_actor, _row

pytest_plugins = ["tests.test_console_sessions"]

CLIENT = uuid4()
# Synthetic personal data the fake children put on their own cards: a plan card never shows it.
NAME, ADDRESS, SUBJECT = "Ada Synthetic", "ada@example.test", "Quarterly figures"


# The refusal each real executor's execute raises, after its key replay, when the record moved
# since the card: every step kind checks its version in the owning service (send_draft and
# export_draft against the draft version, decide_approval against the conversation's
# inbox_version, the booking, HR and training services against their own versions).
DRIFT_REFUSAL = {
    "email_send": "draft_changed",
    "email_export": "draft_changed",
    "booking_action": "proposal_stale",
    "inbox_decision": "proposal_stale",
    "training_submit": "stale_version",
    "hr_person": "proposal_stale",
}


class FakeChild:
    """A child executor that behaves like the owning services.

    Its card carries a name and an address. ``execute`` replays a known proposal id first, then
    refuses a record that moved since the card, then acts: the act is counted in ``acts`` (one
    entry per real effect, replays excluded) and moves the record, so its card then refuses (a
    draft that was sent, a reply that was decided) as the real cards do.
    """

    kind: ProposalKind

    def __init__(self, kind: str) -> None:
        self.kind = typing.cast(ProposalKind, kind)
        self.current: dict[int, str | None] = {}  # step n -> version the record has now
        self.card_error: dict[int, Exception] = {}
        self.gate_error: Exception | None = None
        self.execute_error: dict[int, Exception] = {}
        self.slow: dict[int, float] = {}
        self.park: asyncio.Event | None = None  # the first execute waits on it
        self.parked = asyncio.Event()
        self.calls: list[tuple[int, UUID, str | None, str]] = []
        self.card_calls = 0
        self.receipts: dict[UUID, dict[str, Any]] = {}  # the service's replay store, by key
        self.acts: list[UUID] = []  # one entry per real effect
        self.cancelled: list[int] = []

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        if self.gate_error is not None:
            raise self.gate_error

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
        language: Language = "en",
    ) -> tuple[CardContent, str | None]:
        self.card_calls += 1
        await self.gate(request, actor, client_id, origin=origin)
        n = int(payload["n"])
        if n in self.card_error:
            raise self.card_error[n]
        content = CardContent(
            title=f"Email {NAME} <{ADDRESS}>",
            summary=f"About {SUBJECT}",
            fields=[CardField(label="To", value=ADDRESS)],
            language=language,
        )
        return content, self.current.get(n, f"v{n}")

    async def execute(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        proposal_id: UUID,
        expected_version: str | None,
        origin: ProposalOrigin,
    ) -> dict[str, Any]:
        n = int(payload["n"])
        self.calls.append((n, proposal_id, expected_version, origin))
        if self.park is not None:
            park, self.park = self.park, None
            self.parked.set()
            await park.wait()
        if n in self.slow:
            try:
                await asyncio.sleep(self.slow[n])
            except asyncio.CancelledError:
                self.cancelled.append(n)
                raise
        if proposal_id in self.receipts:  # replay by key, before any version check
            return self.receipts[proposal_id]
        if n in self.execute_error:
            raise self.execute_error[n]
        if self.current.get(n, f"v{n}") != expected_version:
            raise ProposalActionRefused(409, {"detail": DRIFT_REFUSAL[self.kind]})
        self.acts.append(proposal_id)
        receipt = {"n": n, "effect": len(self.acts)}
        if self.kind == "inbox_decision":
            receipt = {"outcome": "applied", "status": "approved", "queued": True}
        self.receipts[proposal_id] = receipt
        # The record moved: its card now refuses, as a sent draft's or a decided reply's does.
        self.current[n] = f"v{n}-done"
        self.card_error[n] = ProposalActionRefused(409, {"detail": "draft_already_sent"})
        return receipt


def _children() -> dict[str, FakeChild]:
    return {kind: FakeChild(kind) for kind in sorted(STEP_KINDS)}


def _step(kind: str, n: int, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "payload": {"n": n, **extra}, "version": f"v{n}"}


def _payload(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"steps": list(steps)}


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


def _actor() -> ConsoleActor:
    return ConsoleActor(
        user_id=uuid4(),
        email="staff@example.test",
        display_name="Synthetic Staff",
        platform_admin=False,
        must_change_password=False,
        session_id=uuid4(),
        client_id=CLIENT,
        role=Role.TENANT_STAFF,
        memberships=[],
        csrf_hash="synthetic",
    )


async def _card(
    executor: PlanExecutor, payload: dict[str, Any], *, origin: ProposalOrigin = "assistant"
) -> tuple[CardContent, str | None]:
    return await executor.card(_request(), _actor(), CLIENT, payload, origin=origin)


async def _execute(
    executor: PlanExecutor,
    payload: dict[str, Any],
    plan_id: UUID,
    *,
    origin: ProposalOrigin = "assistant",
) -> dict[str, Any]:
    _, version = await _card(executor, payload)
    return await executor.execute(
        _request(),
        _actor(),
        CLIENT,
        payload,
        proposal_id=plan_id,
        expected_version=version,
        origin=origin,
    )


def _hash(versions: list[str | None]) -> str:
    return hashlib.sha256(json.dumps(versions, separators=(",", ":")).encode()).hexdigest()


async def test_card_lists_one_row_per_step_with_no_addresses() -> None:
    children = _children()
    draft = str(uuid4())
    payload = _payload(
        _step("email_send", 0, draft_id=draft),
        _step("booking_action", 1),
        _step("hr_person", 2),
        _step("email_export", 3, draft_id=draft),
    )
    content, _ = await _card(PlanExecutor(children), payload)
    assert len(content.fields) == 4
    # An email row carries its draft id so the console opens it through the existing preview.
    assert [f.value for f in content.fields] == [draft, "", "", draft]
    assert "4" in content.title
    shown = json.dumps([content.title, content.summary, [f.model_dump() for f in content.fields]])
    for needle in (NAME, ADDRESS, SUBJECT, "Ada", "example.test"):
        assert needle not in shown
    malay, _ = await PlanExecutor(children).card(
        _request(), _actor(), CLIENT, payload, origin="assistant", language="ms"
    )
    assert malay.language == "ms" and len(malay.fields) == 4
    assert malay.title != content.title


async def test_plan_version_is_hash_of_stored_step_versions() -> None:
    children = _children()
    steps = [_step("booking_action", 0), _step("training_submit", 1), _step("email_export", 2)]
    steps[1]["version"] = children["training_submit"].current[1] = None  # a new request
    payload = _payload(*steps)
    content, version = await _card(PlanExecutor(children), payload)
    assert version == _hash(["v0", None, "v2"])
    assert not any("changed" in f.label for f in content.fields)
    # A step that moved, or whose card now refuses, shows as changed; the plan version is still
    # the stored one, so the registry's own version check at confirm passes.
    children["booking_action"].current[0] = "v-moved"
    children["email_export"].card_error[2] = ProposalActionRefused(409, {"detail": "draft_changed"})
    changed, again = await _card(PlanExecutor(children), payload)
    assert again == version
    assert ["changed" in f.label for f in changed.fields] == [True, False, True]
    assert "2" in changed.summary


async def test_tab_origin_is_refused() -> None:
    executor = PlanExecutor(_children())
    payload = _payload(_step("booking_action", 0))
    for call in (
        _card(executor, payload, origin="tab"),
        executor.gate(_request(), _actor(), CLIENT, origin="tab"),
        executor.execute(
            _request(),
            _actor(),
            CLIENT,
            payload,
            proposal_id=uuid4(),
            expected_version=None,
            origin="tab",
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403 and exc.value.detail == "plan_assistant_only"
    await executor.gate(_request(), _actor(), CLIENT, origin="assistant")


async def test_kinds_outside_the_allowlist_are_refused() -> None:
    assert STEP_KINDS == frozenset(typing.get_args(PlanStep.model_fields["kind"].annotation))
    assert STEP_KINDS == {
        "email_send",
        "email_export",
        "booking_action",
        "inbox_decision",
        "training_submit",
        "hr_person",
    }
    children = _children()
    executor = PlanExecutor(children)
    for kind in ("plan", "subscription_action", "email_batch", "nonsense"):
        with pytest.raises(HTTPException) as exc:
            await _card(executor, _payload(_step("booking_action", 0), _step(kind, 1)))
        assert exc.value.status_code == 422 and exc.value.detail == "plan_step_not_allowed"
    with pytest.raises(HTTPException) as bad:
        await _card(executor, {"steps": "nope"})
    assert bad.value.status_code == 422
    assert all(not child.calls for child in children.values())
    # Every allowed kind needs its executor; other kinds the registry holds are left out.
    with pytest.raises(ValueError, match="hr_person"):
        PlanExecutor({k: v for k, v in children.items() if k != "hr_person"})
    PlanExecutor({**children, "subscription_action": FakeChild("subscription_action")})


async def test_caps_twenty_steps_and_ten_non_email() -> None:
    assert MAX_STEPS == contracts.MAX_STEPS == 20 and MAX_NON_EMAIL_STEPS == 10
    executor = PlanExecutor(_children())
    emails = [_step("email_export", n) for n in range(MAX_STEPS)]
    await _card(executor, _payload(*emails))
    with pytest.raises(HTTPException) as many:
        await _card(executor, _payload(*emails, _step("email_send", 20)))
    assert many.value.status_code == 422 and many.value.detail == "plan_too_many_steps"
    bookings = [_step("booking_action", n) for n in range(MAX_NON_EMAIL_STEPS)]
    await _card(executor, _payload(*bookings, *emails[:10]))
    with pytest.raises(HTTPException) as actions:
        await _card(executor, _payload(*bookings, _step("inbox_decision", 10)))
    assert actions.value.status_code == 422 and actions.value.detail == "plan_too_many_steps"
    with pytest.raises(ValidationError):
        plan_payload([PlanStep.model_validate(_step("email_export", n)) for n in range(21)])
    assert plan_payload([PlanStep.model_validate(_step("email_export", 0))]) == {
        "steps": [{"kind": "email_export", "payload": {"n": 0}, "version": "v0"}]
    }


async def test_execute_runs_steps_in_order_with_derived_ids() -> None:
    children = _children()
    kinds = ["email_send", "booking_action", "email_send", "training_submit", "email_export"]
    payload = _payload(*[_step(kind, n) for n, kind in enumerate(kinds)])
    plan_id = uuid4()
    receipt = await _execute(PlanExecutor(children), payload, plan_id)
    assert step_id(plan_id, 3) == plan_module.uuid5(plan_id, "step:3")
    assert receipt["steps"] == [
        {
            "index": n,
            "kind": kind,
            "status": "done",
            "receipt": children[kind].receipts[step_id(plan_id, n)],
        }
        for n, kind in enumerate(kinds)
    ]
    calls = sorted(call for child in children.values() for call in child.calls)
    assert calls == [(n, step_id(plan_id, n), f"v{n}", "assistant") for n in range(len(kinds))]


async def test_execute_does_not_run_the_child_cards() -> None:
    """The child's execute checks drift itself (it replays by key first), so a step the plan
    already finished is never judged by a card that now refuses it."""
    children = _children()
    payload = _payload(_step("email_send", 0), _step("inbox_decision", 1))
    executor = PlanExecutor(children)
    _, version = await _card(executor, payload)
    assert children["email_send"].card_calls == 1
    await executor.execute(
        _request(),
        _actor(),
        CLIENT,
        payload,
        proposal_id=uuid4(),
        expected_version=version,
        origin="assistant",
    )
    assert children["email_send"].card_calls == 1 and children["inbox_decision"].card_calls == 1


async def test_a_failing_step_is_recorded_and_the_rest_run() -> None:
    children = _children()
    children["booking_action"].execute_error[1] = HTTPException(409, "booking_slot_taken")
    children["training_submit"].execute_error[2] = RuntimeError("synthetic crash")
    payload = _payload(
        _step("email_send", 0),
        _step("booking_action", 1),
        _step("training_submit", 2),
        _step("email_send", 3),
    )
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    steps = receipt["steps"]
    assert [s["status"] for s in steps] == ["done", "failed", "failed", "done"]
    assert steps[1]["code"] == "booking_slot_taken" and "receipt" not in steps[1]
    assert steps[2]["code"] == "step_failed"
    assert len(children["email_send"].acts) == 2


async def test_every_step_failing_with_the_same_refusal_raises_it() -> None:
    children = _children()
    for child in children.values():
        child.gate_error = HTTPException(403, "package_inactive")
    executor = PlanExecutor(children)
    payload = _payload(_step("email_send", 0), _step("booking_action", 1))
    with pytest.raises(HTTPException) as exc:
        await executor.execute(
            _request(),
            _actor(),
            CLIENT,
            payload,
            proposal_id=uuid4(),
            expected_version=_hash(["v0", "v1"]),
            origin="assistant",
        )
    assert exc.value.status_code == 403 and exc.value.detail == "package_inactive"
    assert all(not child.calls for child in children.values())
    # Different refusals are a receipt, not a raise.
    children = _children()
    refusal = ProposalActionRefused(429, {"detail": "send_limit_reached", "retry_after": 60})
    children["email_send"].execute_error[0] = refusal
    children["booking_action"].execute_error[1] = HTTPException(409, "booking_slot_taken")
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    assert [(s["status"], s["code"]) for s in receipt["steps"]] == [
        ("failed", "send_limit_reached"),
        ("failed", "booking_slot_taken"),
    ]
    # The same refusal from every step's execute raises it, body and all.
    children = _children()
    children["email_send"].execute_error = {0: refusal, 1: refusal}
    with pytest.raises(ProposalActionRefused) as same:
        await _execute(
            PlanExecutor(children),
            _payload(_step("email_send", 0), _step("email_send", 1)),
            uuid4(),
        )
    assert same.value.content == {"detail": "send_limit_reached", "retry_after": 60}


async def test_a_drifted_step_is_skipped_not_failed() -> None:
    children = _children()
    payload = _payload(
        _step("email_send", 0),
        _step("inbox_decision", 1),
        _step("email_send", 2),
        _step("booking_action", 3),
    )
    executor = PlanExecutor(children)
    _, version = await _card(executor, payload)
    children["inbox_decision"].current[1] = "v-customer-wrote-again"  # proposal_stale
    children["email_send"].current[2] = "v-edited"  # draft_changed
    children["booking_action"].current[3] = "v-moved"  # proposal_stale
    receipt = await executor.execute(
        _request(),
        _actor(),
        CLIENT,
        payload,
        proposal_id=uuid4(),
        expected_version=version,
        origin="assistant",
    )
    skipped = {"index": 1, "kind": "inbox_decision", "status": "skipped", "code": "changed"}
    assert receipt["steps"][1] == skipped
    assert [(s["status"], s.get("code")) for s in receipt["steps"]] == [
        ("done", None),
        ("skipped", "changed"),
        ("skipped", "changed"),
        ("skipped", "changed"),
    ]
    assert children["inbox_decision"].acts == [] and len(children["email_send"].acts) == 1
    assert children["booking_action"].acts == []


# The child refusals that mean "changed since you reviewed it"; every other one is a failure.
DRIFT = (
    "draft_changed",
    "draft_already_sent",
    "draft_already_exported",
    "proposal_stale",
    "slot_taken",
    "stale_version",
)


def test_drift_codes_are_one_named_set() -> None:
    assert frozenset(DRIFT) == plan_module.DRIFT_CODES


@pytest.mark.parametrize("code", DRIFT)
async def test_each_drift_refusal_is_changed(code: str) -> None:
    children = _children()
    children["email_send"].execute_error[0] = ProposalActionRefused(409, {"detail": code})
    payload = _payload(_step("email_send", 0), _step("booking_action", 1))
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    assert [(s["status"], s.get("code")) for s in receipt["steps"]] == [
        ("skipped", "changed"),
        ("done", None),
    ]


async def test_draft_already_sending_is_failed_not_changed() -> None:
    """Under the step's own key it means this plan's earlier attempt is still sending, or died
    mid-send (the mail may have gone out); under another key, another send holds the draft.
    Neither is "changed since you reviewed it"."""
    children = _children()
    sending = ProposalActionRefused(409, {"detail": "draft_already_sending"})
    children["email_send"].execute_error[0] = sending
    payload = _payload(_step("email_send", 0), _step("booking_action", 1))
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    assert [(s["status"], s.get("code")) for s in receipt["steps"]] == [
        ("failed", "draft_already_sending"),
        ("done", None),
    ]
    # Every step answering it raises that refusal (one identical refusal), not proposal_stale.
    children = _children()
    children["email_send"].execute_error = {0: sending, 1: sending}
    with pytest.raises(ProposalActionRefused) as same:
        await _execute(
            PlanExecutor(children),
            _payload(_step("email_send", 0), _step("email_send", 1)),
            uuid4(),
        )
    assert same.value.detail == "draft_already_sending"


async def test_a_real_refusal_is_failed_with_its_code_not_changed() -> None:
    children = _children()
    children["email_send"].execute_error[0] = ProposalActionRefused(
        409, {"detail": "mailbox_not_connected"}
    )
    children["email_export"].execute_error[1] = HTTPException(503, "email_assistant_not_configured")
    children["booking_action"].execute_error[2] = HTTPException(403, "package_inactive")
    payload = _payload(_step("email_send", 0), _step("email_export", 1), _step("booking_action", 2))
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    assert [(s["status"], s.get("code")) for s in receipt["steps"]] == [
        ("failed", "mailbox_not_connected"),
        ("failed", "email_assistant_not_configured"),
        ("failed", "package_inactive"),
    ]
    # A disconnected mailbox refuses every email step the same way: the plan raises it, so the
    # registry releases the claim instead of recording every step skipped.
    children = _children()
    refusal = ProposalActionRefused(409, {"detail": "mailbox_reauthorization_required"})
    children["email_send"].execute_error = {0: refusal, 1: refusal}
    with pytest.raises(ProposalActionRefused) as same:
        await _execute(
            PlanExecutor(children),
            _payload(_step("email_send", 0), _step("email_send", 1)),
            uuid4(),
        )
    assert same.value.detail == "mailbox_reauthorization_required"


async def test_an_inbox_reply_that_expired_or_vanished_is_failed() -> None:
    children = _children()
    plan_id = uuid4()
    payload = _payload(_step("inbox_decision", 0), _step("inbox_decision", 1))
    executor = PlanExecutor(children)
    _, version = await _card(executor, payload)
    for n, outcome in ((0, "expired"), (1, "not_found")):
        children["inbox_decision"].receipts[step_id(plan_id, n)] = {"outcome": outcome}
    # A colleague decided the held reply with no version move: decide_approval answers it.
    payload["steps"].append(_step("inbox_decision", 2))
    _, version = await _card(executor, payload)
    children["inbox_decision"].receipts[step_id(plan_id, 2)] = {"outcome": "already_handled"}
    receipt = await executor.execute(
        _request(),
        _actor(),
        CLIENT,
        payload,
        proposal_id=plan_id,
        expected_version=version,
        origin="assistant",
    )
    assert [(s["status"], s.get("code")) for s in receipt["steps"]] == [
        ("failed", "reply_approval_expired"),
        ("failed", "reply_approval_not_found"),
        ("skipped", "changed"),
    ]


async def test_a_plan_where_every_step_drifted_is_stale() -> None:
    children = _children()
    payload = _payload(_step("email_send", 0), _step("booking_action", 1))
    executor = PlanExecutor(children)
    _, version = await _card(executor, payload)
    children["email_send"].current[0] = "v-edited"
    children["booking_action"].current[1] = "v-moved"
    with pytest.raises(ProposalError) as stale:
        await executor.execute(
            _request(),
            _actor(),
            CLIENT,
            payload,
            proposal_id=uuid4(),
            expected_version=version,
            origin="assistant",
        )
    assert stale.value.detail == "proposal_stale"
    # One step left to act is not a stale plan.
    children = _children()
    children["email_send"].current[0] = "v-edited"
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    assert [s["status"] for s in receipt["steps"]] == ["skipped", "done"]


async def test_steps_past_the_deadline_are_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deadline stops new steps; a step already running (a Gmail send) finishes."""
    assert plan_module.PLAN_DEADLINE_SECONDS == 90
    assert plan_module.PLAN_DEADLINE_SECONDS < plan_module.PLAN_BACKSTOP_SECONDS < 120
    monkeypatch.setattr(plan_module, "PLAN_DEADLINE_SECONDS", 0.2)
    children = _children()
    for n in range(4):  # fill every slot past the deadline
        children["booking_action"].slow[n] = 0.6
    payload = _payload(
        *[_step("booking_action", n) for n in range(4)],
        _step("email_send", 4),
        _step("email_send", 5),
    )
    started = time.monotonic()
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    assert time.monotonic() - started < 5
    statuses = [(s["status"], s.get("code")) for s in receipt["steps"]]
    # Started steps ran to the end; the rest were never started.
    assert statuses == [("done", None)] * 4 + [("not_run", None)] * 2
    assert children["booking_action"].cancelled == []
    assert len(children["booking_action"].acts) == 4
    assert children["email_send"].calls == [] and children["email_send"].acts == []


async def test_the_backstop_cancels_a_step_still_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_module, "PLAN_DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(plan_module, "PLAN_BACKSTOP_SECONDS", 0.4)
    children = _children()
    children["booking_action"].slow = {0: 30, 1: 0.25, 2: 0.25, 3: 0.25}
    payload = _payload(
        *[_step("booking_action", n) for n in range(4)],
        _step("email_send", 4),
        _step("email_send", 5),
    )
    started = time.monotonic()
    receipt = await _execute(PlanExecutor(children), payload, uuid4())
    assert time.monotonic() - started < 5
    statuses = [(s["status"], s.get("code")) for s in receipt["steps"]]
    # Step 0 outlived the backstop and was cancelled as a last resort (it may have acted), steps
    # 1 to 3 finished past the deadline, and the steps that got a slot only after the deadline
    # were never started.
    assert (
        statuses == [("failed", "plan_deadline")] + [("done", None)] * 3 + [("not_run", None)] * 2
    )
    assert children["booking_action"].cancelled == [0]
    assert children["email_send"].calls == []


async def test_a_cancelled_plan_is_never_swallowed() -> None:
    children = _children()
    children["booking_action"].slow[0] = 30
    executor = PlanExecutor(children)
    payload = _payload(_step("booking_action", 0), _step("email_send", 1))
    task = asyncio.create_task(_execute(executor, payload, uuid4()))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children["booking_action"].cancelled == [0]


async def test_replaying_execute_replays_finished_steps() -> None:
    children = _children()
    children["booking_action"].execute_error[1] = HTTPException(409, "booking_slot_taken")
    payload = _payload(_step("email_send", 0), _step("booking_action", 1), _step("email_send", 2))
    executor, plan_id = PlanExecutor(children), uuid4()
    first = await _execute(executor, payload, plan_id)
    again = await _execute(executor, payload, plan_id)
    assert again == first
    assert [s["status"] for s in again["steps"]] == ["done", "failed", "done"]
    assert len(children["email_send"].calls) == 4  # each step ran twice under its own id...
    # ...and acted once: the sent drafts' cards refuse now, the replays still read done.
    assert children["email_send"].acts == [step_id(plan_id, 0), step_id(plan_id, 2)]


async def test_a_run_cut_short_is_finished_by_the_next() -> None:
    """A confirm that dies mid-plan: the next run replays what it finished, as done, and runs
    the rest. Nothing is reported skipped because its own earlier run moved the record."""
    children = _children()
    children["booking_action"].slow[1] = 30
    payload = _payload(_step("email_send", 0), _step("booking_action", 1), _step("email_send", 2))
    executor, plan_id = PlanExecutor(children), uuid4()
    dead = asyncio.create_task(_execute(executor, payload, plan_id))
    while len(children["email_send"].acts) < 2:
        await asyncio.sleep(0.01)
    dead.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dead
    children["booking_action"].slow.clear()
    receipt = await _execute(executor, payload, plan_id)
    assert [s["status"] for s in receipt["steps"]] == ["done", "done", "done"]
    assert receipt["steps"][0]["receipt"] == children["email_send"].receipts[step_id(plan_id, 0)]
    assert children["email_send"].acts == [step_id(plan_id, 0), step_id(plan_id, 2)]
    assert children["booking_action"].acts == [step_id(plan_id, 1)]


# --------------------------------------------------------------------- inside the registry


def _registry(children: dict[str, FakeChild]) -> ProposalRegistry:
    return ProposalRegistry({"plan": PlanExecutor(children)})


async def _create_plan(
    sessions: SessionWorld, registry: ProposalRegistry, actor: ConsoleActor, payload: dict
) -> Any:
    return await registry.create(
        sessions.pool,
        _request(),
        actor,
        UUID(sessions.tenants[0]),
        "plan",
        payload,
        thread_id=None,
        origin="assistant",
    )


async def _confirm(sessions: SessionWorld, registry: ProposalRegistry, actor, pid) -> Any:
    return await registry.confirm(sessions.pool, _request(), actor, UUID(sessions.tenants[0]), pid)


def _acts(children: dict[str, FakeChild]) -> list[UUID]:
    """Every real effect, one entry each: a step that acted twice shows up twice."""
    return sorted((pid for child in children.values() for pid in child.acts), key=str)


@requires_docker
async def test_concurrent_confirms_run_each_step_once(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    children = _children()
    registry = _registry(children)
    payload = _payload(_step("email_send", 0), _step("booking_action", 1), _step("email_send", 2))
    out = await _create_plan(sessions, registry, actor, payload)
    assert out.status == "requested" and out.card.expected_version == _hash(["v0", "v1", "v2"])
    release = asyncio.Event()
    children["email_send"].park = release
    first = asyncio.create_task(_confirm(sessions, registry, actor, out.id))
    await asyncio.wait_for(children["email_send"].parked.wait(), 10)
    with pytest.raises(ProposalError) as loser:
        await _confirm(sessions, registry, actor, out.id)  # a second tab, while steps run
    assert loser.value.detail == "proposal_consumed"
    release.set()
    done = await asyncio.wait_for(first, 10)
    assert done.status == "executed"
    assert [s["status"] for s in done.result["steps"]] == ["done", "done", "done"]
    assert len(children["email_send"].calls) == 2 and len(children["booking_action"].calls) == 1
    assert (await _confirm(sessions, registry, actor, out.id)) == done
    assert len(children["email_send"].calls) == 2 and len(children["booking_action"].calls) == 1
    assert _acts(children) == sorted((step_id(out.id, n) for n in range(3)), key=str)


@requires_docker
async def test_a_lease_retaken_mid_plan_acts_once_per_step(sessions: SessionWorld) -> None:
    """The first confirm stalls inside a step until its lease goes stale and a second confirm
    retakes it. The steps the first run finished replay as done (their records moved, so their
    cards now refuse), the stalled one runs, and each step acts once."""
    actor, _ = await _member_actor(sessions)
    children = _children()
    registry = _registry(children)
    payload = _payload(_step("email_send", 0), _step("booking_action", 1), _step("email_send", 2))
    out = await _create_plan(sessions, registry, actor, payload)
    release = asyncio.Event()
    children["email_send"].park = release  # step 0 stalls before it acts
    first = asyncio.create_task(_confirm(sessions, registry, actor, out.id))
    await asyncio.wait_for(children["email_send"].parked.wait(), 10)
    while len(_acts(children)) < 2:  # steps 1 and 2 finish in the first run
        await asyncio.sleep(0.01)
    await _age_claim(sessions, out.id)
    retaken = await _confirm(sessions, registry, actor, out.id)
    assert retaken.status == "executed"
    assert [s["status"] for s in retaken.result["steps"]] == ["done", "done", "done"]
    release.set()
    stalled = await asyncio.wait_for(first, 10)
    assert stalled == retaken  # the stalled confirm finds the retaker's record and replays it
    assert (await _row(sessions, out.id))["status"] == "executed"
    assert _acts(children) == sorted((step_id(out.id, n) for n in range(3)), key=str)
