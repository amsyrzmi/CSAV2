"""Assistant card "mark my task done" (HR assistant slice 1c-ii).

``resolve_task_complete`` finds the caller's own open process step in My tasks (read with the
caller's own console session, so the database decides what they see) and prepares one confirm
card, asks which step when several are open, or says there is nothing to mark. ``HrTaskExecutor``
(kind ``hr_task``) re-reads the step as the caller when it builds the card and again on Confirm,
and completes it through the console's own ``task.complete`` operation. It never approves, sends
back, skips, reschedules or reassigns a step, never writes a note, and never completes an
automatic-check step, a pool step, or a step waiting for approval.

The stored payload is the step id and its version only. The stored result is the operation's
receipt. A pick list's stored copy keeps no title, name or date (turn rows are readable
tenant-wide). The card itself names the step and the person (its summary and fields are kept on
the card row and the turn row, as the people cards keep a person's name).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request

from csa.console.contracts import ConsoleActor
from csa.console.services import require_package
from csa.hr.api import hr_http_error, hr_session_from
from csa.hr.policy import HrPolicyError
from csa.hr.process_models import MyTask, TaskComplete
from csa.hr.processes import HrProcesses
from csa.hr.records import HrSession
from csa.workspace.contracts import (
    AnswerResult,
    CardField,
    ClarificationOption,
    ClarificationProposal,
    HrTaskCompleteProposal,
    Language,
    ProposalKind,
)
from csa.workspace.directory import search_words
from csa.workspace.executors import _CARD_TEXT, _key
from csa.workspace.hr_people import MAX_OPTIONS, OPTION_LABEL, as_id, format_day, hr_service
from csa.workspace.hr_processes import ProcessAnswer, Reader
from csa.workspace.planner import PlannerFacts
from csa.workspace.registry import (
    CardContent,
    PreparedProposal,
    ProposalError,
    ProposalOrigin,
    ProposalRegistry,
)
from csa.workspace.rules import TAB_GATES, unlocked

_PAGE = 200
_SCAN = 1000  # rows of My tasks read at most; a step beyond them is not offered
_LINE = 200


def can_mark_done(task: MyTask) -> bool:
    """The caller's own open step the assistant may complete: assigned to the caller, not a pool
    step, not waiting for anyone's approval, no automatic check (the console completes those)."""
    return (
        task.mine
        and not task.pool
        and not task.needs_my_approval
        and task.status == "todo"
        and task.auto_check is None
    )


def _title(task: MyTask, language: Language) -> str:
    if language == "ms" and task.title_ms and task.title_ms.strip():
        return " ".join(task.title_ms.split())[:_LINE]
    return " ".join(task.title_en.split())[:_LINE]


async def _my_open_steps(processes: HrProcesses, session: HrSession) -> list[MyTask]:
    """The caller's steps the assistant may complete, in My tasks' own order."""
    found: list[MyTask] = []
    seen = 0
    after: UUID | None = None
    while True:
        page = await processes.my_tasks(session, limit=_PAGE, after=after)
        found.extend(task for task in page.items if can_mark_done(task))
        seen += len(page.items)
        after = page.next_cursor
        if after is None or seen >= _SCAN:
            return found


def _words_match(task: MyTask, wanted: str) -> bool:
    words = [word.casefold() for word in search_words(wanted)]
    if not words:
        return False
    titles = [task.title_en.casefold(), (task.title_ms or "").casefold()]
    return any(all(word in title for word in words) for title in titles)


def _narrow(steps: list[MyTask], wanted: str) -> list[MyTask]:
    """The steps a picked task id or typed title words name; every step when nothing was said,
    and none when the words or the id match no open step (the caller then asks which)."""
    if not wanted:
        return steps
    target = as_id(wanted)
    if target is not None:
        return [task for task in steps if task.task_id == target]
    return [task for task in steps if _words_match(task, wanted)]


async def resolve_task_complete(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    proposal: HrTaskCompleteProposal,
    facts: PlannerFacts,
    registry: ProposalRegistry,
    text: dict[str, str],
) -> ProcessAnswer | PreparedProposal:
    """One card, a pick list, or one sentence; ``text`` is the turn's copy
    (``service._TEXT[facts.language]``)."""
    if not unlocked(facts.packages, TAB_GATES["hr"]):
        return _say(text["processes_unavailable"])
    reader = Reader(request, actor, client_id, facts.language, text)
    try:
        steps = await _my_open_steps(reader.processes, reader.session)
    except HrPolicyError as exc:
        if exc.code == "permission_denied":
            return _say(text["task_complete_none"])
        raise hr_http_error(exc) from None
    if not steps:
        return _say(text["task_complete_none"])
    wanted = (proposal.task or "").strip()
    chosen = _narrow(steps, wanted)
    if len(chosen) == 1:
        task = chosen[0]
        payload = {"task_id": str(task.task_id), "expected_version": int(task.version)}
        return await registry.prepare(
            request,
            actor,
            client_id,
            "hr_task",
            payload,
            origin="assistant",
            language=facts.language,
        )
    # Words or an id that name no open step (a step finished meanwhile, a guess) never make a card
    # for some other step: the user picks from what is open.
    return _which(reader, chosen or steps)


def _say(markdown: str) -> ProcessAnswer:
    return ProcessAnswer(shown=AnswerResult(action="answer", markdown=markdown))


