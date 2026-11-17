"""Synthetic HR process routes for the console browser tests (ruling R1 of the console plan).

`ProcessWorld` holds the steps of a synthetic company's onboarding and offboarding processes and
answers the browser-side process routes the way the gateway does (docs/CONSOLE_API.md, "HR
processes"). `RecordsWorld.answer` in tests/test_hr_access_browser.py hands it every call for this
tenant first: `answer` returns `None` for a path that is not a process path (the old routes then
answer it), and records any process path it has no route for as unexpected, so a stray call still
fails the test.

Extension point: `ROUTES`. Task 4 added the processes list, detail, start, cancel and close, the
template summaries and the HR-only task actions; Task 5 the template editor's routes (read, create,
update, deactivate, preview: `ProcessWorld.people` are the active people a preview counts); Task 6
adds a person's processes. A route is a handler registered in this module with
`@route("GET", r"hr/processes")`; it gets the world, the `Call` and the path match and returns
`(status, body)`. A new path
family whose prefix is not in `PREFIXES` is still delegated while a registered pattern matches it.

All data is synthetic (the repository is public).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Paths under these prefixes belong to the process world even when no route is registered yet.
PREFIXES = ("hr/tasks/", "hr/processes", "hr/templates")
TASK_STATUSES = ("todo", "awaiting_approval", "done", "rejected", "skipped")
OPEN = ("todo", "awaiting_approval", "rejected")
KINDS = ("onboarding", "offboarding", "custom")
PROCESS_STATUSES = ("active", "completed", "cancelled")
ANCHOR = {"onboarding": "start_date", "offboarding": "last_day", "custom": "start_date"}
# The dates a template step of each kind may count from (0631).
ANCHORS = {
    "onboarding": ("start_date", "probation_end"),
    "offboarding": ("last_day",),
    "custom": ("start_date", "last_day", "probation_end"),
}
RULE_KEYS = ("department_id", "employment_type", "legal_entity_reference", "region")
DEFAULT_LIMIT = 50
NOTE_MAX = 2000
REASON_MAX = 500


def kuala_lumpur_today() -> date:
    """The calendar day the server and `processToday()` use."""
    return datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()


@dataclass(frozen=True)
class Member:
    """Someone in the synthetic company: their console user, employee id and name."""

    user_id: str
    employee_id: str
    name: str


@dataclass(frozen=True)
class Call:
    caller: str
    method: str
    path: str
    params: dict[str, str]
    body: Any


Answer = tuple[int, Any]
Handler = Callable[["ProcessWorld", Call, re.Match[str]], Answer]
# Every route is registered in THIS module, never from another test module: the registry is global
# to the pytest process, so a route added elsewhere would answer (or not) depending on import order
# and could turn a call that should be unexpected into an answered one.
ROUTES: list[tuple[str, re.Pattern[str], Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    """Register `handler` for `method` and a path that fully matches `pattern`."""

    def register(handler: Handler) -> Handler:
        ROUTES.append((method, re.compile(pattern), handler))
        return handler

    return register


@dataclass
class Step:
    """One process task, with who may see and act on it (the fields after `version`)."""

    task_id: str
    process_id: str
    process_kind: str
    process_name: str | None
    about: Member
    ordinal: int
    title_en: str
    owner_role: str
    due_on: str | None
    version: int = 1
    # None is the HR pool.
    assignee: Member | None = None
    # "hr", "manager" or None; `approver` is the manager's user id for "manager".
    approval_by: str | None = None
    approver: str | None = None
    title_ms: str | None = None
    description_en: str | None = None
    description_ms: str | None = None
    reference_note: str | None = None
    note: str | None = None
    required: bool = True
    auto_check: str | None = None
    check_met: bool = False
    status: str = "todo"
    sent_back_reason: str | None = None
    done_by_hr: bool = False
    # The assignee cannot get reminders (a read-only member): the HR team sees the step too.
    on_behalf: bool = False
    completed_by: str | None = None
    completed_at: str | None = None
    # HR moved the step to someone else by hand, or set its date by hand.
    assignee_overridden: bool = False
    due_overridden: bool = False


@dataclass
class Process:
    """One process: its header here, its steps in `ProcessWorld.steps` under its id."""

    process_id: str
    kind: str
    # The template's name; None without one.
    name: str | None
    about: Member
    started_at: str
    department_id: str | None = None
    status: str = "active"
    closed_at: str | None = None
    template_id: str | None = None
    template_version: int | None = None
    anchor_dates: dict[str, str | None] = field(default_factory=dict)
    # The console user who started it; None is the system (a person change).
    started_by: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Template:
    """A template: `steps` are the step titles a start copies; `details` the editor's full steps
    (`TemplateStepOut` without the ordinal), made from the titles when not set."""

    template_id: str
    kind: str
    name: str
    version: int = 1
    is_active: bool = True
    steps: tuple[str, ...] = ("First step",)
    rules: dict[str, Any] = field(default_factory=dict)
    details: list[dict[str, Any]] | None = None
    created_at: str = "2026-10-01T02:00:00Z"

    def rule_fields(self) -> dict[str, Any]:
        return {key: self.rules.get(key) for key in RULE_KEYS}

    def summary(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "is_active": self.is_active,
            "rules": self.rule_fields(),
            "step_count": len(self.steps),
            "created_at": self.created_at,
            "updated_at": self.created_at,
        }

    def step_rows(self) -> list[dict[str, Any]]:
        if self.details is not None:
            return self.details
        return [
            {
                "title_en": title,
                "title_ms": None,
                "description_en": None,
                "description_ms": None,
                "owner_role": "hr",
                "owner_employee_id": None,
                "anchor": ANCHOR[self.kind],
                "due_offset_days": 0,
                "required": True,
                "approval_by": None,
                "auto_check": None,
                "reference_note": None,
            }
            for title in self.steps
        ]


@dataclass
class ProcessWorld:
    """The process side of the synthetic company. Empty by default: My tasks says so."""

    unexpected: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    hr_team: set[str] = field(default_factory=set)
    # The server's default page size; a test lowers it to exercise "Load more".
    page_size: int = DEFAULT_LIMIT
    # Every task write that reached a route, valid or not: caller, task id, action and body.
    writes: list[dict[str, Any]] = field(default_factory=list)
    processes: list[Process] = field(default_factory=list)
    templates: list[Template] = field(default_factory=list)
    # Everyone a step may be assigned to or a process started for, by employee id.
    members: dict[str, Member] = field(default_factory=dict)
    # A person's department, by employee id (a process row carries it).
    departments: dict[str, str] = field(default_factory=dict)
    # Answers to give instead of the route's, by "METHOD path": each call takes the first
    # `(status, code)` off its list (a 500 makes a write uncertain, a 409 refuses it).
    failures: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    # The active people a template preview counts: employee id and the four rule fields each.
    people: list[dict[str, Any]] = field(default_factory=list)

    def handles(self, path: str) -> bool:
        return path.startswith(PREFIXES) or any(p.fullmatch(path) for _, p, _ in ROUTES)

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> Answer | None:
        """The answer for a process path, or None when `path` is not one."""
        if not self.handles(path):
            return None
        call = Call(caller, method, path, params, body)
        if planned := self.failures.get(f"{method} {path}"):
            status, code = planned.pop(0)
            return status, {"detail": code}
        for wanted, pattern, handler in ROUTES:
            match = pattern.fullmatch(path)
            if match and wanted == method:
                return handler(self, call, match)
        return self.refuse_unexpected(call, "no route")

    def refuse_unexpected(self, call: Call, why: str) -> Answer:
        self.unexpected.append(f"{call.method} {call.path}?{call.params} {call.body!r}: {why}")
        return 500, {"detail": "unexpected synthetic call"}

    # ---- Steps ----

    def step(self, task_id: str) -> Step | None:
        return next((step for step in self.steps if step.task_id == task_id), None)

    def touch(self, task_id: str, **changes: Any) -> Step:
        """Someone else changed the step: its version moves on (and any field in `changes`)."""
        step = self.step(task_id)
        assert step, task_id
        for name, value in changes.items():
            setattr(step, name, value)
        step.version += 1
        return step

    def is_assignee(self, step: Step, caller: str) -> bool:
        return step.assignee is not None and step.assignee.user_id == caller

    def is_approver(self, step: Step, caller: str) -> bool:
        if step.approval_by == "hr":
            return caller in self.hr_team and not self.is_assignee(step, caller)
        return step.approver == caller

    def row(self, step: Step, caller: str) -> dict[str, Any] | None:
        """The `MyTask` `caller` gets for `step`, or None when it is not on their list."""
        hr = caller in self.hr_team
        mine = self.is_assignee(step, caller)
        pool = step.assignee is None
        approving = step.status == "awaiting_approval" and self.is_approver(step, caller)
        if not (mine or approving or (hr and (pool or step.on_behalf))):
            return None
        # A row the caller only approves keeps the assignee's text to the assignee (and HR).
        withheld = approving and not mine and not hr
        hidden = lambda value: None if withheld else value  # noqa: E731
        return {
            "task_id": step.task_id,
            "process_id": step.process_id,
            "process_kind": step.process_kind,
            "process_name": step.process_name,
            "employee_name": step.about.name,
            "ordinal": step.ordinal,
            "title_en": step.title_en,
            "title_ms": step.title_ms,
            "description_en": hidden(step.description_en),
            "description_ms": hidden(step.description_ms),
            "owner_role": step.owner_role,
            "due_on": step.due_on,
            "required": step.required,
            "approval_by": step.approval_by,
            "auto_check": step.auto_check,
            "reference_note": hidden(step.reference_note),
            "status": step.status,
            "note": hidden(step.note),
            "version": step.version,
            "overdue": step.status in OPEN
            and step.due_on is not None
            and step.due_on < kuala_lumpur_today().isoformat(),
            "pool": pool,
            "needs_my_approval": approving,
            "sent_back_reason": hidden(step.sent_back_reason),
            "mine": mine,
            "assignee_name": step.assignee.name if hr and step.assignee and not mine else None,
            "done_by_hr": hidden(step.done_by_hr),
        }

    # ---- Processes ----

    def process(self, process_id: str) -> Process | None:
        return next((item for item in self.processes if item.process_id == process_id), None)

    def steps_of(self, process_id: str) -> list[Step]:
        return sorted(
            (step for step in self.steps if step.process_id == process_id),
            key=lambda step: step.ordinal,
        )

    def log(
        self,
        process: Process,
        action: str,
        actor: str | None,
        task_id: str | None = None,
        **detail: Any,
    ) -> None:
        """Add an event to `process`'s history (the HR view of the detail returns them)."""
        count = sum(len(item.events) for item in self.processes) + 1
        process.events.append(
            {
                "event_id": f"99999999-9999-4999-8999-{count:012d}",
                "at": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"),
                "actor": actor,
                "action": action,
                "task_id": task_id,
                "detail": detail,
            }
        )

    def end(
        self,
        process: Process,
        status: str,
        actor: str,
        action: str,
        skipped: str,
        **detail: Any,
    ) -> None:
        """Cancel or close `process`: each open step is skipped (`task.skipped` with the reason
        `skipped`), then the process event `action` (0632 `hr_process_cancel`, 0634 close)."""
        process.status = status
        process.closed_at = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")
        for step in self.steps_of(process.process_id):
            if step.status in OPEN:
                step.status = "skipped"
                step.version += 1
                self.log(process, "task.skipped", actor, step.task_id, reason=skipped)
        self.log(process, action, actor, **detail)

    def process_row(self, process: Process) -> dict[str, Any]:
        steps = self.steps_of(process.process_id)
        today = kuala_lumpur_today().isoformat()
        open_dates = sorted(s.due_on for s in steps if s.status in OPEN and s.due_on is not None)
        return {
            "process_id": process.process_id,
            "employee_id": process.about.employee_id,
            "employee_name": process.about.name,
            "department_id": process.department_id,
            "kind": process.kind,
            "name": process.name,
            "status": process.status,
            "started_at": process.started_at,
            "closed_at": process.closed_at,
            "tasks_total": len(steps),
            "tasks_done": sum(s.status in ("done", "skipped") for s in steps),
            "next_due_on": open_dates[0] if open_dates else None,
            "overdue": bool(open_dates) and open_dates[0] < today,
        }

    def task_out(self, step: Step, caller: str) -> dict[str, Any]:
        """A `ProcessTask` as the HR view of the detail returns it: every field."""
        return {
            "task_id": step.task_id,
            "ordinal": step.ordinal,
            "title_en": step.title_en,
            "title_ms": step.title_ms,
            "owner_role": step.owner_role,
            "assignee_name": step.assignee.name if step.assignee else None,
            "due_on": step.due_on,
            "status": step.status,
            "required": step.required,
            "overdue": step.status in OPEN
            and step.due_on is not None
            and step.due_on < kuala_lumpur_today().isoformat(),
            "mine": self.is_assignee(step, caller),
            "description_en": step.description_en,
            "description_ms": step.description_ms,
            "anchor": ANCHOR[step.process_kind],
            "approval_by": step.approval_by,
            "auto_check": step.auto_check,
            "reference_note": step.reference_note,
            "completed_at": step.completed_at,
            "note": step.note,
            "version": step.version,
            "sent_back_reason": step.sent_back_reason,
            "done_by_hr": step.done_by_hr,
            "assignee_employee_id": step.assignee.employee_id if step.assignee else None,
            "assignee_overridden": step.assignee_overridden,
            "due_offset_days": None,
            "due_overridden": step.due_overridden,
            "completed_by": step.completed_by,
            "approved_by": None,
            "approved_at": None,
        }

    # ---- Templates ----

    def template(self, template_id: str) -> Template | None:
        return next((t for t in self.templates if t.template_id == template_id), None)

    def ties(self, kind: str, rules: dict[str, Any]) -> list[Template]:
        """0636 `hr_template_ties`: the active templates of the kind (onboarding or offboarding)
        with as many set rules and no rule set on both sides with different values; oldest
        first. The template being edited is among them when its own rules are given."""
        if kind not in ("onboarding", "offboarding"):
            return []
        mine = _tie_rules(rules)
        count = sum(value is not None for value in mine)
        found = []
        for other in self.templates:
            theirs = _tie_rules(other.rules)
            if (
                other.kind == kind
                and other.is_active
                and sum(value is not None for value in theirs) == count
                and all(a is None or b is None or a == b for a, b in zip(mine, theirs, strict=True))
            ):
                found.append(other)
        return sorted(found, key=lambda t: (t.created_at, t.template_id))

    def touch_template(self, template_id: str, **changes: Any) -> Template:
        """Someone else saved the template: its version moves on (and any field in `changes`)."""
        template = self.template(template_id)
        assert template, template_id
        for name, value in changes.items():
            setattr(template, name, value)
        template.version += 1
        return template

    # ---- Seed data ----

    def seed_company(
        self, *, hr: Member, manager: Member, staff: Member, read_only: Member
    ) -> None:
        """Lina's onboarding (her own steps, two waiting for the manager, one in the HR pool)
        and Hana's offboarding (a read-only member: HR sees her step on her behalf)."""
        today = kuala_lumpur_today()
        day = lambda offset: (today + timedelta(days=offset)).isoformat()  # noqa: E731
        self.hr_team = {hr.user_id}
        self.members = {m.employee_id: m for m in (hr, manager, staff, read_only)}
        self.departments = {staff.employee_id: DEPARTMENT["warehouse"]}
        self.departments[manager.employee_id] = DEPARTMENT["warehouse"]
        self.departments[hr.employee_id] = DEPARTMENT["operations"]
        self.templates = [
            Template(
                TEMPLATE["warehouse"],
                "onboarding",
                "Warehouse onboarding",
                version=2,
                steps=("Sign the employment contract", "Collect your building badge"),
            ),
            Template(
                TEMPLATE["leaving"],
                "offboarding",
                "Standard offboarding",
                steps=("Return the laptop",),
            ),
            Template(
                TEMPLATE["transfer"],
                "custom",
                "Transfer checklist",
                steps=("Hand over open orders", "Update the duty roster"),
            ),
            Template(TEMPLATE["old"], "custom", "Old transfer list", is_active=False),
        ]
        started = lambda offset: (  # noqa: E731
            datetime.combine(today + timedelta(days=offset), datetime.min.time())
            .replace(hour=2, tzinfo=ZoneInfo("UTC"))
            .isoformat()
        )
        self.processes = [
            Process(
                process_id=PROCESS["onboarding"],
                kind="onboarding",
                name="Warehouse onboarding",
                about=staff,
                started_at=started(-5),
                department_id=DEPARTMENT["warehouse"],
                template_id=TEMPLATE["warehouse"],
                template_version=2,
                anchor_dates={"start_date": day(-5), "last_day": None, "probation_end": day(85)},
            ),
            Process(
                process_id=PROCESS["offboarding"],
                kind="offboarding",
                name=None,
                about=read_only,
                started_at=started(-2),
                anchor_dates={
                    "start_date": "2024-03-04",
                    "last_day": day(10),
                    "probation_end": None,
                },
                started_by=hr.user_id,
            ),
        ]
        onboarding: dict[str, Any] = {
            "process_id": PROCESS["onboarding"],
            "process_kind": "onboarding",
            "process_name": "Warehouse onboarding",
            "about": staff,
        }
        approved_by_manager: dict[str, Any] = {
            "approval_by": "manager",
            "approver": manager.user_id,
        }
        self.steps = [
            Step(
                task_id=TASK["sign"],
                ordinal=1,
                title_en="Sign the employment contract",
                title_ms="Tandatangan kontrak pekerjaan",
                description_en="Read the contract and sign the last page.",
                reference_note="Contract template: HR shared folder\nAsk HR for the signing slot",
                owner_role="employee",
                assignee=staff,
                due_on=day(2),
                **onboarding,
                **approved_by_manager,
            ),
            Step(
                task_id=TASK["details"],
                ordinal=2,
                title_en="Fill in your personal details",
                owner_role="employee",
                assignee=staff,
                due_on=day(0),
                auto_check="personal_details_complete",
                **onboarding,
            ),
            Step(
                task_id=TASK["handbook"],
                ordinal=3,
                title_en="Read the safety handbook",
                owner_role="employee",
                assignee=staff,
                due_on=day(-3),
                version=3,
                note="Read chapters 1 to 3.",
                sent_back_reason="Please sign the last page too.",
                **onboarding,
                **approved_by_manager,
            ),
            Step(
                task_id=TASK["badge"],
                ordinal=4,
                title_en="Collect your building badge",
                owner_role="employee",
                assignee=staff,
                due_on=day(5),
                version=2,
                status="awaiting_approval",
                done_by_hr=True,
                completed_by=hr.user_id,
                description_en="The badge desk is at the main entrance.",
                **onboarding,
                **approved_by_manager,
            ),
            Step(
                task_id=TASK["kit"],
                ordinal=5,
                title_en="Pick up the safety kit",
                owner_role="employee",
                assignee=staff,
                due_on=day(6),
                version=2,
                status="awaiting_approval",
                completed_by=staff.user_id,
                description_en="Boots, gloves and a vest from the store room.",
                reference_note="Store room key: front desk",
                note="Size M boots.",
                **onboarding,
                **approved_by_manager,
            ),
            Step(
                task_id=TASK["payroll"],
                ordinal=6,
                title_en="Set up the payroll record",
                owner_role="hr",
                assignee=None,
                due_on=day(7),
                **onboarding,
            ),
            Step(
                task_id=TASK["laptop"],
                process_id=PROCESS["offboarding"],
                process_kind="offboarding",
                process_name=None,
                about=read_only,
                ordinal=1,
                title_en="Return the laptop",
                title_ms="Pulangkan komputer riba",
                owner_role="employee",
                assignee=read_only,
                due_on=None,
                on_behalf=True,
            ),
        ]


