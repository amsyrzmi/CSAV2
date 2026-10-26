"""Tenant-scoped workspace thread persistence and deterministic A1 actions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter, ValidationError

from csa.agents.base import utc_now
from csa.console.business import BusinessOrder, get_local_order, require_business_read
from csa.console.contracts import ConsoleActor, Role
from csa.console.inbox import conversation
from csa.console.packages import BY_KEY
from csa.console.services import PackageState, active_packages, require_package
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx
from csa.email.api import email_http_error, email_session_from
from csa.email.models import ComposeRequest, EmailPolicyError
from csa.email.service import EmailService
from csa.hr.api import hr_session_from
from csa.hr.policy import HrPolicyError
from csa.hr.service import HrService
from csa.workspace.contracts import (
    MAX_WORK_TTL,
    AnswerProposal,
    AnswerResult,
    ClarificationProposal,
    ClarificationResult,
    DirectoryLookupProposal,
    DirectoryResult,
    EmailComposeProposal,
    GuidanceProposal,
    GuidanceResult,
    InboxDecisionProposal,
    LocalOrderSummary,
    NavigateProposal,
    NavigateResult,
    OrderPageHint,
    OrderReadProposal,
    OrderReadResult,
    ProposalResult,
    RecordRef,
    ResolvedWorkspaceAuthority,
    TrainingSubmitProposal,
    VerifiedWorkspacePrincipal,
    WorkspaceActionResult,
    WorkspaceAllowed,
    WorkspaceDeniedResult,
    WorkspaceProposal,
    WorkspaceThreadOut,
    WorkspaceTurnInput,
    WorkspaceTurnOut,
    WorkspaceWorkBinding,
    validate_workspace_execution,
)
from csa.workspace.directory import my_people, search_people, throttle_lookup
from csa.workspace.planner import (
    PlannerFacts,
    PlannerInvalidOutput,
    WorkspacePlanner,
    detect_language,
)
from csa.workspace.registry import PreparedProposal, ProposalRegistry

_RESULT = TypeAdapter(WorkspaceActionResult)
_GUIDANCE_KEYS: dict[
    str,
    Literal[
        "workspace_home_help",
        "business_orders_help",
        "business_order_help",
    ],
] = {
    "workspace_home": "workspace_home_help",
    "business_orders": "business_orders_help",
    "business_order": "business_order_help",
}


def _stored_result(value: Any) -> WorkspaceActionResult:
    return _RESULT.validate_json(json.dumps(value, allow_nan=False))


class WorkspaceConflict(ValueError):
    """A same-key payload or resource version no longer matches."""


class WorkspaceNotFound(LookupError):
    """Thread or resource is missing in the current tenant/owner scope."""


def _fingerprint(turn: WorkspaceTurnInput) -> str:
    payload = json.dumps(
        turn.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _page_and_proposal(
    turn: WorkspaceTurnInput,
) -> tuple[str, GuidanceProposal | OrderReadProposal]:
    hint = turn.page_hint
    if isinstance(hint, OrderPageHint):
        return hint.page, OrderReadProposal(action="local_order_read", resource=hint.entity)
    page = hint.page if hint is not None else "workspace_home"
    return page, GuidanceProposal(action="page_guidance", page=page)


async def lock_request(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, key: UUID
) -> None:
    await db.q.fetch_one(
        "workspace/lock_request.sql",
        (f"workspace:{client_id}:{owner_user_id}:{key}",),
        conn=conn,
    )


async def create_thread(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, request_key: UUID
) -> WorkspaceThreadOut:
    await lock_request(conn, client_id, owner_user_id, request_key)
    row = await db.q.fetch_one(
        "workspace/thread_by_key.sql", (client_id, owner_user_id, request_key), conn=conn
    )
    duplicate = row is not None
    if row is None:
        row = await db.q.fetch_one(
            "workspace/thread_insert.sql", (client_id, owner_user_id, request_key), conn=conn
        )
        if row is None:
            raise RuntimeError("workspace_thread_insert_returned_no_row")
        await db.q.execute(
            "console/audit.sql",
            (
                owner_user_id,
                client_id,
                "workspace.thread.created",
                str(row["id"]),
                Jsonb({"thread_id": str(row["id"])}),
            ),
            conn=conn,
        )
    return WorkspaceThreadOut.model_validate({**row, "duplicate": duplicate})


async def list_threads(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, before: int | None
) -> tuple[list[WorkspaceThreadOut], int | None]:
    rows = await db.q.fetch_all(
        "workspace/threads.sql",
        (client_id, owner_user_id, before, before, 21),
        conn=conn,
    )
    items = [WorkspaceThreadOut.model_validate(row) for row in rows[:20]]
    return items, items[-1].sequence if len(rows) > 20 and items else None


async def get_thread(
    conn: AsyncConnection[Any], client_id: UUID, owner_user_id: UUID, thread_id: UUID
) -> WorkspaceThreadOut:
    row = await db.q.fetch_one(
        "workspace/thread_get.sql", (client_id, owner_user_id, thread_id), conn=conn
    )
    if row is None:
        raise WorkspaceNotFound("workspace conversation not found")
    return WorkspaceThreadOut.model_validate(row)


async def _fresh_result(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    thread: WorkspaceThreadOut,
    turn: WorkspaceTurnInput,
    *,
    now: datetime,
) -> tuple[str, GuidanceProposal | OrderReadProposal, WorkspaceActionResult]:
    page, proposal = _page_and_proposal(turn)
    require_business_read(actor, client_id)
    resource_updated_at = None
    order: BusinessOrder | None = None
    if isinstance(proposal, OrderReadProposal):
        order = await get_local_order(conn, client_id, proposal.resource.order_id)
        resource_updated_at = order.updated_at if order is not None else None
        if order is None:
            return page, proposal, WorkspaceDeniedResult(code="resource_unavailable")
    binding = WorkspaceWorkBinding(
        work_id=uuid4(),
        thread_id=thread.id,
        owner_user_id=actor.user_id,
        session_id=actor.session_id,
        tenant_id=client_id,
        request_key=turn.request_key,
        created_at=now,
        expires_at=now + MAX_WORK_TTL,
        proposal=proposal,
        expected_resource_updated_at=resource_updated_at,
    )
    principal = VerifiedWorkspacePrincipal(
        user_id=actor.user_id,
        session_id=actor.session_id,
        tenant_id=client_id,
        role=actor.role.value if actor.role is not None else "read_only",
        verified_at=now,
    )
    authority = ResolvedWorkspaceAuthority(
        work_id=binding.work_id,
        thread_id=thread.id,
        tenant_id=client_id,
        user_id=actor.user_id,
        session_id=actor.session_id,
        proposal=proposal,
        checked_at=now,
        capability_enabled=True,
        action_allowed=True,
        resource_allowed=not isinstance(proposal, OrderReadProposal) or order is not None,
        resource_updated_at=resource_updated_at,
    )
    decision = validate_workspace_execution(binding, principal, authority, now=now)
    if not isinstance(decision, WorkspaceAllowed):
        return page, proposal, decision
    if isinstance(proposal, GuidanceProposal):
        return (
            page,
            proposal,
            GuidanceResult(
                page=proposal.page,
                instruction_key=_GUIDANCE_KEYS[proposal.page],
                as_of=now,
            ),
        )
    if order is None:
        return page, proposal, WorkspaceDeniedResult(code="resource_unavailable")
    return (
        page,
        proposal,
        OrderReadResult(
            order=LocalOrderSummary(
                resource=proposal.resource,
                status=order.status,
                payment_status=order.payment_status,
                total=order.total,
                currency=order.currency,
                channel=order.channel,
                external_order_id=order.external_order_id,
                external_shop_id=order.external_shop_id,
                tracking_no=order.tracking_no,
                carrier=order.carrier,
                placed_at=order.placed_at,
                shipped_at=order.shipped_at,
                created_at=order.created_at,
                updated_at=order.updated_at,
            ),
            as_of=now,
        ),
    )


def _replayed(row: dict[str, Any], result: WorkspaceActionResult) -> WorkspaceTurnOut:
    return WorkspaceTurnOut.model_validate(
        {
            "id": row["id"],
            "request_key": row["request_key"],
            "body": row["body"],
            "result": result,
            "created_at": row["created_at"],
            "duplicate": True,
        }
    )


async def create_turn(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    *,
    now: datetime | None = None,
) -> WorkspaceTurnOut:
    timestamp = now or utc_now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("workspace clock must be timezone-aware")
    timestamp = timestamp.astimezone(UTC)
    await lock_request(conn, client_id, actor.user_id, turn.request_key)
    thread = await get_thread(conn, client_id, actor.user_id, thread_id)
    fingerprint = _fingerprint(turn)
    existing = await db.q.fetch_one(
        "workspace/turn_by_key.sql",
        (client_id, thread_id, actor.user_id, turn.request_key),
        conn=conn,
    )
    if existing is not None and existing["fingerprint"] != fingerprint:
        raise WorkspaceConflict("request key was already used for different content")
    page, proposal, result = await _fresh_result(
        conn, actor, client_id, thread, turn, now=timestamp
    )
    if existing is not None:
        previous = _stored_result(existing["result"])
        if isinstance(previous, OrderReadResult):
            if not isinstance(result, OrderReadResult) or (
                previous.order.updated_at != result.order.updated_at
            ):
                raise WorkspaceConflict("resource changed; submit a new request")
        return _replayed(existing, previous)
    resource_id = proposal.resource.order_id if isinstance(proposal, OrderReadProposal) else None
    return await _insert_turn(
        conn,
        actor,
        client_id,
        thread_id,
        turn,
        fingerprint=fingerprint,
        action=proposal.action,
        page=page,
        resource_id=resource_id,
        result=result,
    )


async def _insert_turn(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    *,
    fingerprint: str,
    action: str,
    page: str,
    resource_id: str | None,
    result: WorkspaceActionResult,
) -> WorkspaceTurnOut:
    body = redact(turn.body)
    if not body.strip() or len(body) > 8000:
        raise ValueError("workspace text could not be safely persisted")
    row = await db.q.fetch_one(
        "workspace/turn_insert.sql",
        (
            client_id,
            thread_id,
            actor.user_id,
            actor.session_id,
            turn.request_key,
            fingerprint,
            body,
            action,
            page,
            resource_id,
            Jsonb(result.model_dump(mode="json")),
        ),
        conn=conn,
    )
    if row is None:
        raise RuntimeError("workspace_turn_insert_returned_no_row")
    await db.q.execute(
        "console/audit.sql",
        (
            actor.user_id,
            client_id,
            "workspace.turn.completed",
            str(thread_id),
            Jsonb({"thread_id": str(thread_id), "action": action}),
        ),
        conn=conn,
    )
    row["result"] = result
    return WorkspaceTurnOut.model_validate({**row, "duplicate": False})


async def list_turns(
    conn: AsyncConnection[Any],
    client_id: UUID,
    owner_user_id: UUID,
    thread_id: UUID,
    before: int | None,
) -> tuple[list[WorkspaceTurnOut], int | None]:
    await get_thread(conn, client_id, owner_user_id, thread_id)
    rows = await db.q.fetch_all(
        "workspace/turns.sql",
        (client_id, thread_id, owner_user_id, before, before, 51),
        conn=conn,
    )
    items = []
    for row in rows[:50]:
        row["result"] = _stored_result(row["result"])
        items.append(WorkspaceTurnOut.model_validate(row))
    return items, items[-1].id if len(rows) > 50 and items else None


# ----------------------------------------------------------------- planner-driven turns
#
# Shape: (1) a short transaction checks the thread and replays a known request key; (2) with
# no connection held, facts are gathered, the planner runs and any mutating proposal is
# prepared by the registry (package gate, switch, card read); (3) one transaction takes the
# request lock, re-checks the key, cancels stale proposals on a tab change, runs the typed
# reads, inserts the proposal and stores the turn. Nothing is written before every gate ran.

_ACTIVE = frozenset({"active"})
# Tab -> (packages that unlock it, accepted statuses); mirrors web/lib/navigation.ts.
_TAB_GATES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "inbox": (frozenset({"customer_support"}), frozenset({"active", "suspended"})),
    "knowledge_base": (frozenset({"customer_support"}), _ACTIVE),
    "business": (frozenset({"orders_products", "marketplaces"}), _ACTIVE),
    "hr": (frozenset({"hr"}), _ACTIVE),
    "email": (frozenset({"staff_email"}), _ACTIVE),
}
# Record kind -> the package gate of the service that owns it.
_RECORD_GATES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "conversation": _TAB_GATES["inbox"],
    "approval": _TAB_GATES["inbox"],
    "order": _TAB_GATES["business"],
    "training_request": _TAB_GATES["hr"],
    "draft": _TAB_GATES["email"],
}
_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "locked": "The {tab} tab needs the {packages} service, which is not active for "
        "this workspace. Ask your administrator to activate it on the Services page.",
        "no_record": "No record is open.",
        "hr_inactive": "The HR service is not active, so your manager cannot be looked up.",
        "manager": "Your manager is {name}{title}.",
        "no_manager": "No manager is recorded for you.",
        "no_employee_link": "Your account is not linked to an employee record.",
        "packages": "Services: {packages}.",
        "no_packages": "No services are active.",
    },
    "ms": {
        "locked": "Tab {tab} memerlukan perkhidmatan {packages}, yang tidak aktif untuk "
        "ruang kerja ini. Minta pentadbir anda mengaktifkannya di halaman Services.",
        "no_record": "Tiada rekod dibuka.",
        "hr_inactive": "Perkhidmatan HR tidak aktif, jadi pengurus anda tidak dapat dicari.",
        "manager": "Pengurus anda ialah {name}{title}.",
        "no_manager": "Tiada pengurus direkodkan untuk anda.",
        "no_employee_link": "Akaun anda tidak dipautkan kepada rekod pekerja.",
        "packages": "Perkhidmatan: {packages}.",
        "no_packages": "Tiada perkhidmatan aktif.",
    },
}


def _unlocked(packages: list[PackageState], gate: tuple[frozenset[str], frozenset[str]]) -> bool:
    keys, statuses = gate
    return any(p.key in keys and p.status in statuses for p in packages)


def _tab_allowed(facts: PlannerFacts, tab: str) -> bool:
    gate = _TAB_GATES.get(tab)
    return facts.role == Role.PLATFORM_ADMIN or gate is None or _unlocked(facts.packages, gate)


def _unavailable(kind: str) -> str:
    return f"The open {kind} record is not available to you."


async def _summarize(
    request: Request, actor: ConsoleActor, client_id: UUID, record: RecordRef
) -> str:
    """Server-built, redacted one-liner; ids and states only, never bodies or addresses."""
    pool = request.app.state.pool
    try:
        if record.kind == "employee":
            return f"Employee record {UUID(record.id)} is open; its details are not shared."
        if record.kind == "order":
            async with tx(pool, str(client_id)) as conn:
                order = await get_local_order(conn, client_id, record.id)
            if order is None:
                return _unavailable(record.kind)
            return (
                f"Order {redact(record.id)}: status {order.status}, payment "
                f"{order.payment_status}, total {order.total} {order.currency}."
            )
        if record.kind == "conversation":
            async with tx(pool, str(client_id)) as conn:
                current = await conversation(conn, client_id, UUID(record.id))
            return (
                f"Customer conversation {current.id}: status {current.status}, "
                f"inbox_version {current.inbox_version}."
            )
        if record.kind == "approval":
            async with tx(pool, str(client_id)) as conn:
                approval = await db.q.fetch_one(
                    "inbox/get_approval.sql", (client_id, int(record.id)), conn=conn
                )
                if approval is None:
                    return _unavailable(record.kind)
                current = await conversation(conn, client_id, approval["conversation_id"])
            return (
                f"Held reply approval {approval['id']} (status {approval['status']}) on "
                f"customer conversation {current.id}, inbox_version {current.inbox_version}."
            )
        if record.kind == "training_request":
            hr = getattr(request.app.state, "hr_service", None)
            if not isinstance(hr, HrService):
                raise HTTPException(503, "hr_service_unavailable")
            training = await hr.training(
                hr_session_from(request, actor, client_id, mutation=False), UUID(record.id)
            )
            return (
                f"Training request {training.request_id}: “{redact(training.content.title)}”, "
                f"status {training.status.value}, version {training.version}."
            )
        email = getattr(request.app.state, "email_service", None)
        if not isinstance(email, EmailService):
            raise HTTPException(503, "email_assistant_not_configured")
        draft = await email.draft_by_id(
            email_session_from(request, actor, client_id, mutation=False), UUID(record.id)
        )
        return f"Email draft {draft.id}: subject “{redact(draft.subject)}”, status {draft.status}."
    except (HrPolicyError, EmailPolicyError):
        return _unavailable(record.kind)
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            return _unavailable(record.kind)
        raise


async def record_summary(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    record: RecordRef,
    packages: list[PackageState],
) -> str:
    gate = _RECORD_GATES.get(record.kind)
    if gate is not None and not _unlocked(packages, gate):
        return f"The open {record.kind} record's service is not active."
    if record.kind == "approval" and not (record.id.isdigit() and len(record.id) <= 18):
        return _unavailable(record.kind)
    if record.kind not in ("approval", "order"):
        try:
            UUID(record.id)
        except ValueError:
            return _unavailable(record.kind)
    return await _summarize(request, actor, client_id, record)


async def planner_facts(
    request: Request, actor: ConsoleActor, client_id: UUID, turn: WorkspaceTurnInput
) -> PlannerFacts:
    """Run with no connection held; each read borrows one pool connection at a time."""
    if actor.role is None:
        raise RuntimeError("workspace actor has no role")
    context = turn.page_context
    packages = await active_packages(request.app.state.pool, client_id)
    summary = None
    if context is not None and context.record is not None:
        summary = await record_summary(request, actor, client_id, context.record, packages)
    return PlannerFacts(
        role=actor.role,
        packages=packages,
        page_context=context,
        record_summary=summary,
        language=detect_language(turn.body),
    )


@dataclass(frozen=True)
class _Planned:
    """A planner proposal after every no-connection gate; finished inside the turn tx."""

    result: WorkspaceActionResult | None = None
    prepared: PreparedProposal | None = None
    lookup: str | None = None
    answer: AnswerProposal | None = None


def _payload(proposal: TrainingSubmitProposal | InboxDecisionProposal) -> dict[str, Any]:
    return proposal.model_dump(mode="json", exclude={"action"}, exclude_none=True)


async def _compose(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    proposal: EmailComposeProposal,
    registry: ProposalRegistry,
) -> PreparedProposal:
    """Write the draft (never sent) and prepare its export; the package gate runs first."""
    service = getattr(request.app.state, "email_service", None)
    if not isinstance(service, EmailService):
        raise HTTPException(503, "email_assistant_not_configured")
    await require_package(request.app.state.pool, client_id, frozenset({"staff_email"}))
    try:
        compose = ComposeRequest(
            recipient_employee_id=proposal.recipient_employee_id,
            recipient_address=proposal.recipient_address,
            subject=proposal.subject,
            # The planner may put everything in the subject; compose needs a nonblank brief.
            instructions=proposal.instructions or proposal.subject,
            language=proposal.language,
        )
    except ValidationError:
        raise HTTPException(422, "email_recipient_invalid") from None
    try:
        draft = await service.compose(
            email_session_from(request, actor, client_id, mutation=True),
            compose,
            uuid5(turn.request_key, f"workspace_compose:{thread_id}"),
        )
    except EmailPolicyError as exc:
        raise email_http_error(exc) from None
    return await registry.prepare(
        request, actor, client_id, "email_export", {"draft_id": str(draft.id)}, origin="assistant"
    )


async def _resolve(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    proposal: WorkspaceProposal,
    facts: PlannerFacts,
    registry: ProposalRegistry,
) -> _Planned:
    if isinstance(proposal, AnswerProposal):
        return _Planned(answer=proposal)
    if isinstance(proposal, NavigateProposal):
        if not _tab_allowed(facts, proposal.tab):
            keys = _TAB_GATES[proposal.tab][0]
            names = " or ".join(sorted(BY_KEY[k].title for k in keys))
            text = _TEXT[facts.language]["locked"].format(tab=proposal.tab, packages=names)
            return _Planned(result=AnswerResult(action="answer", markdown=text))
        return _Planned(
            result=NavigateResult(
                action="navigate",
                tab=proposal.tab,
                section=proposal.section,
                record=proposal.record,
                resume=proposal.resume,
            )
        )
    if isinstance(proposal, DirectoryLookupProposal):
        # Same gate and throttle as GET /workspace/people.
        await require_package(request.app.state.pool, client_id, frozenset({"hr"}))
        query = proposal.query.strip()
        if not query:
            raise PlannerInvalidOutput()  # a blank pattern would list the whole directory
        throttle_lookup(actor.session_id)
        return _Planned(lookup=query)
    if isinstance(proposal, ClarificationProposal):
        return _Planned(
            result=ClarificationResult(
                action="clarification", question=proposal.question, options=proposal.options
            )
        )
    if isinstance(proposal, TrainingSubmitProposal | InboxDecisionProposal):
        prepared = await registry.prepare(
            request, actor, client_id, proposal.action, _payload(proposal), origin="assistant"
        )
        return _Planned(prepared=prepared)
    if isinstance(proposal, EmailComposeProposal):
        prepared = await _compose(request, actor, client_id, thread_id, turn, proposal, registry)
        return _Planned(prepared=prepared)
    raise PlannerInvalidOutput()  # legacy guidance/order actions are never planned


async def _answer(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    proposal: AnswerProposal,
    facts: PlannerFacts,
) -> AnswerResult:
    """Append server-rendered typed reads to the planner's markdown."""
    text = _TEXT[facts.language]
    lines: list[str] = []
    for read in dict.fromkeys(proposal.reads):
        if read == "read_current_record":
            lines.append(facts.record_summary or text["no_record"])
        elif read == "my_packages":
            states = ", ".join(f"{p.key} ({p.status})" for p in facts.packages)
            found = text["packages"].format(packages=states) if states else text["no_packages"]
            lines.append(found)
        elif not _unlocked(facts.packages, _TAB_GATES["hr"]):
            lines.append(text["hr_inactive"])
        else:
            mine = await my_people(conn, client_id, actor.user_id)
            if mine.manager is not None:
                title = f", {mine.manager.job_title}" if mine.manager.job_title else ""
                lines.append(text["manager"].format(name=mine.manager.display_name, title=title))
            else:
                lines.append(text[mine.reason or "no_manager"])
    markdown = proposal.markdown
    if lines:
        markdown += "\n\n" + "\n".join(f"- {line}" for line in lines)
    return AnswerResult(action="answer", markdown=markdown)


