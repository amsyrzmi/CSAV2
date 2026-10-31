"""Tenant-scoped workspace thread persistence and deterministic A1 actions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

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
from csa.decide.completion import CompletionError
from csa.email.api import email_http_error, email_session_from
from csa.email.models import (
    AttentionPage,
    ComposeRequest,
    DraftOut,
    EmailPolicyError,
    ReplyRequest,
    TeamComposeRequest,
    draft_version,
    is_employee_ref,
)
from csa.email.service import EmailService
from csa.hr.api import hr_session_from
from csa.hr.policy import HrPolicyError
from csa.hr.service import HrService
from csa.workspace.contracts import (
    MAX_WORK_TTL,
    AnswerProposal,
    AnswerResult,
    CardField,
    ClarificationOption,
    ClarificationProposal,
    ClarificationResult,
    DirectoryLookupProposal,
    DirectoryResult,
    EmailComposeProposal,
    EmailReplyProposal,
    EmailTeamProposal,
    GuidanceProposal,
    GuidanceResult,
    InboxDecisionProposal,
    InboxSummaryProposal,
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
from csa.workspace.directory import MyTeam, my_people, my_team, search_people, throttle_lookup
from csa.workspace.planner import (
    MailboxThreadFact,
    ManagerFact,
    PlannerFacts,
    PlannerInvalidOutput,
    PriorTurn,
    TeamFact,
    TeamMemberFact,
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
    stored: WorkspaceActionResult | None = None,
) -> WorkspaceTurnOut:
    """Store the turn; ``stored`` replaces ``result`` in the row when the answer is private."""
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
            Jsonb((stored or result).model_dump(mode="json")),
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
    "subscriptions": (frozenset({"subscriptions"}), _ACTIVE),
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
        "people_unavailable": "Looking up colleagues is not part of this workspace's "
        "subscribed services; it needs the HR service.",
        "team_package_inactive": "Emailing your team needs the HR service, which is not "
        "active for this workspace.",
        "team_not_team_approver": "Emailing your team is for people who approve requests "
        "for their team. Ask your administrator to turn on “Approves training, leave and "
        "attendance for their team” for you in HR access.",
        "team_no_employee_link": "Your account is not linked to an employee record, so your "
        "team cannot be found.",
        "team_no_reports": "Nobody reports to you in HR, so there is no team to email.",
        "team_no_work_email": "Nobody on your team has a work email in HR. Ask HR to add "
        "them first.",
        "team_team_too_large": "Your team has more than 20 people with a work email. One "
        "email can go to at most 20 people.",
        "summary_none": "Nothing in your synced email needs your attention right now.",
        "summary_head": "Here is what needs your attention in your synced email{as_of}:",
        "summary_as_of": " (as of {time})",
        "summary_from": "from {sender}, {priority} priority",
        "summary_next": "Next: {steps}",
        "summary_skipped": "{count} could not be summarised. Open the Email assistant tab "
        "to read them.",
        "summary_stored": "Inbox summary: {count}.",
        "summary_mailbox_not_connected": "Connect your Gmail on the Email assistant tab first.",
        "summary_assistant_disabled": "The Email assistant is turned off for your workspace.",
        "summary_permission_denied": "Your access doesn't include the email summary.",
        "summary_unavailable": "I couldn't summarise your inbox just now. Try again in a few "
        "minutes.",
        "summary_never_synced": "Your email hasn't been synced yet. Open the Email assistant tab "
        "and press Sync, then ask again.",
        "content_question": "What should the email say? Type a line or two and I'll write it.",
        "high": "high",
        "normal": "normal",
        "low": "low",
        "email": "email",
        "emails": "emails",
        "item": "item",
        "items": "items",
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
        "people_unavailable": "Carian rakan sekerja bukan sebahagian daripada perkhidmatan "
        "yang dilanggan ruang kerja ini; ia memerlukan perkhidmatan HR.",
        "team_package_inactive": "E-mel kepada pasukan anda memerlukan perkhidmatan HR, "
        "yang tidak aktif untuk ruang kerja ini.",
        "team_not_team_approver": "E-mel kepada pasukan hanya untuk mereka yang meluluskan "
        "permohonan pasukan sendiri. Minta pentadbir anda menghidupkan “Approves training, "
        "leave and attendance for their team” untuk anda dalam akses HR.",
        "team_no_employee_link": "Akaun anda tidak dipautkan kepada rekod pekerja, jadi "
        "pasukan anda tidak dapat dicari.",
        "team_no_reports": "Tiada sesiapa melapor kepada anda dalam HR, jadi tiada pasukan "
        "untuk dihantar e-mel.",
        "team_no_work_email": "Tiada ahli pasukan anda yang mempunyai e-mel kerja dalam HR. "
        "Minta HR menambahnya dahulu.",
        "team_team_too_large": "Pasukan anda mempunyai lebih daripada 20 orang dengan e-mel "
        "kerja. Satu e-mel boleh dihantar kepada paling ramai 20 orang.",
        "summary_none": "Tiada e-mel yang disegerakkan memerlukan perhatian anda sekarang.",
        "summary_head": "Ini yang memerlukan perhatian anda dalam e-mel yang disegerakkan{as_of}:",
        "summary_as_of": " (setakat {time})",
        "summary_from": "daripada {sender}, keutamaan {priority}",
        "summary_next": "Seterusnya: {steps}",
        "summary_skipped": "{count} tidak dapat diringkaskan. Buka tab Email assistant untuk "
        "membacanya.",
        "summary_stored": "Ringkasan peti masuk: {count}.",
        "summary_mailbox_not_connected": "Sambungkan Gmail anda di tab Email assistant dahulu.",
        "summary_assistant_disabled": "Email assistant dimatikan untuk ruang kerja anda.",
        "summary_permission_denied": "Akses anda tidak termasuk ringkasan e-mel.",
        "summary_unavailable": "Saya tidak dapat meringkaskan peti masuk anda sekarang. Cuba "
        "lagi dalam beberapa minit.",
        "summary_never_synced": "E-mel anda belum disegerakkan. Buka tab Pembantu E-mel dan "
        "tekan Segerak, kemudian tanya semula.",
        "content_question": "Apa yang patut e-mel itu katakan? Taip satu atau dua ayat dan saya "
        "akan menulisnya.",
        "high": "tinggi",
        "normal": "biasa",
        "low": "rendah",
        "email": "e-mel",
        "emails": "e-mel",
        "item": "item",
        "items": "item",
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
                pending = await db.q.fetch_all(
                    "inbox/list_approvals.sql",
                    (client_id, None, None, current.id, current.id, False, 5),
                    conn=conn,
                )
            held = (
                "pending held reply approvals: "
                + ", ".join(f"approval {row['id']} (status {row['status']})" for row in pending)
                if pending
                else "no pending held reply approval"
            )
            return (
                f"Customer conversation {current.id}: status {current.status}, "
                f"inbox_version {current.inbox_version}; {held}."
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
        # The subject is for the planner and the live answer; _stored_answer drops it.
        return f"Email draft {draft.id}: subject “{redact(draft.subject)}”, status {draft.status}."
    except (HrPolicyError, EmailPolicyError):
        return _unavailable(record.kind)
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            return _unavailable(record.kind)
        raise


# The open draft's line as _summarize writes it (\u201c and \u201d are its curly quotes).
# Migration 0485 rewrites older stored answers with the same pattern, so old and new rows read
# alike.
_DRAFT_SUBJECT = re.compile(
    r"(Email draft [0-9a-f-]{36}): subject \u201c.*\u201d, (status [a-z_]+\.)", re.DOTALL
)


def _stored_answer(result: AnswerResult) -> AnswerResult | None:
    """The row's copy of an answer that quoted an open draft: no subject, as on the cards.

    Turns are readable tenant-wide and mail content is owner-only, so the subject is shown in
    the live answer only. ``None`` when the answer quotes no draft.
    """
    markdown = _DRAFT_SUBJECT.sub(r"\1, \2", result.markdown)
    if markdown == result.markdown:
        return None
    return AnswerResult(action="answer", markdown=markdown)


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


HISTORY_TURNS = 6
HISTORY_CHARS = 1000  # per stored body, bounding the prompt
# "my boss" in English and Bahasa Melayu; resolved from the caller's own HR record.
_MANAGER_WORDS = re.compile(
    r"\b(boss|manager|supervisor|line manager|bos|pengurus|penyelia|ketua)\b", re.IGNORECASE
)


def _prior_turns(rows: list[dict[str, Any]], current: str) -> list[PriorTurn]:
    """Oldest-first memory: stored bodies (redacted at storage) and what each turn offered.

    ``chosen`` is the offered id/option that the next message (the current one for the last
    stored turn) picked.
    """
    rows = list(reversed(rows))
    history: list[PriorTurn] = []
    for index, row in enumerate(rows):
        result = _stored_result(row["result"])
        following = rows[index + 1]["body"] if index + 1 < len(rows) else current
        item: dict[str, Any] = {
            "message": row["body"][:HISTORY_CHARS],
            "action": row["action"],
            "result": getattr(result, "action", None) or getattr(result, "status", "unknown"),
        }
        if isinstance(result, ProposalResult):
            item["proposal_kind"] = result.proposal.kind
        elif isinstance(result, DirectoryResult):
            offered = [person.employee_id for person in result.candidates]
            item["offered_employee_ids"] = offered
            item["chosen"] = next((str(i) for i in offered if str(i) in following), None)
        elif isinstance(result, ClarificationResult):
            values = [option.value for option in result.options]
            item["question"] = result.question
            item["offered_options"] = values
            # Stored values have addresses masked (see _clarification), so match masked text.
            picked = _masked(following)
            item["chosen"] = next(
                (v for v in values if re.search(rf"(?<!\w){re.escape(v)}(?!\w)", picked)),
                None,
            )
        history.append(PriorTurn.model_validate(item))
    return history


async def _my_manager(
    request: Request, actor: ConsoleActor, client_id: UUID, packages: list[PackageState]
) -> ManagerFact:
    if not _unlocked(packages, _TAB_GATES["hr"]):
        return ManagerFact(employee_id=None, reason="package_inactive")
    async with tx(request.app.state.pool, str(client_id)) as conn:
        mine = await my_people(conn, client_id, actor.user_id)
    if mine.manager is None:
        return ManagerFact(employee_id=None, reason=mine.reason or "no_manager")
    return ManagerFact(employee_id=mine.manager.employee_id, reason=None)


MAILBOX_FACTS = 10
MAILBOX_SUBJECT_CHARS = 120
# The message is about email (a reply, a thread, a sender, the inbox) in English or Malay.
_MAIL_WORDS = re.compile(
    r"\b(e-?mails?|e-?mel|inbox|gmail|mail|repl(?:y|ies|ied|ying)|respond|threads?|senders?"
    r"|attention|balas|balasan|peti masuk)\b",
    re.IGNORECASE,
)
_TEAM_WORDS = re.compile(r"\b(team|my reports|direct reports|pasukan|anak buah)\b", re.IGNORECASE)
_SEND_WORDS = re.compile(r"\b(send|hantar|hantarkan|kirim|kirimkan)\b", re.IGNORECASE)
_DRAFT_WORDS = re.compile(r"\b(drafts?|draf|save|simpan)\b", re.IGNORECASE)
_NEGATED_SEND = re.compile(
    r"\b(?:don['’]?t|do not|never|jangan)\s+(?:send|hantar|kirim)\w*", re.IGNORECASE
)
_EMAIL_VERB = re.compile(r"\b(e-?mails?|e-?mel|emel)\b", re.IGNORECASE)
_WRITE_WORD = re.compile(r"\b(write|tulis|karang)\b", re.IGNORECASE)
_ADDRESS = re.compile(r"[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMPLOYEE_ID = re.compile(
    r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])"
)


def _email_service(request: Request) -> EmailService:
    service = getattr(request.app.state, "email_service", None)
    if not isinstance(service, EmailService):
        raise HTTPException(503, "email_assistant_not_configured")
    return service


async def _mailbox_threads(
    request: Request, actor: ConsoleActor, client_id: UUID, packages: list[PackageState]
) -> list[MailboxThreadFact] | None:
    """Up to ten recent synced threads of the caller's own mailbox, or None when unreadable.

    Untrusted: any sender writes these. Subjects are redacted and cut; nothing here is logged
    or stored.
    """
    service = getattr(request.app.state, "email_service", None)
    if not isinstance(service, EmailService) or not _unlocked(packages, _TAB_GATES["email"]):
        return None
    try:
        page = await service.threads(email_session_from(request, actor, client_id, mutation=False))
    except EmailPolicyError:
        return None
    facts: list[MailboxThreadFact] = []
    for item in page.items[:MAILBOX_FACTS]:
        name, address = parseaddr(item.sender)
        facts.append(
            MailboxThreadFact(
                thread_id=item.thread_id,
                sender_name=" ".join(name.split())[:100] or None,
                sender_address=address.strip().lower()[:254] or None,
                subject=" ".join(redact(item.subject).split())[:MAILBOX_SUBJECT_CHARS],
                received_at=item.received_at,
                priority=item.priority,
            )
        )
    return facts


async def _my_team(
    request: Request, actor: ConsoleActor, client_id: UUID, packages: list[PackageState]
) -> MyTeam:
    if not _unlocked(packages, _TAB_GATES["hr"]):
        return MyTeam([], [], "package_inactive")
    async with tx(request.app.state.pool, str(client_id)) as conn:
        return await my_team(conn, client_id, actor.user_id)


def _team_fact(team: MyTeam) -> TeamFact:
    return TeamFact(
        reports=[
            TeamMemberFact(employee_id=p.employee_id, name=p.display_name) for p in team.reports
        ],
        skipped=[
            TeamMemberFact(employee_id=p.employee_id, name=p.display_name) for p in team.skipped
        ],
        reason=team.reason,
    )


async def planner_facts(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
) -> PlannerFacts:
    """Run with no connection held; each read borrows one pool connection at a time."""
    if actor.role is None:
        raise RuntimeError("workspace actor has no role")
    context = turn.page_context
    pool = request.app.state.pool
    packages = await active_packages(pool, client_id)
    summary = None
    if context is not None and context.record is not None:
        summary = await record_summary(request, actor, client_id, context.record, packages)
    async with tx(pool, str(client_id)) as conn:
        rows = await db.q.fetch_all(
            "workspace/turn_history.sql",
            (client_id, thread_id, actor.user_id, HISTORY_TURNS),
            conn=conn,
        )
    manager = None
    if _MANAGER_WORDS.search(turn.body):
        manager = await _my_manager(request, actor, client_id, packages)
    threads = None
    if _MAIL_WORDS.search(turn.body):
        threads = await _mailbox_threads(request, actor, client_id, packages)
    team = None
    if _TEAM_WORDS.search(turn.body):
        team = _team_fact(await _my_team(request, actor, client_id, packages))
    return PlannerFacts(
        role=actor.role,
        packages=packages,
        page_context=context,
        record_summary=summary,
        language=detect_language(turn.body),
        history=_prior_turns(rows, turn.body),
        my_manager=manager,
        mailbox_threads=threads,
        my_team=team,
    )


@dataclass(frozen=True)
class _Planned:
    """A planner proposal after every no-connection gate; finished inside the turn tx."""

    result: WorkspaceActionResult | None = None
    prepared: PreparedProposal | None = None
    lookup: str | None = None
    answer: AnswerProposal | None = None
    summary: AttentionPage | None = None
    stored: WorkspaceActionResult | None = None  # the row's copy when it differs from result


def _payload(proposal: TrainingSubmitProposal | InboxDecisionProposal) -> dict[str, Any]:
    return proposal.model_dump(mode="json", exclude={"action"}, exclude_none=True)


# The console's answer to a clarification: "<typed>\n(Answer to “<question>” for: <request>)",
# where <request> folds earlier answers in as "; answered “<question>”: <answer>". Planner text
# is made inert (see _inert), so these markers in a message can only come from the console.
_ANSWER_MARK = "\n(Answer to “"
_REQUEST_MARK = "” for: "
_ANSWERED_MARK = "; answered “"
_FOLDED_MARK = "”: "
# The console's address mask (lib/assistant-view.ts maskAddresses), used for stored copies.
_ADDRESS_RUN = re.compile(r"[^\s@<>]+@[^\s@<>]+")


def _addresses(text: str) -> set[str]:
    return {found.lower() for found in _ADDRESS.findall(text)}


def _masked(text: str) -> str:
    return _ADDRESS_RUN.sub("[address]", text)


def _inert(text: str) -> str:
    """Planner text the console echoes back in a turn, made unable to forge its markers.

    The console repeats a clarification's question and a clicked option's value inside its
    answer turn, and the turn service reads that turn by the markers above to tell the user's
    words from the planner's. Curly quotes and whitespace become plain, and the marker phrases are
    reworded, so a question, label or value can never end one part early or open another.
    """
    text = text.replace("“", '"').replace("”", '"')
    # Every whitespace run, newlines included, is one plain space: Python's \s also matches
    # \x1c-\x1f and \x85 where JavaScript's does not, and both sides must mask the same text.
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\(\s*answer to", "(reply to", text, flags=re.IGNORECASE)
    text = re.sub(r";\s*answered", ", answered", text, flags=re.IGNORECASE)
    return text.replace('" for: ', '" - ')


# An option that only asks the user to type ("Enter address", "Masukkan alamat") or a code the
# model made up for one (enter_address, subject_body): clicking it would send the code back.
_PLACEHOLDER_LABEL = re.compile(
    r"(?:enter|type|provide|input|write|specify|fill in|masukkan|taip|berikan|isi)\b",
    re.IGNORECASE,
)
_PLACEHOLDER_VALUE = re.compile(r"[a-z]+(?:_[a-z]+)+")
# A snake_case value is a placeholder only when a token says what to type; real snake_case
# choices (knowledge_base, first_aid, training_request) stay.
_PLACEHOLDER_TOKENS = frozenset(
    {
        "enter", "type", "provide", "input", "specify", "write", "address", "subject", "body",
        "email", "details", "message", "content",
    }
)  # fmt: skip
# A clarification that asks for what the planner must write itself; corrected on email turns.
_CONTENT_WORDS = re.compile(
    r"\b(subject|body|wording|content|tajuk|isi|kandungan)\b", re.IGNORECASE
)
# Email words only: "send" and "hantar" also send a training request, so they do not count.
_EMAIL_REQUEST = re.compile(r"\b(e-?mails?|e-?mel|emel|reply|balas|mail)\b", re.IGNORECASE)
_CONTENT_NOTE = (
    "Do not ask for the subject or the wording. Write them yourself from the request; if the "
    "request says nothing about the content, write a short, neutral email from what it does say."
)


def _is_placeholder(option: ClarificationOption) -> bool:
    """The option asks the user to type rather than offering a choice to click.

    A label that starts with a whole word such as "Enter" or "Masukkan", or a snake_case value
    with a token such as "enter", "address" or "subject" (enter_address, subject_body). Real
    choices survive: "Enterprise plan", "First aid", knowledge_base, an employee id or a thread id.
    """
    if _PLACEHOLDER_LABEL.match(option.label.strip()):
        return True
    value = option.value.strip()
    return bool(_PLACEHOLDER_VALUE.fullmatch(value)) and not _PLACEHOLDER_TOKENS.isdisjoint(
        value.split("_")
    )


def _asks_for_content(proposal: WorkspaceProposal) -> bool:
    """A clarification asking for the subject or wording, which the planner writes itself."""
    return isinstance(proposal, ClarificationProposal) and bool(
        _CONTENT_WORDS.search(proposal.question)
    )


def _about_email(message: str, facts: PlannerFacts) -> bool:
    """The user's own words are about email: an email word ("email", "e-mel", "reply",
    "balas", "mail") or an address.

    Only the parts ``_authored`` counts, so a planner question or a clicked value never makes
    a turn about email. Loaded mailbox facts do not count either: words anywhere in the turn
    load them, the folded planner question included. A bare send word ("send", "hantar") is
    not email.
    """
    return any(
        _EMAIL_REQUEST.search(part) or _addresses(part) for part in _authored(message, facts)
    )


def _picks_thread_or_colleague(proposal: WorkspaceProposal, facts: PlannerFacts) -> bool:
    """A clarification offering a synced thread (thread_id) or a colleague (employee id).

    Such a question may name a subject ("Which email, 'Invoice March' or 'Invoice April'?")
    without asking the user to write one. The raw option values are checked.
    """
    if not isinstance(proposal, ClarificationProposal):
        return False
    threads = {thread.thread_id for thread in facts.mailbox_threads or []}
    return any(
        option.value.strip() in threads or is_employee_ref(option.value.strip())
        for option in proposal.options
    )


async def _plan(planner: WorkspacePlanner, message: str, facts: PlannerFacts) -> WorkspaceProposal:
    """Plan the turn with at most two model calls.

    On an email turn (see ``_about_email``), a plan that asks for the subject or wording is
    re-planned once with a fixed server note. If the model asks again, the user gets one plain
    question with no options. A question that picks a thread or a colleague (see
    ``_picks_thread_or_colleague``) and any other clarification pass through after one call. A
    planner error on the re-plan is raised as on the first call.
    """
    proposal = await planner.plan(message, facts)
    if (
        not _asks_for_content(proposal)
        or not _about_email(message, facts)
        or _picks_thread_or_colleague(proposal, facts)
    ):
        return proposal
    proposal = await planner.plan(message, facts, note=_CONTENT_NOTE)
    if not _asks_for_content(proposal):
        return proposal
    question = _TEXT[facts.language]["content_question"]
    return ClarificationProposal(action="clarification", question=question)


def _clarification(
    proposal: ClarificationProposal,
) -> tuple[ClarificationResult, ClarificationResult]:
    """The clarification the user sees and the copy stored; options never carry an address.

    Placeholder options (see ``_is_placeholder``) are dropped first: the user types that answer,
    so a question with none left is a plain question. Everything is inert (see ``_inert``).
    Option labels and values have addresses masked live too, so a click can never add an
    address: colleagues are picked by employee id and threads by thread_id, and an outside
    address is typed by the user. Masking can lengthen a value, so an option past its limit once
    masked is refused rather than cut (a cut value would no longer be recognised as a click). The
    stored copy masks the question as well: turn rows hold no address, while the live question
    may show one the user wrote.
    """
    kept = [o for o in proposal.options if not _is_placeholder(o)]
    pairs = [(_masked(_inert(o.label)), _masked(_inert(o.value))) for o in kept]
    if any(len(label) > 100 or len(value) > 200 for label, value in pairs):
        raise PlannerInvalidOutput()
    options = [ClarificationOption(label=label, value=value) for label, value in pairs]
    question = _inert(proposal.question)
    return (
        ClarificationResult(action="clarification", question=question, options=options),
        ClarificationResult(action="clarification", question=_masked(question), options=options),
    )


def _answer_parts(message: str) -> tuple[str, str, list[str]]:
    """``(typed, first request, folded answers)`` of a console answer turn.

    The question is left out wherever it appears. A plain message is all typed text. The
    request is what follows the last ``” for: ``, so text before it cannot pose as the request.
    """
    typed, marked, rest = message.partition(_ANSWER_MARK)
    if not marked:
        return typed, "", []
    _, found, after = rest.rpartition(_REQUEST_MARK)
    request = after.strip().removesuffix(")") if found else ""
    first, _, folded = request.partition(_ANSWERED_MARK)
    answers = [part.partition(_FOLDED_MARK)[2] for part in folded.split(_ANSWERED_MARK)]
    return typed, first, answers if folded else []


def _picked(text: str, values: list[str]) -> bool:
    """The text is one of the offered option values: a click, not the user's own words."""
    key = _masked(_inert(text)).strip().lower()
    return bool(key) and any(key == _masked(_inert(v)).strip().lower() for v in values)


