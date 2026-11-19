"""Assistant answers about HR processes (slice 1c-i): the caller's own tasks, one person's
onboarding or offboarding, who is onboarding, who is leaving, and what is overdue. Read only.

Every read goes through ``HrProcesses`` with the caller's own console read session
(``hr_session_from(..., mutation=False)``, not an assistant session), so the database decides
what the caller sees, exactly as on the console's My tasks and Processes pages: the HR team
everything, a manager their direct reports' titles, status and dates, a person their own
onboarding and offboarding, a task owner their own tasks, anyone else nothing. Nothing here
filters on a guessed role, writes, or completes, approves or changes a task. Notes, descriptions
and reference notes are never shown, whoever asks.

The answer (an ``AnswerResult``, or a clarification when a name matches several processes) is in
the turn's language; the stored copy keeps counts and fixed text only, because turn rows are
readable tenant-wide (see ``ProcessAnswer``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from fastapi import Request

from csa.console.contracts import ConsoleActor
from csa.hr.api import hr_http_error, hr_session_from
from csa.hr.people_rules import kl_today
from csa.hr.policy import HrPolicyError
from csa.hr.process_models import (
    MyTask,
    ProcessKind,
    ProcessRow,
    ProcessStatus,
    ProcessTask,
)
from csa.hr.processes import HrProcesses
from csa.workspace.contracts import (
    AnswerResult,
    ClarificationOption,
    ClarificationProposal,
    HrProcessReadProposal,
    Language,
)
from csa.workspace.directory import search_words
from csa.workspace.hr_people import MAX_OPTIONS, OPTION_LABEL, as_id, format_day, hr_service
from csa.workspace.planner import PlannerFacts
from csa.workspace.rules import TAB_GATES, unlocked

SHOWN = 10  # lines of a list at most; a count line says how many more
_PAGE = 200  # rows per read (the console reads' own page limit)
_SCAN = 1000  # rows read at most for one answer; past it the count reads "1000+"
_DETAILS = 50  # processes opened at most for the overdue view
_CLOSED = frozenset({"done", "skipped"})
_LINE = 200  # characters of a title or name at most, on one line


@dataclass(frozen=True)
class ProcessAnswer:
    """What the user sees and, when it differs, what the turn row stores.

    ``stored`` is None when ``shown`` is fixed text with no name or title in it.
    """

    shown: AnswerResult | ClarificationProposal
    stored: AnswerResult | ClarificationProposal | None = None


@dataclass(frozen=True)
class _Step:
    """One listed step: its title in the turn's language and what it belongs to."""

    title: str
    kind: str
    name: str
    due_on: date | None
    overdue: bool


def _one_line(text: str) -> str:
    return " ".join(text.split())[:_LINE]


class _Reader:
    """The caller's process reads and the turn's wording."""

    def __init__(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        language: Language,
        text: dict[str, str],
    ) -> None:
        service = hr_service(request)
        self.processes = HrProcesses(service)
        self.session = hr_session_from(request, actor, client_id, mutation=False)
        self.today = kl_today(service.now())
        self.language: Language = language
        self.text = text

    # --- reads, every page up to _SCAN rows ---------------------------------------------------

    async def my_tasks(self) -> tuple[list[MyTask], bool]:
        items: list[MyTask] = []
        after: UUID | None = None
        while True:
            page = await self.processes.my_tasks(self.session, limit=_PAGE, after=after)
            items.extend(page.items)
            after = page.next_cursor
            if after is None:
                return items, False
            if len(items) >= _SCAN:
                return items, True

    async def rows(
        self,
        *,
        kind: ProcessKind | None = None,
        status: ProcessStatus | None = None,
        overdue: bool | None = None,
    ) -> tuple[list[ProcessRow], bool]:
        items: list[ProcessRow] = []
        after: UUID | None = None
        while True:
            page = await self.processes.processes(
                self.session, kind=kind, status=status, overdue=overdue, limit=_PAGE, after=after
            )
            items.extend(page.items)
            after = page.next_cursor
            if after is None:
                return items, False
            if len(items) >= _SCAN:
                return items, True

    # --- wording ------------------------------------------------------------------------------

    def title(self, title_en: str, title_ms: str | None) -> str:
        """``title_ms`` in a Malay turn when the template has one, else ``title_en``."""
        if self.language == "ms" and title_ms and title_ms.strip():
            return _one_line(title_ms)
        return _one_line(title_en)

    def kind(self, kind: ProcessKind, name: str | None) -> str:
        if kind == "custom":
            return _one_line(name) if name and name.strip() else self.text["process_kind_custom"]
        return self.text[f"process_kind_{kind}"]

    def due(self, due_on: date | None, overdue: bool) -> str:
        """The due date in words: overdue by 3 days, due today, due tomorrow, due 1 May 2027,
        or "was due 3 November 2026" for a past date the database does not call overdue (an
        open step of a closed process)."""
        text = self.text
        if due_on is None:
            return text["process_no_due"]
        if overdue:
            days = max((self.today - due_on).days, 1)
            if days == 1:
                return text["process_overdue_one"]
            return text["process_overdue_many"].format(n=days)
        if due_on == self.today:
            return text["process_due_today"]
        if due_on == self.today + timedelta(days=1):
            return text["process_due_tomorrow"]
        day = format_day(due_on, self.language)
        if due_on < self.today:
            return text["process_was_due"].format(day=day)
        return text["process_due_on"].format(day=day)

    def steps(self, done: int, total: int) -> str:
        word = self.text["process_step" if total == 1 else "process_steps"]
        return self.text["process_progress"].format(done=done, total=total, steps=word)

    def count(self, count: int, cut: bool) -> str:
        return f"{count}+" if cut else str(count)

    def more(self, count: int, cut: bool, page: str) -> list[str]:
        if count <= SHOWN:
            return []
        return [self.text["process_more"].format(count=self.count(count - SHOWN, cut), page=page)]

    def stored(self, count: int) -> AnswerResult:
        """Counts only: no name, title or note reaches the tenant-readable turn row."""
        text = self.text
        items = f"{count} {text['item' if count == 1 else 'items']}"
        return AnswerResult(action="answer", markdown=text["process_stored"].format(count=items))

    def task_line(self, step: _Step) -> str:
        return self.text["process_task_line"].format(
            title=step.title,
            kind=step.kind,
            name=_one_line(step.name),
            due=self.due(step.due_on, step.overdue),
        )


