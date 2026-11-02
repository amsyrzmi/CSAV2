"""Assistant actions for People: add, update and mark as leaving through a card, who reports to
whom, and opening a profile.

Every name is resolved against People as the caller sees it (``HrPeople.page``, ``profile`` and
``departments``, with the S1 word search), so the assistant never reaches a person the People
pages would not show them, and changes stay with the HR owner and Manage people holders
(``hr_people_viewer`` = "hr"). Nothing here reads personal details or documents.

The card (``HrPersonExecutor``, kind ``hr_person``) carries the HR operation body itself
(``person.add``, ``person.update`` or ``person.leave``), bound to the person's version, and runs
it through ``HrOperations.apply`` on Confirm.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import UUID, uuid5

from fastapi import HTTPException, Request
from pydantic import Field, TypeAdapter, ValidationError

from csa.console.contracts import ConsoleActor, Role
from csa.console.services import PackageState, require_package
from csa.core.db import q as sql
from csa.core.tenancy import tx
from csa.hr.api import hr_http_error, hr_session_from
from csa.hr.automation_models import PersonAdd, PersonLeave, PersonUpdate, PersonValues
from csa.hr.operations import HrOperations
from csa.hr.people_contracts import DepartmentList, HrPersonOut, PersonStatus
from csa.hr.people_rules import Viewer, kl_today, search
from csa.hr.people_service import HrPeople
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService
from csa.workspace.contracts import (
    AnswerProposal,
    CardField,
    ClarificationOption,
    ClarificationProposal,
    DirectoryResult,
    HrPersonAddProposal,
    HrPersonLeaveProposal,
    HrPersonOpenProposal,
    HrPersonUpdateProposal,
    Language,
    NavigateResult,
    PeopleQueryProposal,
    PersonOut,
    ProposalKind,
    RecordRef,
    WorkspaceTurnInput,
)
from csa.workspace.executors import _CARD_TEXT, _key
from csa.workspace.planner import PlannerFacts
from csa.workspace.registry import (
    CardContent,
    PreparedProposal,
    ProposalError,
    ProposalOrigin,
    ProposalRegistry,
)
from csa.workspace.rules import TAB_GATES, unlocked

PeopleChange = HrPersonAddProposal | HrPersonUpdateProposal | HrPersonLeaveProposal
PeopleProposal = PeopleChange | PeopleQueryProposal | HrPersonOpenProposal
PEOPLE_PROPOSALS = (
    HrPersonAddProposal,
    HrPersonUpdateProposal,
    HrPersonLeaveProposal,
    PeopleQueryProposal,
    HrPersonOpenProposal,
)
# What a people turn resolves to; the turn service turns each into its result.
Resolved = (
    AnswerProposal | ClarificationProposal | NavigateResult | DirectoryResult | PreparedProposal
)

MAX_OPTIONS = 5
OPTION_LABEL = 100
# Everyone who has not left; and those still working (not leaving), for a new manager.
PRESENT: frozenset[PersonStatus] = frozenset({"active", "starting", "leaving"})
WORKING: frozenset[PersonStatus] = frozenset({"active", "starting"})
REASONS = ("resigned", "contract_ended", "dismissed", "retired", "other")
_MONTHS_EN = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
_MONTHS_MS = (
    "Januari", "Februari", "Mac", "April", "Mei", "Jun", "Julai", "Ogos", "September",
    "Oktober", "November", "Disember",
)  # fmt: skip


# --- Reads as the caller ---------------------------------------------------------------------


def _hr_service(request: Request) -> HrService:
    service = getattr(request.app.state, "hr_service", None)
    if not isinstance(service, HrService):
        raise HTTPException(503, "hr_service_unavailable")
    return service


async def people_viewer(service: HrService, session: HrSession) -> Viewer | None:
    """``hr_people_viewer`` for this HR session: "hr", "directory", "self" or None."""
    async with tx(service.pool, str(session.tenant_id)) as conn:
        await service._access(conn, session)
        row = await sql.fetch_one("hr/people_viewer.sql", (session.tenant_id,), conn=conn)
        await service._session(conn, session)
    return row["viewer"] if row else None


async def people_access(
    request: Request, actor: ConsoleActor, client_id: UUID, packages: list[PackageState]
) -> Viewer | None:
    """The planner fact: the caller's People view, or None with no HR (or no HR session)."""
    service = getattr(request.app.state, "hr_service", None)
    if not isinstance(service, HrService) or not unlocked(packages, TAB_GATES["hr"]):
        return None
    try:
        return await people_viewer(
            service, hr_session_from(request, actor, client_id, mutation=False)
        )
    except HrPolicyError:
        return None