def _sent_before(text: str, facts: PlannerFacts) -> bool:
    """The text is a message this thread already stored, so the user sent it earlier."""
    want = " ".join(redact(text).split())
    return bool(want) and any(want == " ".join(turn.message.split()) for turn in facts.history)


def _authored(message: str, facts: PlannerFacts) -> list[str]:
    """The parts of the current message the user wrote, for addresses and employee ids.

    Counted: the typed text, unless it is just a value the clarification being answered offered
    (a click); the first request, when it is a message this thread already stored (history is
    redacted, so it is compared redacted); each folded answer, unless it equals a value any
    earlier clarification offered. Never counted: a clarification question, wherever the turn
    repeats it, and a first request the thread never stored. Planner text is inert, so it cannot
    move itself into a counted part. A typed answer equal to an offered value (for example an
    address the planner offered as an option) is taken as a click: the user writes it in a new
    request instead.
    """
    typed, first, answers = _answer_parts(message)
    latest = facts.history[-1].offered_options if facts.history else []
    offered = [value for turn in facts.history for value in turn.offered_options]
    parts = [] if _picked(typed, latest) else [typed]
    if first and _sent_before(first, facts):
        parts.append(first)
    parts.extend(answer for answer in answers if not _picked(answer, offered))
    return parts


def _requested_addresses(message: str, facts: PlannerFacts) -> set[str]:
    """Addresses the user wrote, from the parts of this message ``_authored`` counts.

    History is redacted at storage and adds none; mailbox facts are never a source.
    """
    return {address for part in _authored(message, facts) for address in _addresses(part)}


