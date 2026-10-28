"""The HR access page service: owner-only reads and batch saves over the switch rules.

Every write goes through `HrOperations.apply`, one person's operation at a time, so authorization,
receipts and authority events stay exactly as for the old access forms. The version stamp is checked
once per batch; each person's save is its own transaction (accepted in the design, not atomic across
the batch).
"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from psycopg import AsyncConnection

from csa.core.db import q as sql
from csa.core.tenancy import tx
from csa.hr.access_contracts import (
    PAGE_SIZE,
    AccessChangeResult,
    AccessChanges,
    AccessEvent,
    AccessEventPage,
    AccessPeoplePage,
    AccessPerson,
    AccessPersonResult,
    AccessWarningOut,
)
from csa.hr.access_switches import (
    SWITCH_IDS,
    AccessLimitError,
    AccessRows,
    AccessWarning,
    PersonAccess,
    ReportingLine,
    SwitchId,
    SwitchState,
    grant_switches,
    operations,
    rows,
    stamp,
    state,
    warnings,
)
from csa.hr.automation_models import AccessSave, PrivateAccessSave
from csa.hr.operations import HrOperations
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService

AREAS = ("recruitment", "attendance", "payroll")

_WHOSE = {
    "self": "their own",
    "assigned_manager": "their team's",
    "hr_operator": "everyone's",
    "client_owner": "everyone's",
    "payroll_operator": "everyone's",
    "payroll_reviewer": "everyone's",
}
_ROLE = {
    "self": "",
    "assigned_manager": "team-manager ",
    "hr_operator": "HR ",
    "client_owner": "client-owner ",
    "payroll_operator": "payroll-preparer ",
    "payroll_reviewer": "payroll-reviewer ",
}
_FIELDS = {
    "display_name": "name",
    "job_title": "job title",
    "department": "team",
    "work_email": "work email",
    "assigned_manager": "manager",
}
_TRAINING = {
    "training.read": "Sees {whose} training requests",
    "training.draft": "Drafts {whose} training requests",
    "training.submit": "Submits {whose} training requests",
    "training.withdraw": "Withdraws {whose} training requests",
    "training.decide": "Records {role}decisions on training",
}
_WORKFLOW = {
    "read": "Sees {whose} {domain} records",
    "prepare": "Prepares {whose} {domain} records",
    "decide": "Records {role}decisions on {domain}",
}


def describe(uncovered: AccessRows) -> list[str]:
    """The rows no switch describes, in plain words, for the person's "Custom access" list."""
    words: list[str] = []
    for g in sorted(uncovered.grants):
        whose, role = _WHOSE.get(g.scope, g.scope), _ROLE.get(g.scope, f"{g.scope} ")
        if g.action == "directory.read":
            fields = [label for field, label in _FIELDS.items() if field in g.fields]
            words.append(
                f"Sees {whose} details: {', '.join(fields)}"
                if fields
                else f"Sees {whose} directory entry, with no details"
            )
        else:
            text = _TRAINING.get(g.action, f"{g.action} ({{whose}})")
            words.append(text.format(whose=whose, role=role))
    for w in sorted(uncovered.workflows):
        whose, role = _WHOSE.get(w.scope, w.scope), _ROLE.get(w.scope, f"{w.scope} ")
        text = _WORKFLOW.get(w.action, f"{w.action} {{domain}} ({{whose}})")
        words.append(text.format(whose=whose, role=role, domain=w.domain))
    words.extend(f"Opens the private {area} area" for area in sorted(uncovered.areas))
    return words


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not re.fullmatch(r"[0-9]{1,7}", cursor):
        raise HrPolicyError("invalid_cursor")
    return int(cursor)


def _event_cursor(occurred_at: datetime, request_key: UUID) -> str:
    raw = f"{occurred_at.isoformat()}|{request_key}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _event_position(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        moment, key = raw.split("|")
        at = datetime.fromisoformat(moment)
        if at.tzinfo is None:
            raise ValueError(cursor)
        return at, UUID(key)
    except ValueError as exc:  # binascii.Error and UnicodeDecodeError are ValueErrors
        raise HrPolicyError("invalid_cursor") from exc


def _keys(batch_id: UUID, user_id: UUID) -> Iterator[UUID]:
    yield uuid5(batch_id, f"{user_id}:access")
    for area in AREAS:
        yield uuid5(batch_id, f"{user_id}:area:{area}")


def _key(batch_id: UUID, user_id: UUID, op: AccessSave | PrivateAccessSave) -> UUID:
    if isinstance(op, PrivateAccessSave):
        return uuid5(batch_id, f"{user_id}:area:{op.area}")
    return uuid5(batch_id, f"{user_id}:access")