async def _finish(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    planned: _Planned,
    facts: PlannerFacts,
    registry: ProposalRegistry,
) -> WorkspaceActionResult:
    if planned.result is not None:
        return planned.result
    if planned.prepared is not None:
        prepared = planned.prepared
        stored = await registry.insert(
            conn, actor, client_id, prepared, thread_id=thread_id, origin="assistant"
        )
        return ProposalResult(action="proposal", proposal=stored)
    if planned.lookup is not None:
        found = await search_people(conn, client_id, planned.lookup)
        # Never the query or any name: only how many candidates were returned.
        await db.q.execute(
            "console/audit.sql",
            (
                actor.user_id,
                client_id,
                "workspace.people.lookup",
                "company_directory",
                Jsonb({"candidates": len(found)}),
            ),
            conn=conn,
        )
        return DirectoryResult(action="directory_lookup", candidates=found)
    if planned.answer is None:
        raise RuntimeError("planned workspace turn has nothing to store")
    return await _answer(conn, actor, client_id, planned.answer, facts)


async def _known_turn(
    conn: AsyncConnection[Any],
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    fingerprint: str,
) -> WorkspaceTurnOut | None:
    row = await db.q.fetch_one(
        "workspace/turn_by_key.sql",
        (client_id, thread_id, actor.user_id, turn.request_key),
        conn=conn,
    )
    if row is None:
        return None
    if row["fingerprint"] != fingerprint:
        raise WorkspaceConflict("request key was already used for different content")
    return _replayed(row, _stored_result(row["result"]))