def _offered_employee_ids(message: str, facts: PlannerFacts) -> set[UUID]:
    """Employee ids the user could have meant in an email_compose or email_reply.

    Ids in this message (a directory result the user picked), the ids earlier directory turns
    offered, and my_manager. my_team ids are not among them: they serve email_team only, whose
    recipients the server adds itself.
    """
    ids = {
        UUID(found) for part in _authored(message, facts) for found in _EMPLOYEE_ID.findall(part)
    }
    ids.update(employee for turn in facts.history for employee in turn.offered_employee_ids)
    if facts.my_manager is not None and facts.my_manager.employee_id is not None:
        ids.add(facts.my_manager.employee_id)
    return ids


def _check_recipients(
    proposal: EmailComposeProposal | EmailReplyProposal, message: str, facts: PlannerFacts
) -> None:
    """Refuse a plan that adds an address the user did not write (Review Focus 4).

    Mailbox facts are untrusted, so an address seen only there is never a recipient, CC or BCC;
    a thread's sender is reached only through ``email_reply``, whose To comes from the thread
    itself. An employee id in To, CC or BCC must be one the facts offered the user (see
    ``_offered_employee_ids``); any other id is refused like an invented one.
    """
    refs = [*proposal.cc, *proposal.bcc]
    if isinstance(proposal, EmailComposeProposal):
        if proposal.recipient_address is not None:
            refs.append(proposal.recipient_address)
        if proposal.recipient_employee_id is not None:
            refs.append(str(proposal.recipient_employee_id))
    if isinstance(proposal, EmailReplyProposal):
        synced = {thread.thread_id for thread in facts.mailbox_threads or []}
        if proposal.thread_id not in synced:
            raise PlannerInvalidOutput()
    allowed = _requested_addresses(message, facts)
    offered = _offered_employee_ids(message, facts)
    for ref in refs:
        if is_employee_ref(ref):
            if UUID(ref) not in offered:
                raise PlannerInvalidOutput()
        elif ref.strip().lower() not in allowed:
            raise PlannerInvalidOutput()


