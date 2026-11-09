"""Plan proposals: several existing proposals behind one card and one confirm.

A plan's payload is ``{"steps": [{"kind", "payload", "version"}]}``: each step is a proposal of
an allowed kind (``STEP_KINDS``) with the version its own executor's ``card`` reported when the
plan was prepared. ``PlanExecutor`` composes the registry's executors by kind and never goes
through the registry itself: the registry claims and records the plan, and each step keeps its
own gate, card, version and idempotency key.

Only the assistant makes a plan: origin ``tab`` is refused here, and the direct create route
rejects the kind. The card holds step titles and counts only (plus each email step's draft id,
which the console opens through the draft's own preview); never an address, name, subject or
body, because ``console_proposals`` is readable across the tenant.

Confirm is per step. ``execute`` runs each step's gate (a refusal fails the step) and then the
child's own ``execute`` under ``step_id(plan_id, i)``, never its ``card``: the owning services
replay a known key before any version check, so a step an earlier, interrupted run finished
replays as done, while a step whose record moved is refused by the service itself. Only the
refusals in ``DRIFT_CODES`` mean "changed since you reviewed it" (``skipped``); every other
refusal is ``failed`` with its code. Steps run ``CONCURRENCY`` at a time; none starts after
``PLAN_DEADLINE_SECONDS``, and ``PLAN_BACKSTOP_SECONDS`` (inside the registry's two-minute
claim lease) cuts off a step still running only as a last resort.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, get_args
from uuid import UUID, uuid5

import structlog
from fastapi import HTTPException, Request
from pydantic import ValidationError

from csa.console.contracts import ConsoleActor
from csa.workspace.contracts import (
    MAX_STEPS,
    CardField,
    Language,
    PlanProposalPayload,
    PlanStep,
    ProposalKind,
)
from csa.workspace.registry import CardContent, ProposalError, ProposalExecutor, ProposalOrigin

logger = structlog.get_logger(__name__)

STEP_KINDS: frozenset[str] = frozenset(get_args(PlanStep.model_fields["kind"].annotation))
"""The kinds a step may have: the ``PlanStep.kind`` literal, so validation and the executor
can never disagree. Never ``plan`` and never ``subscription_action``."""
EMAIL_KINDS = frozenset({"email_send", "email_export"})
MAX_NON_EMAIL_STEPS = 10
PLAN_DEADLINE_SECONDS: float = 90
"""The cut-off for starting steps: one not started by then is ``not_run``. A step already
running is left to finish (a Gmail send has its own ``SEND_DEADLINE_SECONDS``, 25)."""
PLAN_BACKSTOP_SECONDS: float = 115
"""Last resort, below the two-minute claim lease: a step still running then is cancelled and
recorded ``failed``/``plan_deadline`` (it may have acted). 90 + 25 fits under it."""
CONCURRENCY = 4

DRIFT_CODES: frozenset[str] = frozenset(
    {
        # email_send: the draft was edited after the card (send_draft's version check).
        "draft_changed",
        # email_send, email_export: the draft was sent under another key.
        "draft_already_sent",
        # email_send, email_export: another send of the draft holds its claim.
        "draft_already_sending",
        # email_send, email_export: the draft was saved to Gmail drafts meanwhile.
        "draft_already_exported",
        # booking_action: the booking changed, the rules now need acknowledging, or other
        # people were picked; hr_person: the person changed (person_changed).
        "proposal_stale",
        # booking_action: someone took the time since the card.
        "slot_taken",
        # training_submit: the request moved past the version the card read.
        "stale_version",
    }
)
"""The child refusals at execute that mean the record moved since the card: the step is
``skipped`` as ``changed``. Any other refusal (a disconnected mailbox, a package switched
off, the send limit, ``send_outcome_unknown``) is ``failed`` with its code."""

_INBOX_NOT_APPLIED: dict[str, str | None] = {
    "already_handled": None,  # a colleague decided the held reply: changed
    "expired": "reply_approval_expired",
    "not_found": "reply_approval_not_found",
}
"""``decide_approval`` answers with an outcome, not a refusal, when it did not act."""

StepStatus = Literal["done", "failed", "skipped", "not_run"]

# Card text in the turn's language. Step titles are fixed per kind: a child's own title may
# name a person, so it is never copied onto the plan card.
_PLAN_TEXT: dict[Language, dict[str, str]] = {
    "en": {
        "title_one": "Confirm 1 step",
        "title_many": "Confirm {count} steps",
        "summary": "Each step is checked again when you select Confirm. A step that changed "
        "since you reviewed it is skipped, and the rest still run.",
        "changed_one": " 1 step has already changed and will be skipped.",
        "changed_many": " {count} steps have already changed and will be skipped.",
        "changed": "{title} (changed since you reviewed it)",
        "step_email_send": "Send an email",
        "step_email_export": "Save an email to Gmail drafts",
        "step_booking_action": "Book, move or cancel a booking",
        "step_inbox_decision": "Decide a held reply",
        "step_training_submit": "Submit a training request",
        "step_hr_person": "Change a person in People",
    },
    "ms": {
        "title_one": "Sahkan 1 langkah",
        "title_many": "Sahkan {count} langkah",
        "summary": "Setiap langkah disemak semula apabila anda memilih Sahkan. Langkah yang "
        "berubah sejak anda menyemaknya dilangkau, dan yang lain tetap dijalankan.",
        "changed_one": " 1 langkah sudah berubah dan akan dilangkau.",
        "changed_many": " {count} langkah sudah berubah dan akan dilangkau.",
        "changed": "{title} (berubah sejak anda menyemaknya)",
        "step_email_send": "Hantar e-mel",
        "step_email_export": "Simpan e-mel ke draf Gmail",
        "step_booking_action": "Tempah, alih atau batalkan tempahan",
        "step_inbox_decision": "Putuskan balasan yang ditahan",
        "step_training_submit": "Hantar permohonan latihan",
        "step_hr_person": "Ubah maklumat orang dalam People",
    },
}


def step_id(plan_id: UUID, index: int) -> UUID:
    """The proposal id step ``index`` runs under; the child's idempotency keys derive from it."""
    return uuid5(plan_id, f"step:{index}")