def _as_id(text: str | None) -> UUID | None:
    if text is None:
        return None
    try:
        return UUID(text.strip())
    except ValueError:
        return None


def _record_id(record: RecordRef | None) -> UUID | None:
    return _as_id(record.id) if record is not None and record.kind == "employee" else None


async def _candidates(
    people: HrPeople, session: HrSession, name: str | None, record: RecordRef | None
) -> list[HrPersonOut]:
    """The people ``resolve_person`` chooses from: the one named by an id (a picked option)
    or the open record, else the People search for the name. Read as the caller."""
    target = _as_id(name) if name is not None else _record_id(record)
    if name is None or target is not None:
        if target is None:
            return []
        try:
            return [(await people.profile(session, target)).person]
        except HrPolicyError as exc:
            if exc.code == "resource_unavailable":
                return []
            raise
    found = await people.page(
        session, q=name, department=None, manager=None, status=None, cursor=None
    )
    return list(found.items)


def _option(person: HrPersonOut) -> ClarificationOption:
    label = person.display_name
    if person.job_title:
        label = f"{label} — {person.job_title}"
    return ClarificationOption(label=label[:OPTION_LABEL], value=str(person.employee_id))


def resolve_person(
    people: Sequence[HrPersonOut],
    name: str | None,
    record: RecordRef | None,
    text: dict[str, str],
    *,
    statuses: frozenset[PersonStatus] = PRESENT,
) -> HrPersonOut | AnswerProposal | ClarificationProposal | None:
    """One person, or what to tell the user. Pure.

    No name with an open employee record means that record; an id (a picked option) means
    that person; otherwise the S1 word search over name, title and work email among
    ``statuses``. None means nobody was named (the caller asks who).
    """
    target = _as_id(name) if name is not None else _record_id(record)
    if name is None or target is not None:
        if target is None:
            return None
        for person in people:
            if person.employee_id == target and person.status in statuses:
                return person
        return AnswerProposal(action="answer", markdown=text["person_unavailable"])
    matches = [person for person in search(people, name) if person.status in statuses]
    if not matches:
        return AnswerProposal(action="answer", markdown=text["person_not_found"].format(name=name))
    if len(matches) == 1:
        return matches[0]
    return ClarificationProposal(
        action="clarification",
        question=text["which_person"].format(name=name),
        options=[_option(person) for person in matches[:MAX_OPTIONS]],
    )


def _folded(name: str) -> str:
    return " ".join(name.split()).casefold()


def resolve_department(departments: DepartmentList, name: str) -> tuple[UUID | None, str | None]:
    """``(department_id, None)`` for an existing department by folded name, else
    ``(None, new_department)``: the person.add and person.update forms' choice."""
    wanted = _folded(name)
    for department in departments.items:
        if _folded(department.name) == wanted:
            return department.department_id, None
    return None, " ".join(name.split())


def profile_path(employee_id: UUID, *, platform: bool, client_id: UUID) -> str:
    """The profile page: `/portal/hr/people/<id>`, or `/hr/people/<id>?client_id=` for admins."""
    if platform:
        return f"/hr/people/{employee_id}?{urlencode({'client_id': str(client_id)})}"
    return f"/portal/hr/people/{employee_id}"


# --- Turn resolution -------------------------------------------------------------------------


def _say(text: str) -> AnswerProposal:
    return AnswerProposal(action="answer", markdown=text)


def _ask(text: str, options: list[ClarificationOption] | None = None) -> ClarificationProposal:
    return ClarificationProposal(action="clarification", question=text, options=options or [])