def _asked_to_send(
    proposal: EmailComposeProposal | EmailReplyProposal | EmailTeamProposal,
    message: str,
    facts: PlannerFacts,
) -> bool:
    """A send card when the user's own words ask to send; the first rule that matches wins.

    1. A draft word ("draft", "drafts", "save", "simpan") or a negated send ("don't send",
       "do not send", "never send", "jangan hantar") gives a draft card, even next to "send".
    2. A send word ("send", "hantar", "kirim") gives a send card, unless a write word ("write",
       "tulis", "karang") is there too and the plan's ``send`` is false.
    3. The plan's ``send`` with an email verb ("email", "e-mail", "e-mel") and no write word
       gives a send card.
    4. Anything else gives a draft card.

    Only the parts ``_authored`` counts: typed text, the stored first request and typed folded
    answers. Never a clarification question ("Should I send it?") and never a clicked value
    ("yes, send it now"): the planner wrote both. A part equal to a value any earlier card
    offered does not count either, so typing an older card's "send it now" fails closed. The
    user confirms either card.
    """
    offered = [value for turn in facts.history for value in turn.offered_options]
    text = "\n".join(part for part in _authored(message, facts) if not _picked(part, offered))
    if _DRAFT_WORDS.search(text) or _NEGATED_SEND.search(text):
        return False
    written = bool(_WRITE_WORD.search(text))
    if _SEND_WORDS.search(text) and (proposal.send or not written):
        return True
    return bool(proposal.send and _EMAIL_VERB.search(text) and not written)


