"""Proposal executors: each one acts only through the service that owns its records.

``card()`` runs at create and again at confirm, so the package gate and the record read
are rechecked before anything executes. Services come from ``request.app.state`` at call
time. The registry holds no connection while an executor runs, so each step here borrows
at most one pool connection at a time. Idempotency keys derive from the proposal id, so a
confirm retried after the service committed replays inside the owning service instead of
acting twice.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid5

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError

from csa.console.contracts import ConsoleActor
from csa.console.inbox import conversation, decide_approval
from csa.console.services import require_package
from csa.core import db
from csa.core.pii import redact
from csa.core.tenancy import tx
from csa.email.api import email_error_content, email_http_error, email_session_from
from csa.email.gmail import GmailError
from csa.email.models import DraftOut, EmailPolicyError, EmailSession, draft_version
from csa.email.service import EmailService
from csa.hr.api import hr_http_error, hr_session_from
from csa.hr.models import (
    HrAction,
    TrainingAction,
    TrainingCommandInput,
    TrainingDraftInput,
    TrainingStatus,
)
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService
from csa.subscriptions.executor import SubscriptionActionExecutor
from csa.workspace.contracts import (
    CardField,
    EmailExportProposal,
    EmailSendProposal,
    InboxDecisionProposal,
    ProposalKind,
    TrainingSubmitProposal,
)
from csa.workspace.registry import (
    CardContent,
    ProposalActionRefused,
    ProposalError,
    ProposalOrigin,
    ProposalRegistry,
)

_ACTIVE = frozenset({"active"})
_ACTIVE_OR_SUSPENDED = frozenset({"active", "suspended"})
# A new request is saved as a draft and then submitted, so it needs both grants: with only
# the draft grant the submit would be refused and leave the draft behind. The planner's
# my_employee fact uses the same set.
NEW_TRAINING_GRANTS = frozenset({HrAction.TRAINING_DRAFT.value, HrAction.TRAINING_SUBMIT.value})
_SUBMIT_GRANTS = frozenset({HrAction.TRAINING_SUBMIT.value})


def _parse[Model: BaseModel](
    model: type[Model], kind: ProposalKind, payload: dict[str, Any]
) -> Model:
    try:
        return model.model_validate_json(json.dumps({"action": kind, **payload}))
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(422, "invalid proposal payload") from None


def _key(proposal_id: UUID, step: str) -> UUID:
    return uuid5(proposal_id, step)


class TrainingSubmitExecutor:
    kind: ProposalKind = "training_submit"

    @staticmethod
    def _service(request: Request) -> HrService:
        service = getattr(request.app.state, "hr_service", None)
        if not isinstance(service, HrService):
            raise HTTPException(503, "hr_service_unavailable")
        return service

    @staticmethod
    def _session(
        request: Request, actor: ConsoleActor, client_id: UUID, origin: ProposalOrigin
    ) -> HrSession:
        # For assistant proposals, assistant=True makes HrService enforce the tenant's
        # hr_assistant switch on every call; a tab proposal is the user's own action.
        session = hr_session_from(request, actor, client_id, mutation=True)
        return session.model_copy(update={"assistant": origin == "assistant"})

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        await require_package(request.app.state.pool, client_id, frozenset({"hr"}), _ACTIVE)
        if origin != "assistant":
            return
        try:
            await self._service(request).require_capability(
                self._session(request, actor, client_id, origin), "hr_assistant"
            )
        except HrPolicyError as exc:
            raise hr_http_error(exc) from None

    @staticmethod
    async def _prerequisites(
        service: HrService, session: HrSession, needed: frozenset[str]
    ) -> None:
        """The caller's own employee link and training grants, checked when the card is made.

        ``context`` enforces the session (and ``hr_assistant`` for assistant proposals).
        """
        try:
            context = await service.context(session)
        except HrPolicyError as exc:
            if exc.code == "permission_denied":
                raise HTTPException(403, "hr_training_not_permitted") from None
            raise hr_http_error(exc) from None
        if context.employee is None:
            raise HTTPException(409, "no_employee_link")
        if not needed <= set(context.actions):
            raise HTTPException(403, "hr_training_not_permitted")

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
    ) -> tuple[CardContent, str | None]:
        proposal = _parse(TrainingSubmitProposal, self.kind, payload)
        await self.gate(request, actor, client_id, origin=origin)
        service = self._service(request)
        session = self._session(request, actor, client_id, origin)
        new = proposal.request_id is None
        await self._prerequisites(service, session, NEW_TRAINING_GRANTS if new else _SUBMIT_GRANTS)
        if proposal.request_id is None:
            assert proposal.title is not None and proposal.learning_objective is not None
            return (
                CardContent(
                    title="Submit a new training request",
                    summary=f"Create and submit “{proposal.title}” for approval.",
                    fields=[
                        CardField(label="Title", value=proposal.title),
                        CardField(label="Objective", value=proposal.learning_objective),
                    ],
                ),
                None,
            )
        try:
            projection = await service.training(session, proposal.request_id)
        except HrPolicyError as exc:
            raise hr_http_error(exc) from None
        if projection.status != TrainingStatus.DRAFT:
            raise HTTPException(409, "training_not_draft")
        return (
            CardContent(
                title="Submit training request",
                summary=f"Submit “{projection.content.title}” for approval.",
                fields=[
                    CardField(label="Title", value=projection.content.title),
                    CardField(label="Objective", value=projection.content.learning_objective),
                ],
            ),
            str(projection.version),
        )

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
        proposal = _parse(TrainingSubmitProposal, self.kind, payload)
        service = self._service(request)
        session = self._session(request, actor, client_id, origin)
        try:
            if proposal.request_id is None:
                assert proposal.title is not None and proposal.learning_objective is not None
                saved = await service.save_draft(
                    session,
                    TrainingDraftInput(
                        title=proposal.title, learning_objective=proposal.learning_objective
                    ),
                    _key(proposal_id, "training_draft"),
                )
                request_id, version = saved.request.request_id, saved.request.version
            else:
                if expected_version is None:
                    raise RuntimeError("existing training request proposal has no bound version")
                request_id, version = proposal.request_id, int(expected_version)
            receipt = await service.transition(
                session,
                request_id,
                TrainingCommandInput(action=TrainingAction.SUBMIT, expected_version=version),
                _key(proposal_id, "training_submit"),
            )
        except HrPolicyError as exc:
            raise hr_http_error(exc) from None
        return receipt.model_dump(mode="json")


_NOT_A_DRAFT = {
    "exported": "draft_already_exported",
    "sending": "draft_already_sending",
    "sent": "draft_already_sent",
}


def _email_refusal(exc: EmailPolicyError | GmailError) -> ProposalActionRefused:
    """The email route's refusal: its status, body (`retry_after`, `message`) and headers."""
    error = email_http_error(exc)
    return ProposalActionRefused(error.status_code, email_error_content(exc), error.headers)