def _switches_of(details: Mapping[str, Any]) -> frozenset[SwitchId]:
    saved = AccessRows.from_summary(details.get("grants", []), details.get("workflows", []), [])
    return grant_switches(saved.grants, saved.workflows)


def _ordered(switches: frozenset[SwitchId]) -> list[SwitchId]:
    return [s for s in SWITCH_IDS if s in switches]


def _person(p: PersonAccess, s: SwitchState) -> AccessPerson:
    return AccessPerson(
        user_id=p.user_id,
        display_name=p.display_name,
        employee_id=p.employee_id,
        department=p.department,
        manager_id=p.manager_id,
        team_size=p.team_size,
        is_owner=p.is_owner,
        on=_ordered(s.on),
        included=dict(s.included),
        custom=describe(s.uncovered),
    )


def _warnings_out(
    found: Sequence[AccessWarning], people: Sequence[PersonAccess], lines: Sequence[ReportingLine]
) -> list[AccessWarningOut]:
    users = {p.user_id: p.display_name for p in people}
    employees = {line.employee_id: line for line in lines}
    out: list[AccessWarningOut] = []
    for w in found:
        line = employees.get(w.employee_id) if w.employee_id is not None else None
        manager_name = None
        if w.kind == "no_approver" and line is not None:
            boss = (
                employees.get(line.manager_employee_id)
                if line.manager_employee_id is not None
                else None
            )
            if boss is not None:
                manager_name = boss.display_name
            elif line.manager_user_id is not None:
                manager_name = users.get(line.manager_user_id)
        out.append(
            AccessWarningOut(
                kind=w.kind,
                user_id=w.user_id,
                employee_id=w.employee_id,
                user_name=users.get(w.user_id) if w.user_id is not None else None,
                employee_name=line.display_name if line is not None else None,
                manager_name=manager_name,
            )
        )
    return out