# Synthetic ids (UUIDs: the console checks an id before it reaches a URL).
PROCESS = {
    "onboarding": "88888888-8888-4888-8888-000000000001",
    "offboarding": "88888888-8888-4888-8888-000000000002",
}
TEMPLATE = {
    name: f"66666666-6666-4666-8666-{index:012d}"
    for index, name in enumerate(("warehouse", "leaving", "transfer", "old"), start=101)
}
DEPARTMENT = {
    "warehouse": "33333333-3333-4333-8333-000000000001",
    "operations": "33333333-3333-4333-8333-000000000002",
}
TASK = {
    name: f"77777777-7777-4777-8777-{index:012d}"
    for index, name in enumerate(
        ("sign", "details", "handbook", "badge", "kit", "payroll", "laptop"), start=1
    )
}


# ---- Routes: My tasks ----


def _sort_key(row: dict[str, Any]) -> tuple[bool, str, int, str]:
    return (row["due_on"] is None, row["due_on"] or "", row["ordinal"], row["task_id"])


@route("GET", r"hr/tasks/mine")
def my_tasks(world: ProcessWorld, call: Call, _: re.Match[str]) -> Answer:
    params = call.params
    extra = set(params) - {"client_id", "status", "limit", "after"}
    status = params.get("status")
    limit = params.get("limit", str(world.page_size))
    if extra or (status is not None and status not in TASK_STATUSES) or not limit.isdigit():
        world.refuse_unexpected(call, "bad query")
        return 422, {"detail": "invalid_hr_query"}
    size = int(limit)
    if not 1 <= size <= 200:
        world.refuse_unexpected(call, "bad limit")
        return 422, {"detail": "invalid_hr_query"}
    rows = [row for step in world.steps if (row := world.row(step, call.caller))]
    rows = [row for row in rows if (row["status"] == status if status else row["status"] in OPEN)]
    rows.sort(key=_sort_key)
    if after := params.get("after"):
        at = next((i for i, row in enumerate(rows) if row["task_id"] == after), None)
        if at is None:
            world.refuse_unexpected(call, "unknown cursor")
            return 422, {"detail": "invalid_hr_query"}
        rows = rows[at + 1 :]
    page = rows[:size]
    more = len(rows) > size
    return 200, {"items": page, "next_cursor": page[-1]["task_id"] if more else None}