async def create_planned_turn(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    *,
    planner: WorkspacePlanner,
    registry: ProposalRegistry,
) -> WorkspaceTurnOut:
    """A ``page_context`` turn; opens its own short transactions (see the shape above)."""
    context = turn.page_context
    if context is None:
        raise ValueError("planned turns need a page_context")
    pool = request.app.state.pool
    fingerprint = _fingerprint(turn)
    async with tx(pool, str(client_id)) as conn:
        await get_thread(conn, client_id, actor.user_id, thread_id)
        known = await _known_turn(conn, actor, client_id, thread_id, turn, fingerprint)
    if known is not None:
        return known  # a replay never re-plans or re-prepares
    facts = await planner_facts(request, actor, client_id, turn)
    proposal = await planner.plan(turn.body, facts)
    planned = await _resolve(request, actor, client_id, thread_id, turn, proposal, facts, registry)
    async with tx(pool, str(client_id)) as conn:
        await lock_request(conn, client_id, actor.user_id, turn.request_key)
        await get_thread(conn, client_id, actor.user_id, thread_id)
        known = await _known_turn(conn, actor, client_id, thread_id, turn, fingerprint)
        if known is not None:
            return known
        # Threads are tenant-bound, so a tab change is the only context switch to fence here.
        previous = await db.q.fetch_one(
            "workspace/turn_last.sql", (client_id, thread_id, actor.user_id), conn=conn
        )
        if previous is not None and previous["page_key"] != context.tab:
            await registry.cancel_thread(conn, actor, client_id, thread_id)
        result = await _finish(conn, actor, client_id, thread_id, planned, facts, registry)
        return await _insert_turn(
            conn,
            actor,
            client_id,
            thread_id,
            turn,
            fingerprint=fingerprint,
            action=proposal.action,
            page=context.tab,
            resource_id=None,
            result=result,
        )