def _label(reader: Reader, task: MyTask) -> str:
    line = reader.text["process_task_line"].format(
        title=reader.title(task.title_en, task.title_ms),
        kind=reader.kind(task.process_kind, task.process_name),
        name=" ".join(task.employee_name.split())[:_LINE],
        due=reader.due(task.due_on, task.overdue),
    )
    return line[:OPTION_LABEL]


def _which(reader: Reader, steps: list[MyTask]) -> ProcessAnswer:
    """Up to five options valued by step id, labelled "title — kind for name, due". The stored copy
    keeps the ids and a counter only: no title, name or date."""
    text = reader.text
    shown_steps = steps[:MAX_OPTIONS]
    shown = [
        ClarificationOption(label=_label(reader, task), value=str(task.task_id))
        for task in shown_steps
    ]
    stored = [
        ClarificationOption(label=text["task_option_stored"].format(n=n), value=str(task.task_id))
        for n, task in enumerate(shown_steps, start=1)
    ]
    question = text["task_complete_which"]
    return ProcessAnswer(
        shown=ClarificationProposal(action="clarification", question=question, options=shown),
        stored=ClarificationProposal(action="clarification", question=question, options=stored),
    )


class HrTaskExecutor:
    """Completes one of the caller's own process steps after Confirm (``task.complete``).

    The payload is ``{task_id, expected_version}``; ``card`` reads the step as the caller, refuses
    one that is no longer theirs to mark done, and binds the card to the step's version."""

    kind: ProposalKind = "hr_task"

    @staticmethod
    def _session(
        request: Request, actor: ConsoleActor, client_id: UUID, origin: ProposalOrigin
    ) -> HrSession:
        session = hr_session_from(request, actor, client_id, mutation=True)
        return session.model_copy(update={"assistant": origin == "assistant"})

    @staticmethod
    def _ids(payload: dict[str, Any]) -> tuple[UUID, int]:
        task_id, version = payload.get("task_id"), payload.get("expected_version")
        if (
            set(payload) != {"task_id", "expected_version"}
            or not isinstance(task_id, str)
            or isinstance(version, bool)
            or not isinstance(version, int)
        ):
            raise HTTPException(422, "invalid proposal payload")
        try:
            return UUID(task_id), version
        except ValueError:
            raise HTTPException(422, "invalid proposal payload") from None

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        await require_package(
            request.app.state.pool, client_id, frozenset({"hr"}), frozenset({"active"})
        )
        if origin != "assistant":
            return
        try:
            await hr_service(request).require_capability(
                self._session(request, actor, client_id, origin), "hr_assistant"
            )
        except HrPolicyError as exc:
            raise hr_http_error(exc) from None

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
        """Read the step as the caller and render its card (stale when it is no longer theirs)."""
        task_id, version = self._ids(payload)
        await self.gate(request, actor, client_id, origin=origin)
        service = hr_service(request)
        read = hr_session_from(request, actor, client_id, mutation=False)
        try:
            steps = await _my_open_steps(HrProcesses(service), read)
        except HrPolicyError as exc:
            raise hr_http_error(exc) from None
        task = next((step for step in steps if step.task_id == task_id), None)
        if task is None or int(task.version) != version:
            raise ProposalError("proposal_stale")
        return _card(task, _CARD_TEXT[language], language), str(task.version)

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
        """Complete the step as the caller; a step that changed since the card is stale."""
        task_id, _ = self._ids(payload)
        if expected_version is None:
            raise RuntimeError("task proposal has no bound version")
        # The person's own Confirm click, with the console's CSRF proof: the process writes refuse
        # an assistant-bound session ("human_operation_required"), so this is not one. The
        # registry re-reads ``card`` (package, assistant switch, the step's version) first.
        session = hr_session_from(request, actor, client_id, mutation=True)
        try:
            receipt = await HrProcesses(hr_service(request)).task_action(
                session,
                "task.complete",
                task_id,
                TaskComplete(expected_version=int(expected_version)),
                _key(proposal_id, "apply"),
            )
        except HrPolicyError as exc:
            if exc.code in {"task_changed", "task_closed", "task_awaiting_approval"}:
                raise ProposalError("proposal_stale") from None
            raise hr_http_error(exc) from None
        # The operation's receipt: operation, ids, statuses and version, never a title or name.
        return receipt.model_dump(mode="json")


def _card(task: MyTask, text: dict[str, str], language: Language) -> CardContent:
    kind = (
        " ".join(task.process_name.split())[:_LINE]
        if task.process_kind == "custom" and task.process_name and task.process_name.strip()
        else text[f"task_kind_{task.process_kind}"]
    )
    title = _title(task, language)
    name = " ".join(task.employee_name.split())[:_LINE]
    if task.approval_by is None:
        after = text["task_after_done"]
    else:
        after = text["task_after_approval"].format(by=text[f"task_approver_{task.approval_by}"])
    due = format_day(task.due_on, language) if task.due_on is not None else text["none"]
    if task.overdue:
        due = f"{due} ({text['task_overdue']})"
    return CardContent(
        title=text["hr_task_title"],
        summary=text["hr_task_summary"].format(title=title, name=name, kind=kind),
        fields=[
            CardField(label=text["label_step"], value=title),
            CardField(label=text["label_for"], value=f"{name} ({kind})"),
            CardField(label=text["label_due"], value=due),
            CardField(label=text["label_after"], value=after),
        ],
        language=language,
    )


__all__ = ["HrTaskExecutor", "can_mark_done", "resolve_task_complete"]