# The fields each task write takes (`True` = required), as the gateway's models declare them.
TASK_BODIES: dict[str, dict[str, bool]] = {
    "complete": {"expected_version": True, "note": False},
    "approve": {"expected_version": True},
    "send-back": {"expected_version": True, "reason": True},
    "note": {"note": True},
    # The HR team only.
    "skip": {"expected_version": True, "reason": True},
    "reschedule": {"expected_version": True, "due_on": True},
    "reassign": {"expected_version": True, "assignee_employee_id": True},
}
HR_ONLY = ("skip", "reschedule", "reassign")


def _body_problem(action: str, body: Any) -> str | None:
    fields = TASK_BODIES[action]
    if not isinstance(body, dict):
        return "body is not an object"
    if unknown := set(body) - set(fields):
        return f"unknown fields {sorted(unknown)}"
    if missing := {name for name, required in fields.items() if required} - set(body):
        return f"missing fields {sorted(missing)}"
    if "expected_version" in body and not (
        isinstance(body["expected_version"], int) and body["expected_version"] >= 1
    ):
        return "expected_version is not a version"
    if "note" in body and not (body["note"] is None or isinstance(body["note"], str)):
        return "note is not text"
    if "reason" in body and not isinstance(body["reason"], str):
        return "reason is not text"
    if "due_on" in body and not isinstance(body["due_on"], str):
        return "due_on is not text"
    if "assignee_employee_id" in body and not (
        body["assignee_employee_id"] is None or isinstance(body["assignee_employee_id"], str)
    ):
        return "assignee_employee_id is not an id"
    return None