# The console's empty sentence of each view, for a caller with no HR authority at all.
_NOTHING = {
    "my_tasks": "process_tasks_none",
    "person_process": "process_person_not_found",
    "who_onboarding": "process_onboarding_none",
    "who_leaving": "process_leaving_none",
    "overdue": "process_overdue_none",
}


def _say(markdown: str) -> ProcessAnswer:
    return ProcessAnswer(shown=AnswerResult(action="answer", markdown=markdown))


async def resolve_process_read(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    proposal: HrProcessReadProposal,
    facts: PlannerFacts,
    text: dict[str, str],
) -> ProcessAnswer:
    """An ``hr_process_read`` answered from the caller's own process reads; ``text`` is the
    turn's copy (``service._TEXT[facts.language]``)."""
    if not unlocked(facts.packages, TAB_GATES["hr"]):
        return _say(text["processes_unavailable"])
    wanted = (proposal.person or "").strip()
    if proposal.view == "person_process" and not wanted:
        ask = ClarificationProposal(action="clarification", question=text["ask_person"])
        return ProcessAnswer(shown=ask)
    reader = _Reader(request, actor, client_id, facts.language, text)
    try:
        if proposal.view == "my_tasks":
            return await _my_tasks(reader)
        if proposal.view == "person_process":
            return await _person_process(reader, wanted)
        if proposal.view == "overdue":
            return await _overdue(reader)
        kind: ProcessKind = "onboarding" if proposal.view == "who_onboarding" else "offboarding"
        return await _who(reader, kind)
    except HrPolicyError as exc:
        # No HR authority at all: the console's own reads would show this caller nothing.
        if exc.code == "permission_denied":
            return _say(text[_NOTHING[proposal.view]])
        raise hr_http_error(exc) from None


async def _my_tasks(reader: _Reader) -> ProcessAnswer:
    """My tasks as the console lists it: the caller's open steps (soonest due first), the HR
    pool for the HR team, and the steps waiting for the caller's approval."""
    text = reader.text
    tasks, cut = await reader.my_tasks()
    if not tasks:
        return _say(text["process_tasks_none"])
    lines = [text["process_tasks_head"].format(count=reader.count(len(tasks), cut))]
    for index, task in enumerate(tasks[:SHOWN], start=1):
        step = _Step(
            title=reader.title(task.title_en, task.title_ms),
            kind=reader.kind(task.process_kind, task.process_name),
            name=task.employee_name,
            due_on=task.due_on,
            overdue=task.overdue,
        )
        marks = _marks(reader, task)
        line = reader.task_line(step)
        lines.append(f"{index}. {line} ({'; '.join(marks)})" if marks else f"{index}. {line}")
    lines += reader.more(len(tasks), cut, "My tasks")
    return ProcessAnswer(
        shown=AnswerResult(action="answer", markdown="\n".join(lines)),
        stored=reader.stored(len(tasks)),
    )