class _Turn:
    """What one people turn reads with: the caller's People view and the turn's wording."""

    def __init__(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        turn: WorkspaceTurnInput,
        facts: PlannerFacts,
        registry: ProposalRegistry,
        text: dict[str, str],
    ) -> None:
        self.request, self.actor, self.client_id = request, actor, client_id
        self.turn, self.facts, self.registry, self.text = turn, facts, registry, text
        self.service = _hr_service(request)
        self.session = hr_session_from(request, actor, client_id, mutation=False)
        self.people = HrPeople(self.service)
        context = facts.page_context
        self.record = context.record if context is not None else None

    async def person(
        self, name: str | None, *, statuses: frozenset[PersonStatus] = PRESENT, record: bool = True
    ) -> HrPersonOut | AnswerProposal | ClarificationProposal:
        here = self.record if record else None
        found = resolve_person(
            await _candidates(self.people, self.session, name, here),
            name,
            here,
            self.text,
            statuses=statuses,
        )
        return _ask(self.text["ask_person"]) if found is None else found

    async def prepare(self, body: PersonAdd | PersonUpdate | PersonLeave) -> PreparedProposal:
        return await self.registry.prepare(
            self.request,
            self.actor,
            self.client_id,
            "hr_person",
            body.model_dump(mode="json"),
            origin="assistant",
            language=self.facts.language,
        )


async def resolve(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    turn: WorkspaceTurnInput,
    proposal: PeopleProposal,
    facts: PlannerFacts,
    registry: ProposalRegistry,
    text: dict[str, str],
) -> Resolved:
    """A people action, resolved against People as the caller; ``text`` is the turn's copy."""
    if not unlocked(facts.packages, TAB_GATES["hr"]):
        return _say(text["people_unavailable"])
    here = _Turn(request, actor, client_id, turn, facts, registry, text)
    reading = isinstance(proposal, PeopleQueryProposal | HrPersonOpenProposal)
    try:
        if isinstance(proposal, HrPersonOpenProposal):
            return await _open(here, proposal)
        if isinstance(proposal, PeopleQueryProposal):
            return await _query(here, proposal)
        # Changing people is for the HR owner and Manage people holders (as on the screens).
        if await people_viewer(here.service, here.session) != "hr":
            return _say(text["people_not_permitted"])
        if isinstance(proposal, HrPersonAddProposal):
            return await _add(here, proposal)
        if isinstance(proposal, HrPersonUpdateProposal):
            return await _update(here, proposal)
        return await _leave(here, proposal)
    except HrPolicyError as exc:
        if exc.code == "permission_denied":
            return _say(text["people_list_not_permitted" if reading else "people_not_permitted"])
        raise hr_http_error(exc) from None


async def _open(here: _Turn, proposal: HrPersonOpenProposal) -> Resolved:
    person = await here.person(proposal.person, record=False)
    if not isinstance(person, HrPersonOut):
        return person
    platform = here.actor.role == Role.PLATFORM_ADMIN
    return NavigateResult(
        action="navigate",
        tab="hr",
        section="people",
        record=RecordRef(kind="employee", id=str(person.employee_id)),
        path=profile_path(person.employee_id, platform=platform, client_id=here.client_id),
    )


async def _query(here: _Turn, proposal: PeopleQueryProposal) -> Resolved:
    manager: UUID | None = None
    if proposal.reports_to is not None:
        found = await here.person(proposal.reports_to, record=False)
        if not isinstance(found, HrPersonOut):
            return found
        manager = found.employee_id
    department: str | None = None
    if proposal.department is not None:
        known, _ = resolve_department(
            await here.people.departments(here.session), proposal.department
        )
        if known is None:
            return _say(here.text["department_not_found"].format(name=proposal.department))
        department = str(known)
    listed = await here.people.page(
        here.session, q=None, department=department, manager=manager, status=None, cursor=None
    )
    if not listed.items:
        if proposal.reports_to is not None and proposal.department is None:
            return _say(here.text["nobody_reports"].format(name=proposal.reports_to))
        return _say(here.text["nobody_matches"])
    return DirectoryResult(
        action="directory_lookup",
        candidates=[
            PersonOut(
                employee_id=person.employee_id,
                display_name=person.display_name,
                job_title=person.job_title,
                work_email=person.work_email,
            )
            for person in listed.items
        ],
    )