def _is_date(text: str) -> bool:
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def _receipt(step: Step, action: str) -> dict[str, Any]:
    return {
        "operation": f"task.{action.replace('-', '_')}",
        "task_id": step.task_id,
        "process_id": step.process_id,
        "task_status": step.status,
        "version": step.version,
        "process_status": "active",
        "status": "saved",
        "duplicate": False,
    }


@route("POST", r"hr/tasks/(?P<task_id>[0-9a-f-]{36})/(?P<action>[a-z-]+)")
def task_action(world: ProcessWorld, call: Call, match: re.Match[str]) -> Answer:
    task_id, action = match["task_id"], match["action"]
    body = call.body
    world.writes.append({"caller": call.caller, "task_id": task_id, "action": action, "body": body})
    if action not in TASK_BODIES:
        return world.refuse_unexpected(call, f"no stub for task action {action}")
    if problem := _body_problem(action, body):
        world.refuse_unexpected(call, problem)
        return 422, {"detail": "invalid_hr_input"}
    step = world.step(task_id)
    hr = call.caller in world.hr_team
    # The HR team may act on any step; anyone else only on a step of their own list.
    if step is None or (world.row(step, call.caller) is None and not hr):
        return 404, {"detail": "resource_unavailable"}
    if action in HR_ONLY and not hr:
        return 404, {"detail": "resource_unavailable"}
    process = world.process(step.process_id)
    if action == "note":
        if len(body["note"] or "") > NOTE_MAX:
            return 422, {"detail": "task_note_invalid"}
        if not (world.is_assignee(step, call.caller) or hr):
            return 409, {"detail": "task_not_assignee"}
        step.note = body["note"] or None
        step.version += 1
        return 200, _receipt(step, action)
    if step.status in ("done", "skipped") or (process and process.status != "active"):
        return 409, {"detail": "task_closed"}
    if body["expected_version"] != step.version:
        return 409, {"detail": "task_changed"}
    if action in HR_ONLY:
        return _hr_action(world, call, step, process, action, body)
    if action == "complete":
        if not (world.is_assignee(step, call.caller) or hr):
            return 409, {"detail": "task_not_assignee"}
        if step.status == "awaiting_approval":
            return 409, {"detail": "task_awaiting_approval"}
        if step.auto_check == "access_removed":
            return 409, {"detail": "task_system_only"}
        if step.auto_check and not step.check_met:
            return 409, {"detail": "check_not_met"}
        note = body.get("note")
        if note and len(note) > NOTE_MAX:
            return 422, {"detail": "task_note_invalid"}
        if note:
            step.note = note
        step.status = "awaiting_approval" if step.approval_by else "done"
        step.completed_by = call.caller
        step.done_by_hr = not world.is_assignee(step, call.caller)
        step.sent_back_reason = None
    else:  # approve, send-back
        if step.status != "awaiting_approval":
            return 409, {"detail": "task_not_awaiting_approval"}
        if not world.is_approver(step, call.caller):
            return 409, {"detail": "task_not_approver"}
        if action == "approve":
            step.status = "done"
        else:
            reason = body["reason"].strip()
            if not 1 <= len(reason) <= REASON_MAX:
                return 422, {"detail": "task_reason_invalid"}
            step.status = "todo"
            step.sent_back_reason = reason
    step.version += 1
    return 200, _receipt(step, action)