def plan_payload(steps: list[PlanStep]) -> dict[str, Any]:
    """The stored payload of a plan card, validated (1 to ``MAX_STEPS`` steps)."""
    return PlanProposalPayload(steps=steps).model_dump(mode="json")


def plan_version(steps: Sequence[PlanStep]) -> str:
    """The plan's bound version: the hash of the stored step versions, in order.

    It never changes after the card is made, so the registry's own check at confirm passes;
    each step's drift is judged on its own in ``execute``.
    """
    versions = json.dumps([step.version for step in steps], separators=(",", ":"))
    return hashlib.sha256(versions.encode("ascii")).hexdigest()


def _detail(exc: HTTPException) -> str:
    return exc.detail if isinstance(exc.detail, str) else str(exc.detail)


def _distinct(steps: Iterable[PlanStep]) -> list[str]:
    return list(dict.fromkeys(step.kind for step in steps))


@dataclass(frozen=True)
class _Outcome:
    status: StepStatus
    code: str | None = None
    receipt: dict[str, Any] | None = None
    refusal: HTTPException | None = field(default=None, compare=False)

    def entry(self, index: int, kind: str) -> dict[str, Any]:
        entry: dict[str, Any] = {"index": index, "kind": kind, "status": self.status}
        if self.code is not None:
            entry["code"] = self.code
        if self.receipt is not None:
            entry["receipt"] = self.receipt
        return entry


_CHANGED = _Outcome("skipped", "changed")
_NOT_RUN = _Outcome("not_run")
_CUT_OFF = _Outcome("failed", "plan_deadline")