async def _draft_card(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    registry: ProposalRegistry,
    draft: DraftOut,
    *,
    send: bool,
) -> PreparedProposal:
    """A send card bound to the draft's version, or today's save-to-Gmail-drafts card."""
    if send:
        payload: dict[str, Any] = {"draft_id": str(draft.id), "version": draft_version(draft)}
        return await registry.prepare(
            request, actor, client_id, "email_send", payload, origin="assistant"
        )
    return await registry.prepare(
        request, actor, client_id, "email_export", {"draft_id": str(draft.id)}, origin="assistant"
    )


async def _compose(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    proposal: EmailComposeProposal,
    registry: ProposalRegistry,
    *,
    send: bool,
) -> PreparedProposal:
    """Write the draft (never sent here) and prepare its card; the package gate runs first."""
    service = _email_service(request)
    await require_package(request.app.state.pool, client_id, frozenset({"staff_email"}))
    if proposal.recipient_employee_id is not None or any(
        is_employee_ref(ref) for ref in [*proposal.cc, *proposal.bcc]
    ):
        # Directory-resolved recipients need the hr package, as on POST /email/compose.
        await require_package(request.app.state.pool, client_id, frozenset({"hr"}))
    try:
        compose = ComposeRequest(
            recipient_employee_id=proposal.recipient_employee_id,
            recipient_address=proposal.recipient_address,
            cc=list(proposal.cc),
            bcc=list(proposal.bcc),
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
    except (CompletionError, TimeoutError):
        raise HTTPException(503, "email_model_unavailable") from None
    return await _draft_card(request, actor, client_id, registry, draft, send=send)


async def _reply(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    proposal: EmailReplyProposal,
    registry: ProposalRegistry,
    *,
    send: bool,
) -> PreparedProposal:
    """Draft a reply in a synced thread; the email service picks the To from the thread."""
    service = _email_service(request)
    await require_package(request.app.state.pool, client_id, frozenset({"staff_email"}))
    if any(is_employee_ref(ref) for ref in [*proposal.cc, *proposal.bcc]):
        await require_package(request.app.state.pool, client_id, frozenset({"hr"}))
    try:
        reply = ReplyRequest(
            instructions=proposal.instructions,
            cc=list(proposal.cc),
            bcc=list(proposal.bcc),
            language=proposal.language,
        )
    except ValidationError:
        raise HTTPException(422, "email_recipient_invalid") from None
    try:
        draft = await service.reply(
            email_session_from(request, actor, client_id, mutation=True),
            proposal.thread_id,
            reply,
            uuid5(turn.request_key, f"workspace_reply:{thread_id}"),
        )
    except EmailPolicyError as exc:
        raise email_http_error(exc) from None
    except (CompletionError, TimeoutError):
        raise HTTPException(503, "email_model_unavailable") from None
    return await _draft_card(request, actor, client_id, registry, draft, send=send)


async def _team_email(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    thread_id: UUID,
    turn: WorkspaceTurnInput,
    proposal: EmailTeamProposal,
    facts: PlannerFacts,
    registry: ProposalRegistry,
) -> _Planned:
    """One email to the caller's own direct reports, all in To, read fresh from HR here."""
    service = _email_service(request)
    await require_package(request.app.state.pool, client_id, frozenset({"staff_email"}))
    team = await _my_team(request, actor, client_id, facts.packages)
    if team.reason is not None:
        text = _TEXT[facts.language][f"team_{team.reason}"]
        return _Planned(result=AnswerResult(action="answer", markdown=text))
    try:
        draft = await service.compose_team(
            email_session_from(request, actor, client_id, mutation=True),
            TeamComposeRequest(
                to_employee_ids=[person.employee_id for person in team.reports],
                subject=proposal.subject,
                instructions=proposal.instructions or proposal.subject,
                language=proposal.language,
            ),
            uuid5(turn.request_key, f"workspace_team:{thread_id}"),
        )
    except EmailPolicyError as exc:
        raise email_http_error(exc) from None
    except (CompletionError, TimeoutError):
        # The drafting call failed; planning failures stay PlannerUnavailable.
        raise HTTPException(503, "email_model_unavailable") from None
    send = _asked_to_send(proposal, turn.body, facts)
    prepared = await _draft_card(request, actor, client_id, registry, draft, send=send)
    if team.skipped:
        names = ", ".join(person.display_name for person in team.skipped)
        skipped = CardField(label="Not included", value=f"{names} (no work email)")
        content = replace(prepared.content, fields=[*prepared.content.fields, skipped])
        prepared = replace(prepared, content=content)
    return _Planned(prepared=prepared)


# Summary refusals the person can act on are answered in words, like the team refusals.
_SUMMARY_ANSWERS = {
    "mailbox_not_connected": "summary_mailbox_not_connected",
    "assistant_disabled": "summary_assistant_disabled",
    "permission_denied": "summary_permission_denied",
    "model_not_configured": "summary_unavailable",
    "email_model_unavailable": "summary_unavailable",
}


async def _summary(
    request: Request, actor: ConsoleActor, client_id: UUID, facts: PlannerFacts
) -> _Planned:
    """Answers "what needs my attention" from synced mail; may analyse five threads first."""
    service = _email_service(request)
    await require_package(request.app.state.pool, client_id, frozenset({"staff_email"}))
    session = email_session_from(request, actor, client_id, mutation=False)
    try:
        page = await service.attention(session)
    except EmailPolicyError as exc:
        key = _SUMMARY_ANSWERS.get(exc.code)
        if key is None:
            raise email_http_error(exc) from None
    except (CompletionError, TimeoutError):
        key = _SUMMARY_ANSWERS["email_model_unavailable"]
    else:
        if page.as_of is not None:
            return _Planned(summary=page)
        # Never synced (the mailbox row's last-sync time is null). Sync needs a connected
        # mailbox, so a pending or revoked row is asked to connect instead.
        mailbox = (await service.context(session)).mailbox
        connected = mailbox is not None and mailbox.status == "connected"
        key = "summary_never_synced" if connected else "summary_mailbox_not_connected"
    # Fixed copy, no mail content, so the turn row may store it as it is.
    return _Planned(result=AnswerResult(action="answer", markdown=_TEXT[facts.language][key]))


_MYT = ZoneInfo("Asia/Kuala_Lumpur")


def _plain(text: str, limit: int) -> str:
    """Untrusted mail text on one line, bounded; the console shows answers as plain text."""
    return " ".join(text.split())[:limit]


def _count(count: int, one: str, many: str, language: Literal["en", "ms"]) -> str:
    return f"{count} {_TEXT[language][one if count == 1 else many]}"


def _summary_markdown(page: AttentionPage, language: Literal["en", "ms"]) -> str:
    """The answer the user sees now; it is not stored (see ``_summary_stored``)."""
    text = _TEXT[language]
    lines: list[str] = []
    if page.items:
        as_of = ""
        if page.as_of is not None:
            moment = page.as_of.astimezone(_MYT)
            as_of = text["summary_as_of"].format(time=f"{moment.day} {moment:%b %Y, %H:%M}")
        lines.append(text["summary_head"].format(as_of=as_of))
        for index, item in enumerate(page.items, start=1):
            source = text["summary_from"].format(
                sender=_plain(item.sender, 200), priority=text[item.priority]
            )
            lines.append(f"{index}. {_plain(item.subject, 200)} ({source})")
            if item.summary.strip():
                lines.append(f"   {_plain(item.summary, 600)}")
            steps = "; ".join(_plain(step, 200) for step in item.next_steps if step.strip())
            if steps:
                lines.append(f"   {text['summary_next'].format(steps=steps)}")
    else:
        lines.append(text["summary_none"])
    if page.skipped:
        count = _count(page.skipped, "email", "emails", language)
        lines.append(text["summary_skipped"].format(count=count))
    return "\n".join(lines)


def _summary_stored(page: AttentionPage, language: Literal["en", "ms"]) -> AnswerResult:
    """Counts only: turns are readable tenant-wide, mail content is owner-only."""
    count = _count(len(page.items), "item", "items", language)
    return AnswerResult(
        action="answer", markdown=_TEXT[language]["summary_stored"].format(count=count)
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
        # Same gate and throttle as GET /workspace/people; without hr the assistant says so.
        if not _unlocked(facts.packages, _TAB_GATES["hr"]):
            text = _TEXT[facts.language]["people_unavailable"]
            return _Planned(result=AnswerResult(action="answer", markdown=text))
        query = proposal.query.strip()
        if not query:
            raise PlannerInvalidOutput()  # a blank pattern would list the whole directory
        throttle_lookup(actor.session_id)
        return _Planned(lookup=query)
    if isinstance(proposal, ClarificationProposal):
        live, stored = _clarification(proposal)
        return _Planned(result=live, stored=stored)
    if isinstance(proposal, TrainingSubmitProposal | InboxDecisionProposal):
        prepared = await registry.prepare(
            request, actor, client_id, proposal.action, _payload(proposal), origin="assistant"
        )
        return _Planned(prepared=prepared)
    if isinstance(proposal, EmailComposeProposal):
        _check_recipients(proposal, turn.body, facts)
        send = _asked_to_send(proposal, turn.body, facts)
        prepared = await _compose(
            request, actor, client_id, thread_id, turn, proposal, registry, send=send
        )
        return _Planned(prepared=prepared)
    if isinstance(proposal, EmailReplyProposal):
        _check_recipients(proposal, turn.body, facts)
        send = _asked_to_send(proposal, turn.body, facts)
        prepared = await _reply(
            request, actor, client_id, thread_id, turn, proposal, registry, send=send
        )
        return _Planned(prepared=prepared)
    if isinstance(proposal, EmailTeamProposal):
        return await _team_email(
            request, actor, client_id, thread_id, turn, proposal, facts, registry
        )
    if isinstance(proposal, InboxSummaryProposal):
        return await _summary(request, actor, client_id, facts)
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
    if planned.summary is not None:
        return AnswerResult(
            action="answer", markdown=_summary_markdown(planned.summary, facts.language)
        )
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
    facts = await planner_facts(request, actor, client_id, thread_id, turn)
    proposal = await _plan(planner, turn.body, facts)
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
        # The inbox summary is shown once and stored as counts only; an answer about an open
        # draft is stored without its subject.
        stored = planned.stored
        if planned.summary is not None:
            stored = _summary_stored(planned.summary, facts.language)
        elif isinstance(result, AnswerResult):
            stored = _stored_answer(result)
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
            stored=stored,
        )