def _hr_action(
    world: ProcessWorld,
    call: Call,
    step: Step,
    process: Process | None,
    action: str,
    body: dict[str, Any],
) -> Answer:
    """Skip (a reason), reschedule (a date) or reassign (a person or the HR pool) a step."""
    # The detail each action writes (0634 `hr_task_apply`): HR's words in `remark`, codes in
    # `reason`, employee ids in `from`/`to`, dates in `old`/`new`.
    detail: dict[str, Any]
    if action == "skip":
        reason = body["reason"].strip()
        if not 1 <= len(reason) <= REASON_MAX:
            return 422, {"detail": "task_reason_invalid"}
        step.status = "skipped"
        detail = {"reason": "skipped_by_hr", "remark": reason}
    elif action == "reschedule":
        if not _is_date(body["due_on"]):
            return 422, {"detail": "task_due_invalid"}
        detail = {"old": step.due_on, "new": body["due_on"]}
        step.due_on = body["due_on"]
        step.due_overridden = True
    else:
        wanted = body["assignee_employee_id"]
        member = None if wanted is None else world.members.get(wanted)
        if wanted is not None and member is None:
            return 409, {"detail": "task_assignee_unavailable"}
        detail = {
            "from": step.assignee.employee_id if step.assignee else None,
            "to": wanted,
            "reason": "reassigned_by_hr",
        }
        step.assignee = member
        step.assignee_overridden = True
    step.version += 1
    if process:
        world.log(
            process,
            {"skip": "task.skipped", "reschedule": "task.rescheduled"}.get(
                action, "task.reassigned"
            ),
            call.caller,
            step.task_id,
            **detail,
        )
    return 200, _receipt(step, action)


# ---- Routes: processes and templates (Task 4) ----

PROCESS_QUERY = {"client_id", "kind", "status", "overdue", "department", "owner", "limit", "after"}


def _bad_query(world: ProcessWorld, call: Call, why: str) -> Answer:
    world.refuse_unexpected(call, why)
    return 400, {"detail": "invalid_hr_query"}