def _failed(exc: HTTPException) -> _Outcome:
    return _Outcome("failed", _detail(exc), refusal=exc)


def _refused(exc: HTTPException) -> _Outcome:
    return _CHANGED if _detail(exc) in DRIFT_CODES else _failed(exc)


def _finished(kind: str, receipt: dict[str, Any]) -> _Outcome:
    if kind == "inbox_decision" and receipt.get("outcome") in _INBOX_NOT_APPLIED:
        code = _INBOX_NOT_APPLIED[receipt["outcome"]]
        return _CHANGED if code is None else _Outcome("failed", code)
    return _Outcome("done", receipt=receipt)


async def _bounded[T](calls: Sequence[Callable[[], Awaitable[T]]]) -> list[T]:
    """Run ``calls`` ``CONCURRENCY`` at a time; the first error cancels the rest and is raised."""
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(call: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await call()

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(call)) for call in calls]
    except ExceptionGroup as errors:
        raise errors.exceptions[0] from None
    return [task.result() for task in tasks]


class PlanExecutor:
    """Composes the registry's executors by kind; see the module docstring."""

    kind: ProposalKind = "plan"

    def __init__(self, children: Mapping[str, ProposalExecutor]) -> None:
        missing = STEP_KINDS - set(children)
        if missing:
            raise ValueError(f"a plan needs an executor for {sorted(missing)}")
        for kind in STEP_KINDS:
            if children[kind].kind != kind:
                raise ValueError(f"executor for {kind} reports kind {children[kind].kind}")
        # Kinds the registry holds that are not steps (subscription_action) are left out.
        self._children: dict[str, ProposalExecutor] = {k: children[k] for k in STEP_KINDS}

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        """Only the assistant runs a plan; each step's own gate runs with the step."""
        if origin != "assistant":
            raise HTTPException(403, "plan_assistant_only")

    async def _steps(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        origin: ProposalOrigin,
    ) -> list[PlanStep]:
        await self.gate(request, actor, client_id, origin=origin)
        raw = payload.get("steps")
        if isinstance(raw, list):
            if len(raw) > MAX_STEPS:
                raise HTTPException(422, "plan_too_many_steps")
            if any(isinstance(s, dict) and s.get("kind") not in STEP_KINDS for s in raw):
                raise HTTPException(422, "plan_step_not_allowed")
        try:
            plan = PlanProposalPayload.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            raise HTTPException(422, "invalid proposal payload") from None
        if sum(step.kind not in EMAIL_KINDS for step in plan.steps) > MAX_NON_EMAIL_STEPS:
            raise HTTPException(422, "plan_too_many_steps")
        return plan.steps

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
        """Gate every step kind (a refusal refuses the card), then read each step's card.

        A step whose card now refuses or reports a version other than the stored one is
        shown as changed; it does not refuse the plan.
        """
        steps = await self._steps(request, actor, client_id, payload, origin)
        for kind in _distinct(steps):
            await self._children[kind].gate(request, actor, client_id, origin=origin)

        def changed(step: PlanStep) -> Callable[[], Awaitable[bool]]:
            async def check() -> bool:
                try:
                    _, current = await self._children[step.kind].card(
                        request,
                        actor,
                        client_id,
                        dict(step.payload),
                        origin=origin,
                        language=language,
                    )
                except HTTPException:
                    return True
                return current != step.version

            return check

        drifted = await _bounded([changed(step) for step in steps])
        text = _PLAN_TEXT[language]
        fields = []
        for step, moved in zip(steps, drifted, strict=True):
            title = text[f"step_{step.kind}"]
            if moved:
                title = text["changed"].format(title=title)
            draft = step.payload.get("draft_id") if step.kind in EMAIL_KINDS else None
            fields.append(CardField(label=title, value=str(draft) if draft else ""))
        count, moved_count = len(steps), sum(drifted)
        summary = text["summary"]
        if moved_count:
            key = "changed_one" if moved_count == 1 else "changed_many"
            summary += text[key].format(count=moved_count)
        content = CardContent(
            title=text["title_one" if count == 1 else "title_many"].format(count=count),
            summary=summary,
            fields=fields,
            language=language,
        )
        return content, plan_version(steps)

    async def _run_step(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        step: PlanStep,
        *,
        proposal_id: UUID,
        origin: ProposalOrigin,
        gate_refusal: HTTPException | None,
    ) -> _Outcome:
        if gate_refusal is not None:
            return _failed(gate_refusal)
        # No child card here: after a lease retake, a step the dead run finished has a card
        # that refuses (the draft is sent), while its execute replays the receipt by key.
        try:
            receipt = await self._children[step.kind].execute(
                request,
                actor,
                client_id,
                dict(step.payload),
                proposal_id=proposal_id,
                expected_version=step.version,
                origin=origin,
            )
        except HTTPException as exc:
            return _refused(exc)
        except Exception:
            # The owning service's transaction rolled back; record it and let the rest run.
            logger.exception("workspace.plan.step_failed", kind=step.kind)
            return _Outcome("failed", "step_failed")
        return _finished(step.kind, receipt)

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
        """Run every step on its own and return the per-step receipt.

        Raises when every step failed with one identical refusal (the package switched off, a
        mailbox disconnected), and ``proposal_stale`` when every step drifted, so the registry
        releases the claim. A refusal means the owning service acted on nothing, with one
        exception: ``send_outcome_unknown`` comes after Gmail may have sent. The one-send
        guarantee still holds then, because the draft stays ``sending``: the step's replay
        answers the same refusal and the next card refuses the draft.
        """
        steps = await self._steps(request, actor, client_id, payload, origin)
        outcomes: list[_Outcome | None] = [None] * len(steps)
        started = [False] * len(steps)
        semaphore = asyncio.Semaphore(CONCURRENCY)
        refusals: dict[str, HTTPException] = {}
        loop = asyncio.get_running_loop()
        cut_off = loop.time() + PLAN_DEADLINE_SECONDS

        async def run(index: int, step: PlanStep) -> None:
            async with semaphore:
                if loop.time() >= cut_off:
                    return  # never started: not_run
                started[index] = True
                outcomes[index] = await self._run_step(
                    request,
                    actor,
                    client_id,
                    step,
                    proposal_id=step_id(proposal_id, index),
                    origin=origin,
                    gate_refusal=refusals.get(step.kind),
                )

        # The backstop cancels what is still running; CancelledError from anywhere else (the
        # request going away) is never caught.
        backstop = asyncio.timeout(PLAN_BACKSTOP_SECONDS)
        try:
            async with backstop:
                for kind in _distinct(steps):
                    try:
                        await self._children[kind].gate(request, actor, client_id, origin=origin)
                    except HTTPException as exc:
                        refusals[kind] = exc
                async with asyncio.TaskGroup() as group:
                    for index, step in enumerate(steps):
                        group.create_task(run(index, step))
        except TimeoutError:
            if not backstop.expired():
                raise
        # A step cut off by the backstop was started (it may have acted): it is failed, not
        # not_run, so nothing reads it as untouched.
        final = [
            outcome or (_CUT_OFF if begun else _NOT_RUN)
            for outcome, begun in zip(outcomes, started, strict=True)
        ]
        first = final[0].refusal
        if first is not None and all(
            o.refusal is not None
            and (o.refusal.status_code, _detail(o.refusal)) == (first.status_code, _detail(first))
            for o in final
        ):
            raise first
        if all(o == _CHANGED for o in final):
            raise ProposalError("proposal_stale")  # every step drifted: the plan is stale
        return {
            "steps": [
                outcome.entry(index, step.kind)
                for index, (outcome, step) in enumerate(zip(final, steps, strict=True))
            ]
        }