def _marks(reader: _Reader, task: MyTask) -> list[str]:
    text, marks = reader.text, []
    if task.pool:
        marks.append(text["process_pool"])
    elif task.needs_my_approval:
        marks.append(text["process_to_approve"])
    elif not task.mine and task.assignee_name:
        marks.append(text["process_assigned"].format(name=_one_line(task.assignee_name)))
    if task.status == "awaiting_approval" and not task.needs_my_approval:
        marks.append(text["process_awaiting"])
    elif task.status == "rejected":
        marks.append(text["process_sent_back"])
    return marks


async def _who(reader: _Reader, kind: ProcessKind) -> ProcessAnswer:
    """Active onboarding (or offboarding) processes the caller can see, soonest next due first."""
    text = reader.text
    rows, cut = await reader.rows(kind=kind, status="active")
    empty, head = (
        ("process_onboarding_none", "process_onboarding_head")
        if kind == "onboarding"
        else ("process_leaving_none", "process_leaving_head")
    )
    if not rows:
        return _say(text[empty])
    rows.sort(key=lambda row: (row.next_due_on or date.max, row.employee_name.casefold()))
    lines = [text[head].format(count=reader.count(len(rows), cut))]
    for index, row in enumerate(rows[:SHOWN], start=1):
        line = f"{_one_line(row.employee_name)}: {reader.steps(row.tasks_done, row.tasks_total)}"
        if row.next_due_on is not None:
            due = reader.due(row.next_due_on, row.overdue)
            line += text["process_next"].format(due=due)
        lines.append(f"{index}. {line}")
    lines += reader.more(len(rows), cut, "Processes")
    return ProcessAnswer(
        shown=AnswerResult(action="answer", markdown="\n".join(lines)),
        stored=reader.stored(len(rows)),
    )


async def _overdue(reader: _Reader) -> ProcessAnswer:
    """Overdue steps the caller can see: in My tasks, and in each active overdue process the
    caller may open (its tasks as the database shows them to the caller), most overdue first."""
    text = reader.text
    found: dict[UUID, _Step] = {}
    tasks, cut = await reader.my_tasks()
    for task in tasks:
        if task.overdue:
            found[task.task_id] = _Step(
                title=reader.title(task.title_en, task.title_ms),
                kind=reader.kind(task.process_kind, task.process_name),
                name=task.employee_name,
                due_on=task.due_on,
                overdue=True,
            )
    rows, rows_cut = await reader.rows(status="active", overdue=True)
    cut = cut or rows_cut or len(rows) > _DETAILS
    for row in rows[:_DETAILS]:
        try:
            detail = await reader.processes.process(reader.session, row.process_id)
        except HrPolicyError as exc:
            if exc.code == "resource_unavailable":
                continue  # closed or out of sight since the list was read
            raise
        for task in detail.tasks:
            if task.overdue and task.task_id not in found:
                found[task.task_id] = _Step(
                    title=reader.title(task.title_en, task.title_ms),
                    kind=reader.kind(row.kind, row.name),
                    name=row.employee_name,
                    due_on=task.due_on,
                    overdue=True,
                )
    if not found:
        return _say(text["process_overdue_none"])
    steps = sorted(found.values(), key=lambda step: (step.due_on or date.max, step.title))
    lines = [text["process_overdue_head"].format(count=reader.count(len(steps), cut))]
    lines += [f"{i}. {reader.task_line(step)}" for i, step in enumerate(steps[:SHOWN], start=1)]
    lines += reader.more(len(steps), cut, "Processes")
    return ProcessAnswer(
        shown=AnswerResult(action="answer", markdown="\n".join(lines)),
        stored=reader.stored(len(steps)),
    )


def _matches(rows: list[ProcessRow], wanted: str) -> list[ProcessRow]:
    """The visible processes the words name: a picked option's process id (or an employee id),
    else every word in the person's name, in any order (the People search rule for names).
    Active processes win over closed ones; a closed one is only answered when nothing is open."""
    target = as_id(wanted)
    if target is not None:
        found = [row for row in rows if target in (row.process_id, row.employee_id)]
    else:
        words = [word.casefold() for word in search_words(wanted)]
        if not words:
            return []
        found = [row for row in rows if all(w in row.employee_name.casefold() for w in words)]
    active = [row for row in found if row.status == "active"]
    return active or found