@route("GET", r"hr/processes")
def process_list(world: ProcessWorld, call: Call, _: re.Match[str]) -> Answer:
    params = call.params
    kind, status, overdue = params.get("kind"), params.get("status"), params.get("overdue")
    limit = params.get("limit", str(world.page_size))
    if set(params) - PROCESS_QUERY:
        return _bad_query(world, call, "unknown filter")
    if (kind is not None and kind not in KINDS) or (
        status is not None and status not in PROCESS_STATUSES
    ):
        return _bad_query(world, call, "bad kind or status")
    if overdue not in (None, "true", "false") or not limit.isdigit() or not 1 <= int(limit) <= 200:
        return _bad_query(world, call, "bad overdue or limit")
    if call.caller not in world.hr_team:
        return 200, {"items": [], "next_cursor": None}
    owner = params.get("owner")
    chosen = [
        item
        for item in world.processes
        if (kind is None or item.kind == kind)
        and (status is None or item.status == status)
        and (params.get("department") is None or item.department_id == params["department"])
        and (
            owner is None
            or any(
                s.assignee and s.assignee.employee_id == owner
                for s in world.steps_of(item.process_id)
            )
        )
    ]
    rows = [world.process_row(item) for item in chosen]
    if overdue == "true":
        rows = [row for row in rows if row["overdue"]]
    # Newest first.
    rows.sort(key=lambda row: (row["started_at"], row["process_id"]), reverse=True)
    if after := params.get("after"):
        at = next((i for i, row in enumerate(rows) if row["process_id"] == after), None)
        if at is None:
            return _bad_query(world, call, "unknown cursor")
        rows = rows[at + 1 :]
    size = int(limit)
    page = rows[:size]
    more = len(rows) > size
    return 200, {"items": page, "next_cursor": page[-1]["process_id"] if more else None}


def _hr_process(world: ProcessWorld, call: Call, process_id: str) -> Process | None:
    """The process, for the HR team only (anyone else, like a missing one, is a 404)."""
    if call.caller not in world.hr_team:
        return None
    return world.process(process_id)


@route("GET", r"hr/processes/(?P<process_id>[0-9a-f-]{36})")
def process_detail(world: ProcessWorld, call: Call, match: re.Match[str]) -> Answer:
    if set(call.params) != {"client_id"}:
        return _bad_query(world, call, "a detail takes no filter")
    process = _hr_process(world, call, match["process_id"])
    if process is None:
        return 404, {"detail": "resource_unavailable"}
    row = world.process_row(process)
    steps = world.steps_of(process.process_id)
    # The HR view: no `required_total`, `required_done` or `open_tasks` (the self view's counts).
    header = {
        "process_id": process.process_id,
        "kind": process.kind,
        "name": process.name,
        "status": process.status,
        "employee_name": process.about.name,
        "started_at": process.started_at,
        "closed_at": process.closed_at,
        "employee_id": process.about.employee_id,
        "tasks_total": row["tasks_total"],
        "tasks_done": row["tasks_done"],
        "template_id": process.template_id,
        "template_version": process.template_version,
        "anchor_dates": {"start_date": None, "last_day": None, "probation_end": None}
        | process.anchor_dates,
        "started_by": process.started_by,
    }
    return 200, {
        "view": "hr",
        "process": header,
        "tasks": [world.task_out(step, call.caller) for step in steps],
        "events": list(process.events),
    }


START_FIELDS = {"employee_id", "kind", "template_id", "replace"}


@route("POST", r"hr/processes")
def process_start(world: ProcessWorld, call: Call, _: re.Match[str]) -> Answer:
    body = call.body
    if call.caller not in world.hr_team:
        return 404, {"detail": "resource_unavailable"}
    if (
        not isinstance(body, dict)
        or set(body) - START_FIELDS
        or not {"employee_id", "kind"} <= set(body)
        or ("replace" in body and not isinstance(body["replace"], bool))
    ):
        world.refuse_unexpected(call, "bad start body")
        return 422, {"detail": "invalid_hr_input"}
    kind = body["kind"]
    if kind not in KINDS:
        return 422, {"detail": "process_kind_invalid"}
    person = world.members.get(body["employee_id"])
    if person is None:
        return 404, {"detail": "resource_unavailable"}
    template_id = body.get("template_id")
    active = [t for t in world.templates if t.kind == kind and t.is_active]
    if template_id is not None:
        template = next((t for t in active if t.template_id == template_id), None)
        if template is None:
            return 409, {"detail": "template_unavailable"}
    elif kind == "custom":
        return 422, {"detail": "process_template_required"}
    elif active:
        template = active[0]
    else:
        return 409, {"detail": "process_no_template"}
    if kind != "custom":
        running = next(
            (
                item
                for item in world.processes
                if item.about == person and item.kind == kind and item.status == "active"
            ),
            None,
        )
        if running and body.get("replace") is not True:
            return 409, {"detail": "process_already_active"}
        if running:
            world.end(
                running,
                "cancelled",
                call.caller,
                "process.cancelled",
                "process_cancelled",
                reason="superseded",
            )
    number = len(world.processes) + 1
    process = Process(
        process_id=f"88888888-8888-4888-8888-{number:012d}",
        kind=kind,
        name=template.name,
        about=person,
        started_at=datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"),
        department_id=world.departments.get(person.employee_id),
        template_id=template.template_id,
        template_version=template.version,
        started_by=call.caller,
    )
    world.processes.append(process)
    world.log(
        process,
        "process.started",
        call.caller,
        kind=kind,
        template_id=template.template_id,
        template_version=template.version,
    )
    for ordinal, title in enumerate(template.steps, start=1):
        world.steps.append(
            Step(
                task_id=f"77777777-7777-4777-8777-{1000 + len(world.steps):012d}",
                process_id=process.process_id,
                process_kind=kind,
                process_name=template.name,
                about=person,
                ordinal=ordinal,
                title_en=title,
                owner_role="hr",
                due_on=None,
            )
        )
        world.log(
            process,
            "task.created",
            call.caller,
            world.steps[-1].task_id,
            owner_role="hr",
            assignee_employee_id=None,
            due_on=None,
        )
    return 201, {
        "operation": "process.start",
        "process_id": process.process_id,
        "employee_id": person.employee_id,
        "kind": kind,
        "template_id": template.template_id,
        "status": "saved",
        "duplicate": False,
    }