async def _connected_draft(
    service: EmailService, session: EmailSession, draft_id: UUID
) -> DraftOut:
    """The caller's draft, refused with ``mailbox_not_connected`` while their mailbox isn't.

    Checked when the card is made, so the user learns it now and not at Confirm.
    """
    draft = await service.draft_by_id(session, draft_id)
    mailbox = (await service.context(session)).mailbox
    if mailbox is None or mailbox.status != "connected":
        raise EmailPolicyError("mailbox_not_connected")
    return draft


def _people(count: int) -> str:
    return f"{count} person" if count == 1 else f"{count} people"


def _audience(draft: DraftOut) -> tuple[str, str]:
    """("3 people (1 outside your company)", "2 in To, 1 in CC"): counts, never addresses."""
    everyone = {r.address.lower() for r in draft.recipients}
    outside = {r.address.lower() for r in draft.recipients if r.outside}
    audience = _people(len(everyone))
    if outside:
        audience += f" ({len(outside)} outside your company)"
    counts = ", ".join(
        f"{n} in {label}"
        for label, n in (("To", len(draft.to)), ("CC", len(draft.cc)), ("BCC", len(draft.bcc)))
        if n
    )
    return audience, counts


def _content_fields(draft: DraftOut) -> list[CardField]:
    return [
        CardField(label="Subject", value="(shown on open)"),
        CardField(label="Draft", value=str(draft.id)),
    ]


def send_card(draft: DraftOut) -> CardContent:
    """Counts only: the console reads recipients, subject and body from the draft itself.

    ``console_proposals`` rows are readable tenant-wide, so no address, subject or body is
    stored here; the owner's email session renders them when the card is shown.
    """
    # A reply whose thread was cleaned up after sync has no thread link: it is a plain email now.
    what = "reply" if draft.thread_id is not None and draft.reply_to_message_id else "email"
    audience, counts = _audience(draft)
    summary = f"{counts}. We send it from your Gmail when you select Send."
    if draft.needs_staff_input:
        summary += " Fill in the [STAFF: …] parts before sending."
    return CardContent(
        title=f"Send {what} to {audience}", summary=summary, fields=_content_fields(draft)
    )


