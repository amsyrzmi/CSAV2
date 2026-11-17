"""Synthetic HR process routes for the console browser tests (ruling R1 of the console plan).

`ProcessWorld` holds the steps of a synthetic company's onboarding and offboarding processes and
answers the browser-side process routes the way the gateway does (docs/CONSOLE_API.md, "HR
processes"). `RecordsWorld.answer` in tests/test_hr_access_browser.py hands it every call for this
tenant first: `answer` returns `None` for a path that is not a process path (the old routes then
answer it), and records any process path it has no route for as unexpected, so a stray call still
fails the test.

Extension point: `ROUTES`. Tasks 4-6 add the processes list and detail, the templates and a
person's processes by registering a handler with `@route("GET", r"hr/processes")`; the handler
gets the world, the `Call` and the path match and returns `(status, body)`. A new path family
whose prefix is not in `PREFIXES` is still delegated as long as a registered pattern matches it.

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

    def handles(self, path: str) -> bool:
        return path.startswith(PREFIXES) or any(p.fullmatch(path) for _, p, _ in ROUTES)

    def answer(
        self, caller: str, method: str, path: str, params: dict[str, str], body: Any
    ) -> Answer | None:
        """The answer for a process path, or None when `path` is not one."""
        if not self.handles(path):
            return None
        call = Call(caller, method, path, params, body)
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

    # ---- Seed data ----

    def seed_company(
        self, *, hr: Member, manager: Member, staff: Member, read_only: Member
    ) -> None:
        """Lina's onboarding (her own steps, two waiting for the manager, one in the HR pool)
        and Hana's offboarding (a read-only member: HR sees her step on her behalf)."""
        today = kuala_lumpur_today()
        day = lambda offset: (today + timedelta(days=offset)).isoformat()  # noqa: E731
        self.hr_team = {hr.user_id}
        onboarding: dict[str, Any] = {
            "process_id": "88888888-8888-4888-8888-000000000001",
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
                process_id="88888888-8888-4888-8888-000000000002",
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


# Synthetic task ids (UUIDs: the console checks an id before it reaches a URL).
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
}


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
    return None


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
    if step is None or world.row(step, call.caller) is None:
        return 404, {"detail": "resource_unavailable"}
    hr = call.caller in world.hr_team
    if action == "note":
        if len(body["note"] or "") > NOTE_MAX:
            return 422, {"detail": "task_note_invalid"}
        if not (world.is_assignee(step, call.caller) or hr):
            return 409, {"detail": "task_not_assignee"}
        step.note = body["note"] or None
        step.version += 1
        return 200, _receipt(step, action)
    if step.status in ("done", "skipped"):
        return 409, {"detail": "task_closed"}
    if body["expected_version"] != step.version:
        return 409, {"detail": "task_changed"}
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