@route("POST", r"hr/processes/(?P<process_id>[0-9a-f-]{36})/(?P<action>cancel|close)")
def process_end(world: ProcessWorld, call: Call, match: re.Match[str]) -> Answer:
    action, body = match["action"], call.body
    if action == "cancel" and body is not None:
        world.refuse_unexpected(call, "cancel takes no body")
        return 422, {"detail": "invalid_hr_input"}
    if action == "close" and not (
        isinstance(body, dict) and set(body) == {"note"} and isinstance(body["note"], str)
    ):
        world.refuse_unexpected(call, "bad close body")
        return 422, {"detail": "invalid_hr_input"}
    process = _hr_process(world, call, match["process_id"])
    if process is None:
        return 404, {"detail": "resource_unavailable"}
    if process.status != "active":
        return 409, {"detail": "process_closed"}
    if action == "cancel":
        world.end(
            process,
            "cancelled",
            call.caller,
            "process.cancelled",
            "process_cancelled",
            reason="cancelled_by_hr",
        )
        receipt = {"operation": "process.cancel"}
    else:
        assert isinstance(body, dict)
        note = body["note"].strip()
        if not 1 <= len(note) <= REASON_MAX:
            return 422, {"detail": "invalid_hr_input"}
        world.end(
            process,
            "completed",
            call.caller,
            "process.completed",
            "process_closed_anyway",
            reason="closed_anyway",
            remark=note,
        )
        receipt = {"operation": "process.close_anyway", "process_status": "completed"}
    return 200, receipt | {"process_id": process.process_id, "status": "saved", "duplicate": False}


@route("GET", r"hr/templates")
def template_list(world: ProcessWorld, call: Call, _: re.Match[str]) -> Answer:
    if set(call.params) != {"client_id"}:
        return _bad_query(world, call, "the template list takes no filter")
    if call.caller not in world.hr_team:
        return 404, {"detail": "resource_unavailable"}
    items = sorted(world.templates, key=lambda t: (t.kind, t.name))
    return 200, {"items": [t.summary() for t in items]}


# ---- Routes: the template editor (Task 5) ----

TEMPLATE_STEP_FIELDS = {
    "title_en",
    "title_ms",
    "description_en",
    "description_ms",
    "owner_role",
    "owner_employee_id",
    "anchor",
    "due_offset_days",
    "required",
    "approval_by",
    "auto_check",
    "reference_note",
}
OWNER_ROLES = ("hr", "manager", "employee", "person")
AUTO_CHECKS = (
    "account_linked",
    "personal_details_complete",
    "access_removed",
    "document_uploaded:contract",
    "document_uploaded:identity",
    "document_uploaded:certificate",
    "document_uploaded:other",
)


def _tie_rules(rules: dict[str, Any] | None) -> tuple[str | None, ...]:
    """The four rules as the tie rule compares them: trimmed, empty is none, entity lowered."""
    values = []
    for key in RULE_KEYS:
        value = (rules or {}).get(key)
        text = value.strip() if isinstance(value, str) and value.strip() else None
        values.append(text.lower() if text and key == "legal_entity_reference" else text)
    return tuple(values)


def _text(value: Any, limit: int, *, required: bool = False) -> bool:
    if value is None:
        return not required
    return isinstance(value, str) and (not required or bool(value.strip())) and len(value) <= limit


def _rules_problem(rules: Any) -> str | None:
    if rules is None:
        return None
    if not isinstance(rules, dict) or not set(rules) <= set(RULE_KEYS):
        return "rules shape"
    if rules.get("department_id") not in (None, *DEPARTMENT.values()):
        return "unknown department"
    if rules.get("employment_type") not in (None, "full_time", "part_time", "contract", "intern"):
        return "employment type"
    if rules.get("region") not in (None, "peninsular_labuan", "sabah", "sarawak"):
        return "region"
    if not _text(rules.get("legal_entity_reference"), 200):
        return "legal entity"
    return None


def _step_problem(world: ProcessWorld, kind: str, step: Any) -> str | None:
    """Why a template step is one the console must never send (its own checks catch these)."""
    if not isinstance(step, dict) or not set(step) <= TEMPLATE_STEP_FIELDS:
        return "step shape"
    checks = {
        "title_en": _text(step.get("title_en"), 160, required=True),
        "title_ms": _text(step.get("title_ms"), 160),
        "description_en": _text(step.get("description_en"), 2000),
        "description_ms": _text(step.get("description_ms"), 2000),
        "reference_note": _text(step.get("reference_note"), 500),
        "owner_role": step.get("owner_role") in OWNER_ROLES,
        "anchor": step.get("anchor") in ANCHORS[kind],
        "due_offset_days": type(step.get("due_offset_days")) is int
        and -365 <= step["due_offset_days"] <= 365,
        "required": type(step.get("required", True)) is bool,
        "approval_by": step.get("approval_by") in (None, "hr", "manager"),
        "auto_check": step.get("auto_check") in (None, *AUTO_CHECKS),
        "owner_employee_id": (step.get("owner_employee_id") in world.members)
        if step.get("owner_role") == "person"
        else step.get("owner_employee_id") is None,
    }
    bad = [name for name, ok in checks.items() if not ok]
    return f"step fields {bad}" if bad else None


def _template_problem(world: ProcessWorld, kind: str, body: dict[str, Any]) -> str | None:
    name = body.get("name")
    if not (isinstance(name, str) and name.strip() == name and 1 <= len(name) <= 120):
        return "name"
    if problem := _rules_problem(body.get("rules")):
        return problem
    steps = body.get("steps")
    if not (isinstance(steps, list) and 1 <= len(steps) <= 40):
        return "step count"
    for step in steps:
        if problem := _step_problem(world, kind, step):
            return problem
    return None


