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
from dataclasses import dataclass, field, replace
from typing import Any, Literal, get_args
from uuid import UUID, uuid5

import structlog
from fastapi import HTTPException, Request
from pydantic import ValidationError

from csa.console.contracts import ConsoleActor
from csa.core.tenancy import tx
from csa.workspace.contracts import (
    MAX_STEPS,
    CardField,
    Language,
    PlanProposalPayload,
    PlanStep,
    ProposalKind,
    ProposalOut,
)
from csa.workspace.registry import (
    CardContent,
    ProposalError,
    ProposalExecutor,
    ProposalOrigin,
    ProposalRegistry,
)

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
        # draft_already_sending is NOT here: under the step's own key it means this plan's
        # earlier attempt is still sending or died mid-send (the mail may have gone out), so
        # it is failed with its code, never "changed since you reviewed it".
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
off, the send limit, ``draft_already_sending``, ``send_outcome_unknown``) is ``failed`` with
its code.

Every step kind's execute compares the stored version in its owning service, after its key
replay: ``send_draft`` and ``export_draft`` (``draft_changed``), ``decide_approval``
(``proposal_stale`` on the conversation's ``inbox_version``), and the booking, HR and
training services. The plan card cannot do it for them: its version is the hash of the
stored versions, so the registry's own check at confirm always passes."""

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
        "left_out_one": " 1 step could not be retried and is left out.",
        "left_out_many": " {count} steps could not be retried and are left out.",
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
        "left_out_one": " 1 langkah tidak dapat dicuba semula dan ditinggalkan.",
        "left_out_many": " {count} langkah tidak dapat dicuba semula dan ditinggalkan.",
        "step_email_send": "Hantar e-mel",
        "step_email_export": "Simpan e-mel ke draf Gmail",
        "step_booking_action": "Tempah, alih atau batalkan tempahan",
        "step_inbox_decision": "Putuskan balasan yang ditahan",
        "step_training_submit": "Hantar permohonan latihan",
        "step_hr_person": "Ubah maklumat orang dalam People",
    },
}


# Why a step or a recipient was left off a plan card: counts and these reasons only, never who.
_LEFT_OUT: dict[Language, dict[str, str]] = {
    "en": {
        "head": " Not included: {count} ({reasons}).",
        "needs_details": "needs more details",
        "unavailable": "could not be prepared",
        "not_found": "not found",
        "ambiguous": "more than one match",
        "no_email": "no email address",
        "duplicate": "listed twice",
        "over_limit": "over the limit of 20",
        "draft_failed": "draft failed",
        "draft_deadline": "not drafted in time",
        "rest": ' Say "the rest" to email the next ones.',
        "continued": " Continuing the group after {count} already offered.",
    },
    "ms": {
        "head": " Tidak disertakan: {count} ({reasons}).",
        "needs_details": "perlukan butiran lanjut",
        "unavailable": "tidak dapat disediakan",
        "not_found": "tidak dijumpai",
        "ambiguous": "lebih daripada satu padanan",
        "no_email": "tiada alamat e-mel",
        "duplicate": "disenaraikan dua kali",
        "over_limit": "melebihi had 20",
        "draft_failed": "draf gagal",
        "draft_deadline": "tidak sempat didraf",
        "rest": ' Katakan "selebihnya" untuk e-mel yang seterusnya.',
        "continued": " Menyambung kumpulan selepas {count} yang sudah ditawarkan.",
    },
}


def left_out_note(counts: Mapping[str, int], language: Language) -> str:
    """The summary's count line, for example ``Not included: 3 (2 not found, 1 no email
    address).``, or an empty string when nothing was left out.

    Unknown reason codes read as "could not be prepared"; the text never names anyone.
    """
    text = _LEFT_OUT[language]
    kept = {reason: n for reason, n in counts.items() if n > 0}
    if not kept:
        return ""
    reasons = ", ".join(f"{n} {text.get(r, text['unavailable'])}" for r, n in kept.items())
    return text["head"].format(count=sum(kept.values()), reasons=reasons)