async def _person_process(reader: _Reader, wanted: str) -> ProcessAnswer:
    """One person's process, found among the processes the caller can see (never in People,
    which a manager or a new starter may not read). Nobody visible gets one sentence, whether
    or not the person exists."""
    text = reader.text
    rows, _ = await reader.rows()
    found = _matches(rows, wanted)
    if not found:
        return _say(text["process_person_not_found"])
    if len(found) > 1:
        return _which(reader, wanted, found)
    row = found[0]
    try:
        detail = await reader.processes.process(reader.session, row.process_id)
    except HrPolicyError as exc:
        if exc.code == "resource_unavailable":
            return _say(text["process_person_not_found"])
        raise
    kind = reader.kind(row.kind, row.name)
    head = text["process_person_head"].format(
        name=_one_line(row.employee_name),
        kind=kind,
        # The Malay sentence starts with the kind: "Onboarding Aina Rahman: ...".
        Kind=kind[:1].upper() + kind[1:],
        progress=reader.steps(row.tasks_done, row.tasks_total),
    )
    lines = [head]
    if row.status != "active":
        lines.append(text["process_closed"].format(status=text[f"process_{row.status}"]))
    open_tasks = sorted(
        (task for task in detail.tasks if task.status not in _CLOSED),
        key=lambda task: (task.due_on or date.max, task.ordinal),
    )
    if not open_tasks:
        lines.append(text["process_open_none"])
    else:
        lines.append(text["process_open_head"].format(count=len(open_tasks)))
        for index, task in enumerate(open_tasks[:SHOWN], start=1):
            lines.append(f"{index}. {_step_line(reader, task)}")
        lines += reader.more(len(open_tasks), False, "Processes")
    return ProcessAnswer(
        shown=AnswerResult(action="answer", markdown="\n".join(lines)),
        stored=reader.stored(len(open_tasks)),
    )


def _step_line(reader: _Reader, task: ProcessTask) -> str:
    """Title, owner role and due date: what every viewer of the process gets (0633)."""
    text = reader.text
    line = text["process_step_line"].format(
        title=reader.title(task.title_en, task.title_ms),
        owner=text[f"process_owner_{task.owner_role}"],
        due=reader.due(task.due_on, task.overdue),
    )
    if task.status == "awaiting_approval":
        line += f" ({text['process_awaiting']})"
    elif task.status == "rejected":
        line += f" ({text['process_sent_back']})"
    return line


def _which(reader: _Reader, wanted: str, found: list[ProcessRow]) -> ProcessAnswer:
    """Several visible processes match: up to five options, valued by process id, labelled
    "Aina Rahman — onboarding, started 3 November 2026" (a closed one marked), with a counter
    when two labels would still read alike.

    A typed name is asked about in the user's own words; a picked id gets a fixed question, so
    no name the user did not type reaches the turn row (the console also repeats the question
    in the next turn's body). The stored options keep a generic kind only: no name, date or
    template name."""
    text = reader.text
    order = sorted(found, key=lambda row: (row.employee_name.casefold(), row.kind, row.started_at))
    found = order[:MAX_OPTIONS]
    if as_id(wanted) is not None:
        question = text["process_which"]
    else:
        question = text["which_person"].format(name=_one_line(wanted))

    def closed(row: ProcessRow, label: str) -> str:
        return label if row.status == "active" else f"{label} ({text[f'process_{row.status}']})"

    names = [_one_line(row.employee_name) for row in found]
    rests = [
        text["process_option"].format(
            name="",
            kind=closed(row, reader.kind(row.kind, row.name)),
            day=format_day(kl_today(row.started_at), reader.language),
        )
        for row in found
    ]
    shown = [
        ClarificationOption(label=label, value=str(row.process_id))
        for label, row in zip(_distinct(names, rests, text), found, strict=True)
    ]
    # The kind word only ("process" for a custom one, never its template's name).
    generic = [closed(row, text[f"process_kind_{row.kind}"]) for row in found]
    stored = [
        ClarificationOption(label=label, value=str(row.process_id))
        for label, row in zip(generic, found, strict=True)
    ]
    return ProcessAnswer(
        shown=ClarificationProposal(action="clarification", question=question, options=shown),
        stored=ClarificationProposal(action="clarification", question=question, options=stored),
    )


def _fit(name: str, rest: str, suffix: str = "") -> str:
    """``name + rest + suffix`` within the option label limit, the name cut first."""
    room = max(OPTION_LABEL - len(rest) - len(suffix), 1)
    return (name[:room] + rest + suffix)[:OPTION_LABEL]


def _distinct(names: list[str], rests: list[str], text: dict[str, str]) -> list[str]:
    """The option labels, every one different: labels that read alike (after cutting) get
    " (1 of 2)", and the counter is never cut off."""
    labels = [_fit(name, rest) for name, rest in zip(names, rests, strict=True)]
    for label in set(labels):
        same = [i for i, seen in enumerate(labels) if seen == label]
        if len(same) > 1:
            for number, i in enumerate(same, start=1):
                counter = text["process_counter"].format(n=number, total=len(same))
                labels[i] = _fit(names[i], rests[i], f" ({counter})")
    return labels


Reader = _Reader  # the process reader, for the "mark my task done" card (hr_task_card.py)

__all__ = ["SHOWN", "ProcessAnswer", "Reader", "resolve_process_read"]
