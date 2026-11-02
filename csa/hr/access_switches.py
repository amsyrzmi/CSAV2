"""The switch rules for the HR access page: pure, no database and no I/O.

The page shows a handful of plain switches. Each switch stands for a fixed set of saved rows
(grants, workflow grants and private areas). Everything the switches do not explain is "uncovered"
and is kept untouched on every save, so the page never drops an owner's decisions or an unusual
hand-made grant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, NamedTuple, Self, cast
from uuid import UUID

from csa.hr.automation_models import AccessSave, PrivateAccessSave, PrivateArea

SwitchId = Literal[
    "directory",
    "own_requests",
    "approves_team",
    "hr_team",
    "manage_people",
    "recruitment",
    "attendance",
    "payroll_prepare",
    "payroll_review",
]
PresetId = Literal["staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"]
WarningKind = Literal[
    "no_approver",
    "no_manager",
    "no_payroll_reviewer",
    "manager_without_team",
    "prepares_and_reviews",
]

SWITCH_IDS: tuple[SwitchId, ...] = (
    "directory",
    "own_requests",
    "approves_team",
    "hr_team",
    "manage_people",
    "recruitment",
    "attendance",
    "payroll_prepare",
    "payroll_review",
)
MAX_SWITCHES = 16  # the most switch ids one person's `on` (or `base`) list may carry
MAX_GRANTS = 32
MAX_WORKFLOWS = 48
BASIC_FIELDS = ("assigned_manager", "department", "display_name", "job_title", "work_email")


class GrantRow(NamedTuple):
    action: str
    scope: str
    fields: tuple[str, ...]


class WorkflowRow(NamedTuple):
    domain: str
    action: str
    scope: str


class AccessRows(NamedTuple):
    grants: frozenset[GrantRow]
    workflows: frozenset[WorkflowRow]
    areas: frozenset[str]

    def union(self, *others: AccessRows) -> AccessRows:
        grants, workflows, areas = set(self.grants), set(self.workflows), set(self.areas)
        for other in others:
            grants |= other.grants
            workflows |= other.workflows
            areas |= other.areas
        return AccessRows(frozenset(grants), frozenset(workflows), frozenset(areas))

    def minus(self, other: AccessRows) -> AccessRows:
        return AccessRows(
            self.grants - other.grants, self.workflows - other.workflows, self.areas - other.areas
        )

    def issubset(self, other: AccessRows) -> bool:
        return (
            self.grants <= other.grants
            and self.workflows <= other.workflows
            and self.areas <= other.areas
        )

    @classmethod
    def from_summary(
        cls,
        grants_json: Sequence[Mapping[str, Any]],
        workflows_json: Sequence[Mapping[str, Any]],
        areas_json: Sequence[str],
    ) -> Self:
        return cls(
            frozenset(_grant(g["action"], g["scope"], g.get("fields") or ()) for g in grants_json),
            frozenset(WorkflowRow(w["domain"], w["action"], w["scope"]) for w in workflows_json),
            frozenset(areas_json),
        )


def _grant(action: str, scope: str, fields: Sequence[str] = ()) -> GrantRow:
    return GrantRow(action, scope, tuple(sorted(fields)))


def _rows(
    grants: Sequence[GrantRow] = (),
    workflows: Sequence[WorkflowRow] = (),
    areas: Sequence[str] = (),
) -> AccessRows:
    return AccessRows(frozenset(grants), frozenset(workflows), frozenset(areas))


def _workflows(domains: Sequence[str], actions: Sequence[str], scope: str) -> list[WorkflowRow]:
    return [WorkflowRow(d, a, scope) for d in domains for a in actions]


EMPTY = _rows()

SWITCHES: Mapping[SwitchId, AccessRows] = {
    "directory": _rows([_grant("directory.read", "hr_operator", BASIC_FIELDS)]),
    "own_requests": _rows(
        [
            _grant("directory.read", "self", BASIC_FIELDS),
            *(
                _grant(a, "self")
                for a in ("training.read", "training.draft", "training.submit", "training.withdraw")
            ),
        ],
        _workflows(("attendance", "leave"), ("read", "prepare"), "self"),
    ),
    "approves_team": _rows(
        [
            _grant("directory.read", "assigned_manager", BASIC_FIELDS),
            _grant("training.read", "assigned_manager"),
            _grant("training.decide", "assigned_manager"),
        ],
        _workflows(("attendance", "leave"), ("read", "decide"), "assigned_manager"),
    ),
    "hr_team": _rows(
        [
            _grant("directory.read", "hr_operator", BASIC_FIELDS),
            _grant("training.read", "hr_operator"),
        ],
        [
            *_workflows(("recruitment",), ("read", "prepare"), "hr_operator"),
            *_workflows(("attendance", "leave"), ("read",), "hr_operator"),
        ],
    ),
    # Add and edit people and departments, and see Left people and employment profiles. It never
    # changes access: that stays with the HR owner.
    "manage_people": _rows([_grant("people.manage", "hr_operator")]),
    "recruitment": _rows(areas=["recruitment"]),
    "attendance": _rows(areas=["attendance"]),
    "payroll_prepare": _rows(
        workflows=_workflows(("payroll",), ("read", "prepare"), "payroll_operator"),
        areas=["payroll"],
    ),
    "payroll_review": _rows(
        workflows=_workflows(("payroll",), ("decide",), "payroll_reviewer"), areas=["payroll"]
    ),
}


def switch_set(*ids: SwitchId) -> frozenset[SwitchId]:
    return frozenset(ids)


_STAFF = switch_set("directory", "own_requests")

PRESETS: Mapping[PresetId, frozenset[SwitchId]] = {
    "staff": _STAFF,
    "manager": _STAFF | switch_set("approves_team"),
    "hr_team": _STAFF | switch_set("hr_team", "manage_people", "recruitment", "attendance"),
    "payroll_preparer": _STAFF | switch_set("payroll_prepare"),
    "payroll_reviewer": _STAFF | switch_set("payroll_review"),
}


class SwitchState(NamedTuple):
    on: frozenset[SwitchId]
    included: Mapping[SwitchId, SwitchId]
    uncovered: AccessRows


def state(current: AccessRows) -> SwitchState:
    on: frozenset[SwitchId] = frozenset(s for s in SWITCH_IDS if SWITCHES[s].issubset(current))
    included: dict[SwitchId, SwitchId] = {}
    for a in sorted(on):
        for b in sorted(on):
            if a != b and SWITCHES[a].issubset(SWITCHES[b]):
                included[a] = b
                break
    return SwitchState(on, included, current.minus(_cover(on)))


def _cover(on: frozenset[SwitchId]) -> AccessRows:
    return EMPTY.union(*(SWITCHES[s] for s in sorted(on)))


def rows(on: frozenset[SwitchId], uncovered: AccessRows) -> AccessRows:
    cover = _cover(on)
    # hr_grants is keyed by (action, scope): a switch row replaces a partial hand-made grant for the
    # same pair. The switch row has all five basic fields, so no access is lost.
    taken = {(g.action, g.scope) for g in cover.grants}
    kept = uncovered._replace(
        grants=frozenset(g for g in uncovered.grants if (g.action, g.scope) not in taken)
    )
    return cover.union(kept)


def apply_preset(on: frozenset[SwitchId], preset: PresetId) -> frozenset[SwitchId]:
    return on | PRESETS[preset]


class AccessLimitError(ValueError):
    """The saved rows would exceed what one access.save can carry."""


def access_save(user_id: UUID, after: AccessRows) -> AccessSave:
    """The access.save that leaves the person with exactly these grant and workflow rows."""
    if len(after.grants) > MAX_GRANTS or len(after.workflows) > MAX_WORKFLOWS:
        raise AccessLimitError("access_limit")
    return AccessSave.model_validate(
        {
            "operation": "access.save",
            "user_id": user_id,
            "grants": [
                {"action": g.action, "scope": g.scope, "fields": list(g.fields)}
                for g in sorted(after.grants)
            ],
            "workflows": [
                {"domain": w.domain, "action": w.action, "scope": w.scope}
                for w in sorted(after.workflows)
            ],
        }
    )


def operations(
    user_id: UUID, before: AccessRows, after: AccessRows
) -> list[AccessSave | PrivateAccessSave]:
    result: list[AccessSave | PrivateAccessSave] = []
    if before.grants != after.grants or before.workflows != after.workflows:
        result.append(access_save(user_id, after))
    for enabled, areas in ((True, after.areas - before.areas), (False, before.areas - after.areas)):
        for area in sorted(areas):
            result.append(
                PrivateAccessSave.model_validate(
                    {
                        "operation": "private_access.save",
                        "user_id": user_id,
                        "area": cast(PrivateArea, area),
                        "enabled": enabled,
                    }
                )
            )
    return result


def grant_switches(
    grants: frozenset[GrantRow], workflows: frozenset[WorkflowRow]
) -> frozenset[SwitchId]:
    """Switches whose grant and workflow rows are all present. Areas are ignored (no areas here)."""
    present = AccessRows(grants, workflows, frozenset())
    return frozenset(
        s
        for s, r in SWITCHES.items()
        if (r.grants or r.workflows)
        and AccessRows(r.grants, r.workflows, frozenset()).issubset(present)
    )


class PersonAccess(NamedTuple):
    user_id: UUID
    display_name: str
    rows: AccessRows
    employee_id: UUID | None
    department: str | None
    manager_id: UUID | None
    active: bool | None
    team_size: int
    is_owner: bool

    @classmethod
    def from_summary(cls, row: Mapping[str, Any]) -> Self:
        return cls(
            user_id=row["user_id"],
            display_name=row["display_name"],
            rows=AccessRows.from_summary(row["grants"], row["workflows"], row["areas"]),
            employee_id=row["employee_id"],
            department=row["department"],
            manager_id=row["manager_id"],
            active=row["active"],
            team_size=row["team_size"] or 0,
            is_owner=bool(row["is_owner"]),
        )


class ReportingLine(NamedTuple):
    employee_id: UUID
    display_name: str
    manager_employee_id: UUID | None
    manager_user_id: UUID | None


class AccessWarning(NamedTuple):
    kind: WarningKind
    user_id: UUID | None
    employee_id: UUID | None


def warnings(people: Sequence[PersonAccess], lines: Sequence[ReportingLine]) -> list[AccessWarning]:
    on = {p.user_id: state(p.rows).on for p in people}
    found: list[AccessWarning] = []
    for line in lines:
        if line.manager_employee_id is None:
            found.append(AccessWarning("no_manager", None, line.employee_id))
        elif line.manager_user_id is None or "approves_team" not in on.get(
            line.manager_user_id, frozenset()
        ):
            found.append(AccessWarning("no_approver", None, line.employee_id))
    if not any("payroll_review" in s for s in on.values()):
        found.append(AccessWarning("no_payroll_reviewer", None, None))
    for p in people:
        if "approves_team" in on[p.user_id] and p.team_size == 0:
            found.append(AccessWarning("manager_without_team", p.user_id, p.employee_id))
        if {"payroll_prepare", "payroll_review"} <= on[p.user_id]:
            found.append(AccessWarning("prepares_and_reviews", p.user_id, p.employee_id))
    return sorted(found, key=lambda w: (w.kind, str(w.user_id or ""), str(w.employee_id or "")))


def stamp(people: Sequence[PersonAccess], lines: Sequence[ReportingLine]) -> str:
    """A digest of everything the page showed, so a stale save is refused. Ignores input order."""
    body = {
        "people": sorted(
            (
                {
                    "user_id": str(p.user_id),
                    "grants": sorted([g.action, g.scope, list(g.fields)] for g in p.rows.grants),
                    "workflows": sorted(list(w) for w in p.rows.workflows),
                    "areas": sorted(p.rows.areas),
                }
                for p in people
            ),
            key=lambda x: x["user_id"],
        ),
        "lines": sorted(
            [
                [
                    str(x) if x is not None else None
                    for x in (ln.employee_id, ln.manager_employee_id, ln.manager_user_id)
                ]
                + [ln.display_name]
                for ln in lines
            ],
            key=lambda x: json.dumps(x),
        ),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