def _template_receipt(world: ProcessWorld, template: Template, operation: str) -> dict[str, Any]:
    """A save's receipt: the other templates it may tie with, and a warning for each step of
    the leaver due after the last day (0631 `step_after_last_day`)."""
    warnings = [
        {"code": "step_after_last_day", "ordinal": ordinal}
        for ordinal, step in enumerate(template.step_rows(), start=1)
        if template.kind == "offboarding"
        and step["owner_role"] == "employee"
        and step["anchor"] == "last_day"
        and step["due_offset_days"] > 0
    ]
    return {
        "operation": operation,
        "template_id": template.template_id,
        "version": template.version,
        "conflicts": _conflicts(world, template),
        "warnings": warnings,
        "status": "saved",
        "duplicate": False,
    }


def _conflicts(world: ProcessWorld, template: Template) -> list[str]:
    if not template.is_active:
        return []
    return [
        other.template_id
        for other in world.ties(template.kind, template.rules)
        if other.template_id != template.template_id
    ]


def _apply(template: Template, body: dict[str, Any]) -> None:
    template.name = body["name"]
    template.rules = {k: v for k, v in (body["rules"] or {}).items() if v is not None}
    template.details = [
        {key: step.get(key) for key in TEMPLATE_STEP_FIELDS}
        | {"required": step.get("required", True)}
        for step in body["steps"]
    ]
    template.steps = tuple(step["title_en"] for step in body["steps"])


@route("GET", r"hr/templates/(?P<template_id>[0-9a-f-]{36})")
def template_detail(world: ProcessWorld, call: Call, match: re.Match[str]) -> Answer:
    template = world.template(match["template_id"])
    if call.caller not in world.hr_team or template is None:
        return 404, {"detail": "resource_unavailable"}
    return 200, {
        "template_id": template.template_id,
        "kind": template.kind,
        "name": template.name,
        "version": template.version,
        "is_active": template.is_active,
        "created_at": template.created_at,
        "updated_at": template.created_at,
        "rules": template.rule_fields(),
        "steps": [
            {"ordinal": ordinal} | step
            for ordinal, step in enumerate(template.step_rows(), start=1)
        ],
        "conflicts": _conflicts(world, template),
        "warnings": [],
    }


@route("POST", r"hr/templates")
def template_create(world: ProcessWorld, call: Call, _: re.Match[str]) -> Answer:
    body = call.body
    if call.caller not in world.hr_team:
        return 404, {"detail": "resource_unavailable"}
    if not (
        isinstance(body, dict)
        and set(body) == {"kind", "name", "rules", "steps"}
        and body["kind"] in KINDS
    ):
        world.refuse_unexpected(call, "bad template create body")
        return 422, {"detail": "invalid_hr_input"}
    if problem := _template_problem(world, body["kind"], body):
        world.refuse_unexpected(call, problem)
        return 422, {"detail": "invalid_hr_input"}
    template = Template(
        f"66666666-6666-4666-8666-{201 + len(world.templates):012d}",
        body["kind"],
        body["name"],
        created_at=f"2026-11-{10 + len(world.templates):02d}T02:00:00Z",
    )
    _apply(template, body)
    world.templates.append(template)
    return 201, _template_receipt(world, template, "template.create")


@route("PUT", r"hr/templates/(?P<template_id>[0-9a-f-]{36})")
def template_update(world: ProcessWorld, call: Call, match: re.Match[str]) -> Answer:
    body = call.body
    template = world.template(match["template_id"])
    if call.caller not in world.hr_team or template is None:
        return 404, {"detail": "resource_unavailable"}
    wanted = {"expected_version", "name", "rules", "steps"}
    if not (isinstance(body, dict) and wanted <= set(body) <= wanted | {"is_active"}):
        # `rules` has no default on purpose: the API answers 422 without it.
        world.refuse_unexpected(call, "bad template update body")
        return 422, {"detail": "invalid_hr_input"}
    if problem := _template_problem(world, template.kind, body):
        world.refuse_unexpected(call, problem)
        return 422, {"detail": "invalid_hr_input"}
    if body["expected_version"] != template.version:
        return 409, {"detail": "template_changed"}
    _apply(template, body)
    if body.get("is_active") is not None:
        template.is_active = body["is_active"]
    template.version += 1
    return 200, _template_receipt(world, template, "template.update")


@route("POST", r"hr/templates/(?P<template_id>[0-9a-f-]{36})/deactivate")
def template_deactivate(world: ProcessWorld, call: Call, match: re.Match[str]) -> Answer:
    body = call.body
    template = world.template(match["template_id"])
    if call.caller not in world.hr_team or template is None:
        return 404, {"detail": "resource_unavailable"}
    if not (isinstance(body, dict) and set(body) == {"expected_version"}):
        world.refuse_unexpected(call, "bad deactivate body")
        return 422, {"detail": "invalid_hr_input"}
    if body["expected_version"] != template.version:
        return 409, {"detail": "template_changed"}
    template.is_active = False
    template.version += 1
    return 200, _template_receipt(world, template, "template.deactivate")


@route("POST", r"hr/templates/preview")
def template_preview(world: ProcessWorld, call: Call, _: re.Match[str]) -> Answer:
    body = call.body
    if call.caller not in world.hr_team:
        return 404, {"detail": "resource_unavailable"}
    if not (
        isinstance(body, dict)
        and set(body) == {"kind", "rules"}
        and body["kind"] in KINDS
        and _rules_problem(body["rules"]) is None
    ):
        world.refuse_unexpected(call, "bad preview body")
        return 422, {"detail": "invalid_hr_input"}
    rules = _tie_rules(body["rules"])
    matching = [
        person
        for person in world.people
        if all(
            wanted is None or wanted == have
            for wanted, have in zip(rules, _tie_rules(person), strict=True)
        )
    ]
    ties = world.ties(body["kind"], body["rules"] or {})
    return 200, {
        "matching": len(matching),
        "ties": [{"template_id": t.template_id, "name": t.name} for t in ties],
        "total_active": len(world.people),
    }