class EmailExportExecutor:
    kind: ProposalKind = "email_export"

    @staticmethod
    def _service(request: Request) -> EmailService:
        service = getattr(request.app.state, "email_service", None)
        if not isinstance(service, EmailService):
            raise HTTPException(503, "email_assistant_not_configured")
        return service

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        await require_package(
            request.app.state.pool, client_id, frozenset({"staff_email"}), _ACTIVE
        )

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
    ) -> tuple[CardContent, str | None]:
        proposal = _parse(EmailExportProposal, self.kind, payload)
        await self.gate(request, actor, client_id, origin=origin)
        session = email_session_from(request, actor, client_id, mutation=True)
        try:
            draft = await _connected_draft(self._service(request), session, proposal.draft_id)
        except EmailPolicyError as exc:
            raise email_http_error(exc) from None
        # Bound to the whole draft the user sees (To, CC, BCC, subject, body), as a send card
        # is: any edit makes the card stale, the export does not.
        version = draft_version(draft)
        # Counts only, as on the send card: the console reads the draft itself when it shows it.
        audience, counts = _audience(draft)
        return (
            CardContent(
                title=f"Save email to Gmail drafts for {audience}",
                summary=f"{counts}. Nothing is sent.",
                fields=_content_fields(draft),
            ),
            version,
        )

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
        proposal = _parse(EmailExportProposal, self.kind, payload)
        session = email_session_from(request, actor, client_id, mutation=True)
        try:
            draft = await self._service(request).export_draft(
                session, proposal.draft_id, _key(proposal_id, "email_export")
            )
        except (EmailPolicyError, GmailError) as exc:
            raise email_http_error(exc) from None
        # Stored on the proposal row, which the tenant can read: no address, subject or body.
        return {
            "draft_id": str(draft.id),
            "status": draft.status,
            "gmail_draft_id": draft.gmail_draft_id,
        }


class EmailSendExecutor:
    """Sends one of the caller's drafts from their own Gmail, at most once, after the card.

    The payload is only ``draft_id`` and ``version`` (``draft_version`` at card time). Any edit
    after the card was made changes the version, so confirming refuses with ``draft_changed``
    and nothing is sent until a new card is confirmed.
    """

    kind: ProposalKind = "email_send"

    @staticmethod
    def _service(request: Request) -> EmailService:
        service = getattr(request.app.state, "email_service", None)
        if not isinstance(service, EmailService):
            raise HTTPException(503, "email_assistant_not_configured")
        return service

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        await require_package(
            request.app.state.pool, client_id, frozenset({"staff_email"}), _ACTIVE
        )

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
    ) -> tuple[CardContent, str | None]:
        proposal = _parse(EmailSendProposal, self.kind, payload)
        await self.gate(request, actor, client_id, origin=origin)
        session = email_session_from(request, actor, client_id, mutation=True)
        try:
            draft = await _connected_draft(self._service(request), session, proposal.draft_id)
            if draft.status != "draft":
                raise EmailPolicyError(_NOT_A_DRAFT[draft.status])
            version = draft_version(draft)
            if version != proposal.version:
                raise EmailPolicyError("draft_changed")
        except EmailPolicyError as exc:
            raise _email_refusal(exc) from None
        return send_card(draft), version

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
        proposal = _parse(EmailSendProposal, self.kind, payload)
        if expected_version != proposal.version:
            raise RuntimeError("email send proposal is not bound to its version")
        session = email_session_from(request, actor, client_id, mutation=True)
        try:
            result = await self._service(request).send_draft(
                session, proposal.draft_id, proposal.version, _key(proposal_id, "email_send")
            )
        except (EmailPolicyError, GmailError) as exc:
            raise _email_refusal(exc) from None
        # Outcome, time and Gmail's message id only: never an address, subject or body.
        return {"draft_id": str(proposal.draft_id), **result.model_dump(mode="json")}


class InboxDecisionExecutor:
    kind: ProposalKind = "inbox_decision"

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        await require_package(
            request.app.state.pool, client_id, frozenset({"customer_support"}), _ACTIVE_OR_SUSPENDED
        )

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
    ) -> tuple[CardContent, str | None]:
        proposal = _parse(InboxDecisionProposal, self.kind, payload)
        await self.gate(request, actor, client_id, origin=origin)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            approval = await db.q.fetch_one(
                "inbox/get_approval.sql", (client_id, proposal.approval_id), conn=conn
            )
            if approval is None or approval["conversation_id"] != proposal.conversation_id:
                raise HTTPException(404, "reply_approval_not_found")
            current = await conversation(conn, client_id, proposal.conversation_id)
        if current.inbox_version != proposal.expected_version:
            raise ProposalError("proposal_stale")
        verb = "Approve" if proposal.decision == "approve" else "Reject"
        return (
            CardContent(
                title=f"{verb} held reply",
                summary=f"{verb} the held reply to this customer conversation.",
                fields=[
                    CardField(label="Decision", value=proposal.decision),
                    CardField(label="Reply", value=redact(approval["draft"])[:500]),
                ],
            ),
            str(current.inbox_version),
        )

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
        proposal = _parse(InboxDecisionProposal, self.kind, payload)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            result = await decide_approval(
                conn,
                client_id,
                actor,
                proposal.approval_id,
                proposal.decision,
                None,
                _key(proposal_id, "inbox_decision"),
            )
        return result.model_dump(mode="json")


def build_proposal_registry() -> ProposalRegistry:
    return ProposalRegistry(
        {
            "training_submit": TrainingSubmitExecutor(),
            "email_export": EmailExportExecutor(),
            "email_send": EmailSendExecutor(),
            "inbox_decision": InboxDecisionExecutor(),
            "subscription_action": SubscriptionActionExecutor(),
        }
    )