class HrAccessPage:
    def __init__(self, core: HrService):
        self.core = core

    async def _load(
        self, conn: AsyncConnection, session: HrSession
    ) -> tuple[list[PersonAccess], list[ReportingLine]]:
        HrOperations.owner(await self.core._access(conn, session))
        summary = await sql.fetch_all("hr/access_summary.sql", (session.tenant_id,), conn=conn)
        lines = await sql.fetch_all(
            "hr/access_reporting_lines.sql", (session.tenant_id,), conn=conn
        )
        return [PersonAccess.from_summary(row) for row in summary], [
            ReportingLine(
                row["employee_id"],
                row["display_name"],
                row["manager_employee_id"],
                row["manager_user_id"],
            )
            for row in lines
        ]

    async def _snapshot(self, session: HrSession) -> tuple[list[PersonAccess], list[ReportingLine]]:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            loaded = await self._load(conn, session)
            await self.core._session(conn, session)
        return loaded

    async def people(
        self,
        session: HrSession,
        *,
        cursor: str | None,
        q: str | None,
        team: str | None,
        has: SwitchId | None,
        reports_to: UUID | None,
    ) -> AccessPeoplePage:
        people, lines = await self._snapshot(session)
        start = _offset(cursor)
        states = {p.user_id: state(p.rows) for p in people}
        needle = q.casefold() if q else None
        matches = [
            p
            for p in people
            if (needle is None or needle in (p.display_name or "").casefold())
            and (team is None or p.department == team)
            and (has is None or has in states[p.user_id].on)
            and (reports_to is None or p.manager_id == reports_to)
        ]
        end = start + PAGE_SIZE
        return AccessPeoplePage(
            items=[_person(p, states[p.user_id]) for p in matches[start:end]],
            next_cursor=str(end) if end < len(matches) else None,
            version=stamp(people, lines),
            counts={s: sum(1 for st in states.values() if s in st.on) for s in SWITCH_IDS},
            warnings=_warnings_out(warnings(people, lines), people, lines),
        )

    async def changes(self, session: HrSession, body: AccessChanges) -> AccessChangeResult:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            people, lines = await self._load(conn, session)
            replayed = set() if body.dry_run else await self._receipts(conn, session, body)
            await self.core._session(conn, session)
        # A batch with any receipt is a retry of one that landed: its saved people now compute to
        # no change, so it is answered from the receipts and the stamp is not checked again.
        if not replayed and stamp(people, lines) != body.version:
            raise HrPolicyError("access_changed")
        by_user = {p.user_id: p for p in people}
        proposed = dict(by_user)
        apply = HrOperations(self.core)
        results: list[AccessPersonResult] = []
        for change in body.changes:
            user = change.user_id
            if user in replayed:
                results.append(AccessPersonResult(user_id=user, outcome="saved"))
                continue
            current = by_user.get(user)
            if current is None:
                results.append(
                    AccessPersonResult(user_id=user, outcome="refused", reason="not_a_member")
                )
                continue
            after = rows(frozenset(change.on), state(current.rows).uncovered)
            try:
                ops = operations(user, current.rows, after)
            except AccessLimitError:
                results.append(
                    AccessPersonResult(user_id=user, outcome="refused", reason="too_many_rows")
                )
                continue
            if not ops:
                results.append(AccessPersonResult(user_id=user, outcome="unchanged"))
                continue
            if body.dry_run:
                proposed[user] = current._replace(rows=after)
                results.append(AccessPersonResult(user_id=user, outcome="saved"))
                continue
            try:
                for op in ops:
                    await apply.apply(session, op, _key(body.batch_id, user, op))
            except HrPolicyError as exc:
                results.append(AccessPersonResult(user_id=user, outcome="refused", reason=exc.code))
                continue
            results.append(AccessPersonResult(user_id=user, outcome="saved"))
        if body.dry_run:
            final, version = list(proposed.values()), stamp(people, lines)
        else:
            final, lines = await self._snapshot(session)
            version = stamp(final, lines)
        return AccessChangeResult(
            results=results,
            version=version,
            warnings=_warnings_out(warnings(final, lines), final, lines),
        )

    async def _receipts(
        self, conn: AsyncConnection, session: HrSession, body: AccessChanges
    ) -> set[UUID]:
        """People of this batch with at least one stored receipt for the owner."""
        found: set[UUID] = set()
        for change in body.changes:
            for key in _keys(body.batch_id, change.user_id):
                row = await sql.fetch_one(
                    "hr/op_get.sql", (session.tenant_id, session.subject_id, key), conn=conn
                )
                if row is not None:
                    found.add(change.user_id)
                    break
        return found

    async def recent(self, session: HrSession, *, cursor: str | None) -> AccessEventPage:
        tenant = session.tenant_id
        async with tx(self.core.pool, str(tenant)) as conn:
            HrOperations.owner(await self.core._access(conn, session))
            position = _event_position(cursor)
            moment, key = position if position is not None else (None, None)
            summary = await sql.fetch_all("hr/access_summary.sql", (tenant,), conn=conn)
            events = await sql.fetch_all(
                "hr/access_event_page.sql", (tenant, moment, moment, key, PAGE_SIZE + 1), conn=conn
            )
            page = events[:PAGE_SIZE]
            earlier: dict[int, frozenset[SwitchId]] = {}
            for index, event in enumerate(page):
                if event["operation"] != "access.save":
                    continue
                target = event["details"]["user_id"]
                prior = next(
                    (
                        e["details"]
                        for e in events[index + 1 :]
                        if e["operation"] == "access.save" and e["details"]["user_id"] == target
                    ),
                    None,
                )
                if prior is None:
                    row = await sql.fetch_one(
                        "hr/access_event_previous.sql",
                        (tenant, target, event["occurred_at"], event["request_key"]),
                        conn=conn,
                    )
                    prior = row["details"] if row is not None else None
                earlier[index] = _switches_of(prior) if prior is not None else frozenset()
            await self.core._session(conn, session)
        names = {row["user_id"]: row["display_name"] for row in summary}
        items: list[AccessEvent] = []
        for index, event in enumerate(page):
            details = event["details"]
            target = UUID(details["user_id"])
            common = {
                "occurred_at": event["occurred_at"],
                "actor_user_id": event["subject_id"],
                "actor_name": names.get(event["subject_id"]),
                "target_user_id": target,
                "target_name": names.get(target),
            }
            if event["operation"] == "access.save":
                now, before = _switches_of(details), earlier[index]
                items.append(
                    AccessEvent(
                        **common,
                        turned_on=_ordered(now - before),
                        turned_off=_ordered(before - now),
                        area=None,
                        area_enabled=None,
                    )
                )
            else:
                items.append(
                    AccessEvent(
                        **common,
                        turned_on=[],
                        turned_off=[],
                        area=details["area"],
                        area_enabled=bool(details["enabled"]),
                    )
                )
        last = page[-1] if page and len(events) > PAGE_SIZE else None
        return AccessEventPage(
            items=items,
            next_cursor=_event_cursor(last["occurred_at"], last["request_key"]) if last else None,
        )
