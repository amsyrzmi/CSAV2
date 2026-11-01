"""The People reads: directory, profile, history, access, org chart, departments and HR home.

Each read opens one transaction, resolves the caller's authority with `HrService._access`, reads
through the people SQL functions (which decide who may see which rows) and ends with the session
check, like `HrAccessPage`. The pure rules for status, search, filters and paging live in
`people_rules`.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection

from csa.core.db import q as sql
from csa.core.tenancy import tx
from csa.hr.access_page import describe
from csa.hr.access_switches import SWITCH_IDS, AccessRows, state
from csa.hr.people_contracts import (
    DepartmentList,
    DepartmentOut,
    HrHome,
    LinkedAccount,
    OrgChart,
    PeoplePage,
    PersonAccessOut,
    PersonChange,
    PersonHistory,
    PersonOut,
    PersonProfile,
    PersonStatus,
)
from csa.hr.people_rules import (
    Viewer,
    history_fields,
    kl_today,
    org_chart,
    page,
    person_from_row,
    search,
    sections_for,
    select,
)
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService

HISTORY_LIMIT = 200  # history rows returned; one more is read to tell it was cut


def _department_filter(department: str | None) -> UUID | Literal["none"] | None:
    if department is None or department == "none":
        return department
    try:
        return UUID(department)
    except ValueError as exc:
        raise HrPolicyError("invalid_department_filter") from exc


class HrPeople:
    def __init__(self, core: HrService):
        self.core = core

    async def _viewer(self, conn: AsyncConnection, session: HrSession) -> Viewer:
        row = await sql.fetch_one("hr/people_viewer.sql", (session.tenant_id,), conn=conn)
        if row is None or row["viewer"] is None:
            raise HrPolicyError("permission_denied")
        return row["viewer"]

    async def _load(
        self, conn: AsyncConnection, session: HrSession
    ) -> tuple[Viewer, list[PersonOut]]:
        viewer = await self._viewer(conn, session)
        rows = await sql.fetch_all("hr/people_rows.sql", (session.tenant_id,), conn=conn)
        today = kl_today(self.core.now())
        names = {row["employee_id"]: row["display_name"] for row in rows}
        return viewer, [person_from_row(row, today, names) for row in rows]

    @staticmethod
    def _person(people: list[PersonOut], employee_id: UUID) -> PersonOut:
        for person in people:
            if person.employee_id == employee_id:
                return person
        raise HrPolicyError("resource_unavailable")

    async def page(
        self,
        session: HrSession,
        *,
        q: str | None,
        department: str | None,
        manager: UUID | None,
        status: PersonStatus | None,
        cursor: str | None,
    ) -> PeoplePage:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            viewer, people = await self._load(conn, session)
            if viewer == "self" or (status == "left" and viewer != "hr"):
                raise HrPolicyError("permission_denied")
            chosen = _department_filter(department)
            await self.core._session(conn, session)
        kept = select(people, department=chosen, manager=manager, status=status)
        if q is not None and q.strip():
            kept = search(kept, q)
        return page(kept, cursor)

    async def profile(self, session: HrSession, employee_id: UUID) -> PersonProfile:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            viewer, people = await self._load(conn, session)
            person = self._person(people, employee_id)
            await self.core._session(conn, session)
        return PersonProfile(person=person, sections=sections_for(viewer), can_edit=viewer == "hr")

    async def _hr_person(
        self, conn: AsyncConnection, session: HrSession, employee_id: UUID
    ) -> None:
        viewer, people = await self._load(conn, session)
        if viewer != "hr":
            raise HrPolicyError("permission_denied")
        self._person(people, employee_id)

    async def history(self, session: HrSession, employee_id: UUID) -> PersonHistory:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            await self._hr_person(conn, session, employee_id)
            rows = await sql.fetch_all(
                "hr/employee_changes.sql",
                (session.tenant_id, employee_id, HISTORY_LIMIT + 1),
                conn=conn,
            )
            await self.core._session(conn, session)
        return PersonHistory(
            items=[
                PersonChange(
                    changed_at=row["changed_at"],
                    actor_name=row["actor_name"],
                    fields=history_fields(row["changes"]),
                )
                for row in rows[:HISTORY_LIMIT]
            ],
            truncated=len(rows) > HISTORY_LIMIT,
        )

    async def access(self, session: HrSession, employee_id: UUID) -> PersonAccessOut:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            await self._hr_person(conn, session, employee_id)
            row = await sql.fetch_one(
                "hr/people_access.sql", (session.tenant_id, employee_id), conn=conn
            )
            await self.core._session(conn, session)
        if row is None:
            return PersonAccessOut(account=None, on=[], custom=[], is_owner=False)
        switches = state(AccessRows.from_summary(row["grants"], row["workflows"], row["areas"]))
        return PersonAccessOut(
            account=LinkedAccount(user_id=row["user_id"], display_name=row["display_name"]),
            on=[switch for switch in SWITCH_IDS if switch in switches.on],
            custom=describe(switches.uncovered),
            is_owner=bool(row["is_owner"]),
        )

    async def org_chart(self, session: HrSession) -> OrgChart:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            viewer, people = await self._load(conn, session)
            if viewer == "self":
                raise HrPolicyError("permission_denied")
            await self.core._session(conn, session)
        return org_chart(people)

    async def departments(self, session: HrSession) -> DepartmentList:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            if await self._viewer(conn, session) == "self":
                raise HrPolicyError("permission_denied")
            rows = await sql.fetch_all("hr/department_rows.sql", (session.tenant_id,), conn=conn)
            await self.core._session(conn, session)
        return DepartmentList(items=[DepartmentOut.model_validate(row) for row in rows])

    async def home(self, session: HrSession) -> HrHome:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self.core._access(conn, session)
            training = await sql.fetch_one("hr/home_training.sql", (session.tenant_id,), conn=conn)
            requests = await sql.fetch_one("hr/home_requests.sql", (session.tenant_id,), conn=conn)
            await self.core._session(conn, session)
        return HrHome(training_waiting=training["n"], requests_waiting=requests["n"])
