"""Pure people rules: status, search, filters, paging, org chart and history ordering. No I/O."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, get_args
from uuid import UUID
from zoneinfo import ZoneInfo

from csa.hr.people_contracts import (
    FieldChange,
    HistoryField,
    HrPersonOut,
    OrgChart,
    OrgNode,
    PeoplePage,
    PersonStatus,
    ProfileSection,
)
from csa.hr.people_types import EmploymentProfile
from csa.hr.policy import HrPolicyError
from csa.workspace.directory import search_words

Viewer = Literal["hr", "directory", "self"]
PAGE_SIZE = 50

_KUALA_LUMPUR = ZoneInfo("Asia/Kuala_Lumpur")
_CURSOR = re.compile(r"[0-9]{1,7}")
_HISTORY_ORDER: tuple[str, ...] = get_args(HistoryField)


def kl_today(now: datetime) -> date:
    """The calendar date in Malaysia at the aware moment ``now``."""
    return now.astimezone(_KUALA_LUMPUR).date()


def person_status(active: bool, start_date: date | None, today: date) -> PersonStatus:
    if not active:
        return "left"
    if start_date is not None and start_date > today:
        return "starting"
    return "active"


def person_from_row(row: Mapping[str, Any], today: date, names: Mapping[UUID, str]) -> HrPersonOut:
    """One `hr_people_rows` row as a person. The HR-only columns are absent for other viewers."""
    manager_id = row["manager_id"]
    profile = None
    if all(
        row.get(key) is not None
        for key in ("region", "legal_entity_reference", "employment_category")
    ):
        profile = EmploymentProfile(
            region=row["region"],
            legal_entity_reference=row["legal_entity_reference"],
            employment_category=row["employment_category"],
        )
    version = row.get("version")
    return HrPersonOut(
        employee_id=row["employee_id"],
        display_name=row["display_name"],
        job_title=row["job_title"],
        department_id=row["department_id"],
        department=row["department"],
        manager_id=manager_id,
        manager_name=names.get(manager_id) if manager_id is not None else None,
        work_email=row["work_email"],
        work_phone=row["work_phone"],
        start_date=row["start_date"],
        employment_type=row["employment_type"],
        status=person_status(row["active"], row["start_date"], today),
        employment_profile=profile,
        version=version,
        account_linked=row.get("account_user_id") is not None if version is not None else None,
    )


def _name_order(person: HrPersonOut) -> tuple[str, str]:
    return (person.display_name.casefold(), str(person.employee_id))


def search(people: Sequence[HrPersonOut], query: str) -> list[HrPersonOut]:
    """Every word, in any order: in the name or work email, or starting a word of the job title.
    People whose name or email holds every word come first."""
    words = search_words(query)
    if not words:
        return []
    title_starts = [re.compile(r"(?<!\w)(?=\w)" + re.escape(word), re.IGNORECASE) for word in words]
    folded = [word.casefold() for word in words]
    ranked: list[tuple[int, tuple[str, str], HrPersonOut]] = []
    for person in people:
        haystack = f"{person.display_name}\n{person.work_email or ''}".casefold()
        by_name = all(word in haystack for word in folded)
        title = person.job_title or ""
        if by_name or all(
            word in haystack or pattern.search(title)
            for word, pattern in zip(folded, title_starts, strict=True)
        ):
            ranked.append((0 if by_name else 1, _name_order(person), person))
    ranked.sort(key=lambda item: item[:2])
    return [person for _, _, person in ranked]


def select(
    people: Sequence[HrPersonOut],
    *,
    department: UUID | Literal["none"] | None,
    manager: UUID | None,
    status: PersonStatus | None,
) -> list[HrPersonOut]:
    """Filter by department ("none" = no department), manager and status.
    No status keeps active and starting people."""
    kept = []
    for person in people:
        if status is None:
            if person.status == "left":
                continue
        elif person.status != status:
            continue
        if department == "none":
            if person.department_id is not None:
                continue
        elif department is not None and person.department_id != department:
            continue
        if manager is not None and person.manager_id != manager:
            continue
        kept.append(person)
    return kept


def page(items: Sequence[HrPersonOut], cursor: str | None) -> PeoplePage:
    """One page of ``PAGE_SIZE``; the cursor is a decimal offset."""
    if cursor is None:
        offset = 0
    elif _CURSOR.fullmatch(cursor) and cursor.isascii():
        offset = int(cursor)
    else:
        raise HrPolicyError("invalid_cursor")
    end = offset + PAGE_SIZE
    return PeoplePage(
        items=list(items[offset:end]),
        next_cursor=str(end) if end < len(items) else None,
        total=len(items),
    )


def org_chart(people: Sequence[HrPersonOut]) -> OrgChart:
    """Nodes for everyone who has not left. A manager outside the set makes a root; people on a
    management loop are listed in ``looped`` and shown as roots."""
    present = sorted((p for p in people if p.status != "left"), key=_name_order)
    parent = {p.employee_id: p.manager_id for p in present}
    parent = {eid: (m if m in parent else None) for eid, m in parent.items()}
    looped: set[UUID] = set()
    for start in parent:
        seen: set[UUID] = set()
        current: UUID | None = start
        while current is not None and current not in seen:
            seen.add(current)
            current = parent[current]
        if current is None:
            continue
        # `current` is on a cycle; walk it once to collect every member.
        member = current
        while member not in looped:
            looped.add(member)
            nxt = parent[member]
            assert nxt is not None
            member = nxt
    nodes = [
        OrgNode(
            employee_id=p.employee_id,
            display_name=p.display_name,
            job_title=p.job_title,
            department=p.department,
            manager_id=None if p.employee_id in looped else parent[p.employee_id],
        )
        for p in present
    ]
    return OrgChart(nodes=nodes, looped=[p.employee_id for p in present if p.employee_id in looped])


def history_fields(changes: Mapping[str, Mapping[str, str | None]]) -> list[FieldChange]:
    """Changed fields in the fixed history order; keys that are not history fields are dropped."""
    return [
        FieldChange(field=name, old=changes[name].get("old"), new=changes[name].get("new"))  # type: ignore[arg-type]
        for name in _HISTORY_ORDER
        if name in changes
    ]


def sections_for(viewer: Viewer) -> list[ProfileSection]:
    return ["overview", "access", "history"] if viewer == "hr" else ["overview"]