async def _manager(here: _Turn, name: str) -> UUID | AnswerProposal | ClarificationProposal:
    found = await here.person(name, statuses=WORKING, record=False)
    return found.employee_id if isinstance(found, HrPersonOut) else found


async def _department(here: _Turn, name: str | None) -> tuple[UUID | None, str | None] | None:
    if name is None:
        return None
    return resolve_department(await here.people.departments(here.session), name)


async def _add(here: _Turn, proposal: HrPersonAddProposal) -> Resolved:
    text = here.text
    for given, key in (
        (proposal.display_name, "ask_name"),
        (proposal.job_title, "ask_job_title"),
        (proposal.start_date, "ask_start_date"),
    ):
        if given is None:
            return _ask(text[key])
    manager: UUID | None = None
    if proposal.manager is not None:
        found = await _manager(here, proposal.manager)
        if not isinstance(found, UUID):
            return found
        manager = found
    department_id, new_department = await _department(here, proposal.department) or (None, None)
    assert proposal.display_name is not None
    try:
        body = PersonAdd(
            operation="person.add",
            employee_id=uuid5(here.turn.request_key, "hr_person_add"),
            values=PersonValues(
                display_name=proposal.display_name,
                job_title=proposal.job_title,
                start_date=proposal.start_date,
                manager_id=manager,
                department_id=department_id,
                new_department=new_department,
                work_email=proposal.work_email,
                employment_type=proposal.employment_type,
            ),
        )
    except ValidationError:
        return _say(text["person_values_invalid"])
    return await here.prepare(body)


async def _update(here: _Turn, proposal: HrPersonUpdateProposal) -> Resolved:
    text = here.text
    person = await here.person(proposal.person)
    if not isinstance(person, HrPersonOut):
        return person
    if person.version is None:  # only HR viewers see versions
        return _say(text["people_not_permitted"])
    changes = (
        proposal.job_title,
        proposal.department,
        proposal.manager,
        proposal.work_phone,
        proposal.employment_type,
        proposal.start_date,
    )
    if all(change is None for change in changes):
        return _ask(text["ask_change"])
    manager = person.manager_id
    if proposal.manager is not None:
        found = await _manager(here, proposal.manager)
        if not isinstance(found, UUID):
            return found
        manager = found
    department_id, new_department = await _department(here, proposal.department) or (
        person.department_id,
        None,
    )
    try:
        values = PersonValues(
            display_name=person.display_name,
            job_title=proposal.job_title or person.job_title,
            start_date=proposal.start_date or person.start_date,
            manager_id=manager,
            department_id=department_id,
            new_department=new_department,
            work_email=person.work_email,
            work_phone=proposal.work_phone or person.work_phone,
            employment_type=proposal.employment_type or person.employment_type,
        )
        body = PersonUpdate(
            operation="person.update",
            employee_id=person.employee_id,
            expected_version=person.version,
            values=values,
            employment_profile=person.employment_profile,
        )
    except ValidationError:
        return _say(text["person_values_invalid"])
    if not _changed_fields(person, values):
        return _say(text["no_change"].format(name=person.display_name))
    return await here.prepare(body)


async def _leave(here: _Turn, proposal: HrPersonLeaveProposal) -> Resolved:
    text = here.text
    person = await here.person(proposal.person)
    if not isinstance(person, HrPersonOut):
        return person
    if person.version is None:
        return _say(text["people_not_permitted"])
    if proposal.last_day is None:
        return _ask(text["ask_last_day"])
    if proposal.reason is None:
        labels = _CARD_TEXT[here.facts.language]
        options = [ClarificationOption(label=labels[f"reason_{r}"], value=r) for r in REASONS]
        return _ask(text["ask_reason"], options)
    if proposal.reason == "other" and proposal.note is None:
        return _ask(text["ask_leave_note"])
    if proposal.last_day < kl_today(here.service.now()):
        return _say(text["last_day_past"])
    # The reports move to the leaver's own manager unless that manager is no longer working.
    successor: UUID | None = None
    if person.manager_id is not None:
        found = await _candidates(here.people, here.session, str(person.manager_id), None)
        if found and found[0].status in WORKING:
            successor = person.manager_id
    try:
        body = PersonLeave(
            operation="person.leave",
            employee_id=person.employee_id,
            expected_version=person.version,
            last_day=proposal.last_day,
            reason=proposal.reason,
            note=proposal.note if proposal.reason == "other" else None,
            reports_new_manager=successor,
        )
    except ValidationError:
        return _say(text["person_values_invalid"])
    return await here.prepare(body)