def card_notes(
    notes: Sequence[str],
    counts: Mapping[str, int],
    *,
    more: bool,
    language: Language,
    continued: int = 0,
) -> str:
    """What a plan card's summary adds: that a page continues a group (``continued``, the
    batch's offset, when above 0), the batch's count-only notes ("sending 12, saving 8 as
    drafts"), what was left out, and how to ask for the rest of a group."""
    text = _LEFT_OUT[language]
    added = f" {'; '.join(notes)}." if notes else ""
    if added:
        added = f" {added[1].upper()}{added[2:]}"
    if continued > 0:
        added = text["continued"].format(count=continued) + added
    return added + left_out_note(counts, language) + (text["rest"] if more else "")


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

    async def refresh(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        steps: Sequence[PlanStep],
        *,
        language: Language = "en",
    ) -> tuple[list[PlanStep], int]:
        """Read each step's version again from its child's ``card``, as the assistant.

        Returns the steps with their current versions, in order, and how many were left out
        because their card now refuses (a draft already sent, a closed slot, a package switched
        off). The stored versions are never reused.
        """

        def read(step: PlanStep) -> Callable[[], Awaitable[PlanStep | None]]:
            async def one() -> PlanStep | None:
                try:
                    _, current = await self._children[step.kind].card(
                        request,
                        actor,
                        client_id,
                        dict(step.payload),
                        origin="assistant",
                        language=language,
                    )
                except HTTPException:
                    return None
                return step.model_copy(update={"version": current})

            return one

        read_back = await _bounded([read(step) for step in steps])
        kept = [step for step in read_back if step is not None]
        return kept, len(steps) - len(kept)

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
        # that refuses (the draft is sent), while its execute replays the receipt by key. The
        # drift check is the child's: each service compares expected_version after its replay.
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


_RETRYABLE: frozenset[str] = frozenset({"failed", "not_run"})


def _retry_indexes(result: Mapping[str, Any] | None, count: int) -> list[int]:
    """The stored steps to try again: the receipt entries that failed or never ran.

    ``done`` steps acted and ``skipped`` ones changed since the card, so neither is retried. A
    ``failed``/``plan_deadline`` step may have acted: re-reading its card refuses it or moves its
    version, and it is then left out and counted, not special-cased here.
    """
    entries = result.get("steps") if result else None
    if not isinstance(entries, list):
        return []
    found = {
        entry["index"]
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("status") in _RETRYABLE
        and isinstance(entry.get("index"), int)
        and 0 <= entry["index"] < count
    }
    return sorted(found)


async def retry_plan(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    proposal_id: UUID,
    registry: ProposalRegistry,
) -> ProposalOut:
    """Prepare a new plan from an executed plan's failed and not-run steps.

    Owner and login session only (``proposal_session_mismatch``); 409 ``plan_not_executed`` for a
    plan that has not run, and ``plan_nothing_to_retry`` when no failed or not-run step is left
    to propose. The new plan is stored as the assistant's, bound to the caller's session and
    tied to the same conversation, and confirmed on its own like any plan. No connection is held
    while the children's cards are read.
    """
    pool = request.app.state.pool
    async with tx(pool, str(client_id)) as conn:
        old = await registry.owned(conn, actor, client_id, proposal_id)
    if old.login_session_id != actor.session_id:
        raise ProposalError("proposal_session_mismatch")
    if old.kind != "plan":
        raise HTTPException(409, "proposal_not_retryable")
    if old.status != "executed":
        raise HTTPException(409, "plan_not_executed")
    stored = PlanProposalPayload.model_validate(old.payload).steps
    wanted = [stored[index] for index in _retry_indexes(old.result, len(stored))]
    if not wanted:
        raise HTTPException(409, "plan_nothing_to_retry")
    executor = registry.executor("plan")
    if not isinstance(executor, PlanExecutor):
        raise RuntimeError("the plan executor is not a PlanExecutor")
    steps, left_out = await executor.refresh(
        request, actor, client_id, wanted, language=old.language
    )
    if not steps:
        raise HTTPException(409, "plan_nothing_to_retry")
    prepared = await registry.prepare(
        request,
        actor,
        client_id,
        "plan",
        plan_payload(steps),
        origin="assistant",
        language=old.language,
    )
    if left_out:
        text = _PLAN_TEXT[old.language]
        note = text["left_out_one" if left_out == 1 else "left_out_many"].format(count=left_out)
        content = replace(prepared.content, summary=prepared.content.summary + note)
        prepared = replace(prepared, content=content)
    async with tx(pool, str(client_id)) as conn:
        return await registry.insert(
            conn, actor, client_id, prepared, thread_id=old.thread_id, origin="assistant"
        )
