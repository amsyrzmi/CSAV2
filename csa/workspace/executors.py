"""Proposal executors: each one acts only through the service that owns its records.

``card()`` runs at create and again at confirm, so the package gate and the record read
are rechecked before anything executes. Services come from ``request.app.state`` at call
time. The registry holds no connection while an executor runs, so each step here borrows
at most one pool connection at a time. Idempotency keys derive from the proposal id, so a
confirm retried after the service committed replays inside the owning service instead of
acting twice.
"""

from __future__ import annotations

import hashlib
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
from csa.email.models import DraftOut, EmailPolicyError, draft_version
from csa.email.service import EmailService
from csa.hr.api import hr_http_error, hr_session_from
from csa.hr.models import TrainingAction, TrainingCommandInput, TrainingDraftInput, TrainingStatus
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
        try:
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
            draft = await self._service(request).draft_by_id(session, proposal.draft_id)
        except EmailPolicyError as exc:
            raise email_http_error(exc) from None
        # Bind the content the user sees; an edit makes the card stale, the export does not.
        version = hashlib.sha256(
            json.dumps(
                [draft.thread_id, draft.recipient, draft.subject, draft.body],
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        return (
            CardContent(
                title="Save email draft to Gmail",
                summary=(
                    f"Save the reply to {draft.recipient} in your Gmail drafts. Nothing is sent."
                ),
                fields=[
                    CardField(label="To", value=draft.recipient),
                    CardField(label="Subject", value=draft.subject),
                ],
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
        return draft.model_dump(mode="json")


_NOT_A_DRAFT = {
    "exported": "draft_already_exported",
    "sending": "draft_already_sending",
    "sent": "draft_already_sent",
}


def _email_refusal(exc: EmailPolicyError | GmailError) -> ProposalActionRefused:
    """The email route's refusal: its status, body (`retry_after`, `message`) and headers."""
    error = email_http_error(exc)
    return ProposalActionRefused(error.status_code, email_error_content(exc), error.headers)


def _people(count: int) -> str:
    return f"{count} person" if count == 1 else f"{count} people"


def send_card(draft: DraftOut) -> CardContent:
    """Counts only: the console reads recipients, subject and body from the draft itself.

    ``console_proposals`` rows are readable tenant-wide, so no address, subject or body is
    stored here; the owner's email session renders them when the card is shown.
    """
    everyone = {r.address.lower() for r in draft.recipients}
    outside = {r.address.lower() for r in draft.recipients if r.outside}
    # A reply whose thread was cleaned up after sync has no thread link: it is a plain email now.
    what = "reply" if draft.thread_id is not None and draft.reply_to_message_id else "email"
    title = f"Send {what} to {_people(len(everyone))}"
    if outside:
        title += f" ({len(outside)} outside your company)"
    counts = ", ".join(
        f"{n} in {label}"
        for label, n in (("To", len(draft.to)), ("CC", len(draft.cc)), ("BCC", len(draft.bcc)))
        if n
    )
    summary = f"{counts}. We send it from your Gmail when you select Send."
    if draft.needs_staff_input:
        summary += " Fill in the [STAFF: …] parts before sending."
    return CardContent(
        title=title,
        summary=summary,
        fields=[
            CardField(label="Subject", value="(shown on open)"),
            CardField(label="Draft", value=str(draft.id)),
        ],
    )


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
            draft = await self._service(request).draft_by_id(session, proposal.draft_id)
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
                raise HTTPException(404, "reply approval not found")
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