# --- The card ---------------------------------------------------------------------------------

_OPERATION = TypeAdapter(
    Annotated[PersonAdd | PersonUpdate | PersonLeave, Field(discriminator="operation")]
)
# The fields an assistant update may change, in card order.
UpdateField = Literal[
    "job_title", "department", "manager", "work_phone", "employment_type", "start_date"
]
_UPDATE_FIELDS: tuple[UpdateField, ...] = (
    "job_title", "department", "manager", "work_phone", "employment_type", "start_date",
)  # fmt: skip


def _changed_fields(person: HrPersonOut, values: PersonValues) -> list[UpdateField]:
    """The card's fields whose new value differs from the person's current one."""
    department_changed = values.new_department is not None or (
        values.department_id != person.department_id
    )
    changed = {
        "job_title": values.job_title != person.job_title,
        "department": department_changed,
        "manager": values.manager_id != person.manager_id,
        "work_phone": values.work_phone != person.work_phone,
        "employment_type": values.employment_type != person.employment_type,
        "start_date": values.start_date != person.start_date,
    }
    return [name for name in _UPDATE_FIELDS if changed[name]]


def format_day(day: date, language: Language) -> str:
    """The card's date: 1 December 2026, or 1 Disember 2026 in Malay (never the locale's)."""
    month = (_MONTHS_MS if language == "ms" else _MONTHS_EN)[day.month - 1]
    return f"{day.day} {month} {day.year}"


class HrPersonExecutor:
    """Adds, updates or marks a person as leaving through ``HrOperations.apply``, after Confirm.

    The payload is the HR operation body; ``card`` re-reads the person as the caller, refuses
    unless they may change people, and binds the card to ``str(person.version)``.
    """

    kind: ProposalKind = "hr_person"

    @staticmethod
    def _session(
        request: Request, actor: ConsoleActor, client_id: UUID, origin: ProposalOrigin
    ) -> HrSession:
        # As TrainingSubmitExecutor: assistant proposals honour the tenant's hr_assistant switch.
        session = hr_session_from(request, actor, client_id, mutation=True)
        return session.model_copy(update={"assistant": origin == "assistant"})

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        await require_package(
            request.app.state.pool, client_id, frozenset({"hr"}), frozenset({"active"})
        )
        if origin != "assistant":
            return
        try:
            await _hr_service(request).require_capability(
                self._session(request, actor, client_id, origin), "hr_assistant"
            )
        except HrPolicyError as exc:
            raise hr_http_error(exc) from None

    @staticmethod
    def _body(payload: dict[str, Any]) -> PersonAdd | PersonUpdate | PersonLeave:
        try:
            return _OPERATION.validate_json(json.dumps(payload))
        except (ValidationError, TypeError, ValueError):
            raise HTTPException(422, "invalid proposal payload") from None

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
        body = self._body(payload)
        await self.gate(request, actor, client_id, origin=origin)
        service = _hr_service(request)
        session = self._session(request, actor, client_id, origin)
        card = _Card(HrPeople(service), session, _CARD_TEXT[language], language)
        try:
            if isinstance(body, PersonAdd):
                if await people_viewer(service, session) != "hr":
                    raise HrPolicyError("permission_denied")
                return await card.add(body), None
            profile = await card.people.profile(session, body.employee_id)
            if not profile.can_edit:
                raise HrPolicyError("permission_denied")
            person = profile.person
            if person.version != body.expected_version:
                raise ProposalError("proposal_stale")
            if isinstance(body, PersonUpdate):
                content = await card.update(person, body)
            else:
                content = await card.leave(person, body)
        except HrPolicyError as exc:
            raise hr_http_error(exc) from None
        return content, str(person.version)

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
        body = self._body(payload)
        if not isinstance(body, PersonAdd):
            if expected_version is None:
                raise RuntimeError("person proposal has no bound version")
            body = body.model_copy(update={"expected_version": int(expected_version)})
        session = self._session(request, actor, client_id, origin)
        try:
            receipt = await HrOperations(_hr_service(request)).apply(
                session, body, _key(proposal_id, "apply")
            )
        except HrPolicyError as exc:
            if exc.code == "person_changed":
                raise ProposalError("proposal_stale") from None
            raise hr_http_error(exc) from None
        # The operation's receipt: operation, person id, version and status, never a value.
        return receipt.model_dump(mode="json")


class _Card:
    """Card fields as "<Label>: old → new", names read as the caller."""

    def __init__(
        self, people: HrPeople, session: HrSession, text: dict[str, str], language: Language
    ) -> None:
        self.people = people
        self.session = session
        self.text = text
        self.language: Language = language
        self._departments: DepartmentList | None = None

    async def _name(self, employee_id: UUID | None) -> str:
        if employee_id is None:
            return self.text["no_manager"]
        return (await self.people.profile(self.session, employee_id)).person.display_name

    async def _department(self, values: PersonValues) -> str:
        if values.new_department is not None:
            return self.text["new_department"].format(name=values.new_department)
        if values.department_id is None:
            return self.text["none"]
        if self._departments is None:
            self._departments = await self.people.departments(self.session)
        for department in self._departments.items:
            if department.department_id == values.department_id:
                return department.name
        return self.text["none"]

    def _type(self, value: str | None) -> str:
        return self.text[f"type_{value}"] if value else self.text["none"]

    def _day(self, value: date | None) -> str:
        return format_day(value, self.language) if value else self.text["none"]

    def _field(self, key: str, value: str) -> CardField:
        return CardField(label=self.text[f"label_{key}"], value=value)

    async def add(self, body: PersonAdd) -> CardContent:
        values, text = body.values, self.text
        fields = [self._field("name", values.display_name)]
        if values.job_title:
            fields.append(self._field("job_title", values.job_title))
        if values.department_id is not None or values.new_department is not None:
            fields.append(self._field("department", await self._department(values)))
        if values.manager_id is not None:
            fields.append(self._field("manager", await self._name(values.manager_id)))
        fields.append(self._field("start_date", self._day(values.start_date)))
        if values.work_email:
            fields.append(self._field("work_email", values.work_email))
        if values.employment_type:
            fields.append(self._field("employment_type", self._type(values.employment_type)))
        return CardContent(
            title=text["hr_add_title"].format(name=values.display_name),
            summary=text["hr_add_summary"],
            fields=fields,
            language=self.language,
        )

    async def update(self, person: HrPersonOut, body: PersonUpdate) -> CardContent:
        values, text = body.values, self.text
        fields: list[CardField] = []
        for name in _changed_fields(person, values):
            if name == "department":
                old = person.department or text["none"]
                new = await self._department(values)
            elif name == "manager":
                old = person.manager_name or text["no_manager"]
                new = await self._name(values.manager_id)
            elif name == "employment_type":
                old, new = self._type(person.employment_type), self._type(values.employment_type)
            elif name == "start_date":
                old, new = self._day(person.start_date), self._day(values.start_date)
            else:
                old = getattr(person, name) or text["none"]
                new = getattr(values, name) or text["none"]
            fields.append(self._field(name, text["change"].format(old=old, new=new)))
        return CardContent(
            title=text["hr_update_title"].format(name=person.display_name),
            summary=text["hr_update_summary"].format(name=person.display_name),
            fields=fields,
            language=self.language,
        )

    async def leave(self, person: HrPersonOut, body: PersonLeave) -> CardContent:
        text, day = self.text, self._day(body.last_day)
        return CardContent(
            title=text["hr_leave_title"].format(name=person.display_name),
            summary=text["hr_leave_summary"].format(name=person.display_name, day=day),
            fields=[
                self._field("last_day", day),
                self._field("reason", text[f"reason_{body.reason}"]),
                self._field("reports_manager", await self._name(body.reports_new_manager)),
            ],
            language=self.language,
        )
