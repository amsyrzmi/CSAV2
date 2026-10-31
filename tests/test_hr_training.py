"""Synthetic proposal semantics; no persistence, HTTP transport or provider calls."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import csa.hr.service as hr_service
import pytest
from csa.hr.api import hr_http_error
from csa.hr.assistant import HR_CHAT_SYSTEM
from csa.hr.models import (
    DirectoryRecord,
    DirectoryValues,
    EmployeeIdentity,
    EmployeeRef,
    GrantScope,
    HrAction,
    ResolvedGrant,
    TrainingAction,
    TrainingCommandInput,
    TrainingDecision,
    TrainingDraftInput,
    TrainingSnapshot,
    TrainingStatus,
    TrustedAccess,
)
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService
from csa.hr.training import project_training, propose_draft, propose_transition
from pydantic import SecretStr, ValidationError

TENANT = UUID(int=1)
EMPLOYEE = EmployeeRef(tenant_id=TENANT, employee_id=UUID(int=10))
MANAGER = EmployeeRef(tenant_id=TENANT, employee_id=UUID(int=11))
OUTSIDER = EmployeeRef(tenant_id=TENANT, employee_id=UUID(int=12))
STAMP = datetime(2026, 10, 21, 8, tzinfo=UTC)
CONTENT = TrainingDraftInput(title="Synthetic workshop", learning_objective="Practice planning")


def directory(manager: EmployeeRef | None = MANAGER) -> DirectoryRecord:
    return DirectoryRecord(
        identity=EmployeeIdentity(employee=EMPLOYEE),
        values=DirectoryValues(assigned_manager=manager),
    )


def access(
    action: HrAction,
    scope: GrantScope = GrantScope.SELF,
    employee: EmployeeRef | None = EMPLOYEE,
    *,
    owner: bool = False,
    tenant_id: UUID = TENANT,
) -> TrustedAccess:
    return TrustedAccess(
        tenant_id=tenant_id,
        subject_id=UUID(int=20),
        employee=employee,
        client_owner=owner,
        grants=(ResolvedGrant(action=action, scope=scope),),
    )


def snapshot(status: TrainingStatus = TrainingStatus.DRAFT, version: int = 0) -> TrainingSnapshot:
    decision = None
    if status in {TrainingStatus.APPROVED, TrainingStatus.REJECTED}:
        decision = TrainingDecision(
            subject_id=UUID(int=21),
            action=TrainingAction.APPROVE
            if status == TrainingStatus.APPROVED
            else TrainingAction.REJECT,
            note="Synthetic review",
        )
    return TrainingSnapshot(
        request_id=UUID(int=30),
        employee=EMPLOYEE,
        content=CONTENT,
        status=status,
        version=version,
        updated_at=STAMP,
        decision=decision,
    )


def command(action: TrainingAction, version: int = 0) -> TrainingCommandInput:
    return TrainingCommandInput(
        action=action,
        expected_version=version,
        decision_note="Synthetic reason" if action == TrainingAction.REJECT else None,
    )


def test_draft_is_only_a_proposal_without_request_id_or_persistence_claim() -> None:
    proposal = propose_draft(access(HrAction.TRAINING_DRAFT), directory(), CONTENT)
    assert proposal.kind == "draft_proposal"
    assert proposal.employee == EMPLOYEE
    assert proposal.content == CONTENT
    assert not {"request_id", "persisted", "approved"} & set(proposal.model_dump())


def test_draft_permission_is_required() -> None:
    with pytest.raises(HrPolicyError, match="permission_denied"):
        propose_draft(access(HrAction.TRAINING_READ), directory(), CONTENT)


@pytest.mark.parametrize(
    "status,action,expected",
    [
        (TrainingStatus.DRAFT, TrainingAction.SUBMIT, TrainingStatus.SUBMITTED),
        (TrainingStatus.DRAFT, TrainingAction.WITHDRAW, TrainingStatus.WITHDRAWN),
        (TrainingStatus.SUBMITTED, TrainingAction.WITHDRAW, TrainingStatus.WITHDRAWN),
        (TrainingStatus.SUBMITTED, TrainingAction.APPROVE, TrainingStatus.APPROVED),
        (TrainingStatus.SUBMITTED, TrainingAction.REJECT, TrainingStatus.REJECTED),
    ],
)
def test_valid_lifecycle_returns_candidate_state_without_mutating_snapshot(
    status: TrainingStatus, action: TrainingAction, expected: TrainingStatus
) -> None:
    current = snapshot(status, 3)
    before = current.model_dump_json()
    deciding = action in {TrainingAction.APPROVE, TrainingAction.REJECT}
    actor = (
        access(HrAction.TRAINING_DECIDE, GrantScope.TEAM, MANAGER)
        if deciding
        else access(
            HrAction.TRAINING_SUBMIT
            if action == TrainingAction.SUBMIT
            else HrAction.TRAINING_WITHDRAW
        )
    )
    proposal = propose_transition(
        actor, directory(), current, command(action, 3), now=lambda: STAMP + timedelta(seconds=1)
    )
    assert proposal.kind == "transition_proposal"
    assert proposal.after.status == expected
    assert proposal.after.version == 4
    assert proposal.after.updated_at == STAMP + timedelta(seconds=1)
    assert proposal.before == current
    assert current.model_dump_json() == before
    assert (proposal.after.decision is not None) == deciding


@pytest.mark.parametrize(
    "scope,employee,owner",
    [
        (GrantScope.TEAM, OUTSIDER, False),
        (GrantScope.TEAM, None, False),
        (GrantScope.CLIENT_OWNER, None, False),
    ],
)
def test_unassigned_manager_or_unverified_owner_cannot_decide(
    scope: GrantScope, employee: EmployeeRef | None, owner: bool
) -> None:
    actor = access(HrAction.TRAINING_DECIDE, scope, employee, owner=owner)
    with pytest.raises(HrPolicyError, match="permission_denied"):
        propose_transition(
            actor,
            directory(),
            snapshot(TrainingStatus.SUBMITTED),
            command(TrainingAction.APPROVE),
            now=lambda: STAMP,
        )


@pytest.mark.parametrize("manager", [MANAGER, EMPLOYEE])
def test_client_can_approve_own_request_with_assigned_manager(manager: EmployeeRef) -> None:
    actor = access(HrAction.TRAINING_DECIDE, GrantScope.CLIENT_OWNER, owner=True)
    proposal = propose_transition(
        actor,
        directory(manager),
        snapshot(TrainingStatus.SUBMITTED),
        command(TrainingAction.APPROVE),
        now=lambda: STAMP,
    )
    assert proposal.after.status == TrainingStatus.APPROVED


def test_client_without_employee_link_can_approve_assigned_request() -> None:
    actor = access(HrAction.TRAINING_DECIDE, GrantScope.CLIENT_OWNER, None, owner=True)
    proposal = propose_transition(
        actor,
        directory(),
        snapshot(TrainingStatus.SUBMITTED),
        command(TrainingAction.APPROVE),
        now=lambda: STAMP,
    )
    assert proposal.after.decision is not None
    assert proposal.after.decision.subject_id == actor.subject_id


def test_nonclient_cannot_approve_own_request_through_self_manager_relationship() -> None:
    actor = access(HrAction.TRAINING_DECIDE, GrantScope.TEAM)
    with pytest.raises(HrPolicyError, match="permission_denied"):
        propose_transition(
            actor,
            directory(EMPLOYEE),
            snapshot(TrainingStatus.SUBMITTED),
            command(TrainingAction.APPROVE),
            now=lambda: STAMP,
        )


def test_reassigned_manager_cannot_use_old_team_grant() -> None:
    actor = access(HrAction.TRAINING_DECIDE, GrantScope.TEAM, MANAGER)
    with pytest.raises(HrPolicyError, match="permission_denied"):
        propose_transition(
            actor,
            directory(OUTSIDER),
            snapshot(TrainingStatus.SUBMITTED),
            command(TrainingAction.APPROVE),
            now=lambda: STAMP,
        )


def test_submitted_request_cannot_be_submitted_again_at_current_version() -> None:
    with pytest.raises(HrPolicyError, match="invalid_transition"):
        propose_transition(
            access(HrAction.TRAINING_SUBMIT),
            directory(),
            snapshot(TrainingStatus.SUBMITTED, 1),
            command(TrainingAction.SUBMIT, 1),
            now=lambda: STAMP,
        )


@pytest.mark.parametrize("action", [TrainingAction.APPROVE, TrainingAction.REJECT])
def test_no_manager_blocks_even_client_decision(action: TrainingAction) -> None:
    actor = access(HrAction.TRAINING_DECIDE, GrantScope.CLIENT_OWNER, owner=True)
    with pytest.raises(HrPolicyError, match="permission_denied"):
        propose_transition(
            actor,
            directory(None),
            snapshot(TrainingStatus.SUBMITTED),
            command(action),
            now=lambda: STAMP,
        )


@pytest.mark.parametrize(
    "status", [TrainingStatus.APPROVED, TrainingStatus.REJECTED, TrainingStatus.WITHDRAWN]
)
@pytest.mark.parametrize("action", list(TrainingAction))
def test_terminal_requests_cannot_transition(
    status: TrainingStatus, action: TrainingAction
) -> None:
    deciding = action in {TrainingAction.APPROVE, TrainingAction.REJECT}
    actor = (
        access(HrAction.TRAINING_DECIDE, GrantScope.TEAM, MANAGER)
        if deciding
        else access(
            HrAction.TRAINING_SUBMIT
            if action == TrainingAction.SUBMIT
            else HrAction.TRAINING_WITHDRAW
        )
    )
    with pytest.raises(HrPolicyError, match="invalid_transition"):
        propose_transition(actor, directory(), snapshot(status), command(action), now=lambda: STAMP)


@pytest.mark.parametrize("action", [TrainingAction.APPROVE, TrainingAction.REJECT])
def test_cannot_decide_unsubmitted_draft(action: TrainingAction) -> None:
    with pytest.raises(HrPolicyError, match="invalid_transition"):
        propose_transition(
            access(HrAction.TRAINING_DECIDE, GrantScope.TEAM, MANAGER),
            directory(),
            snapshot(),
            command(action),
            now=lambda: STAMP,
        )


def test_stale_and_replayed_versions_denied() -> None:
    actor = access(HrAction.TRAINING_SUBMIT)
    proposal = propose_transition(
        actor, directory(), snapshot(), command(TrainingAction.SUBMIT), now=lambda: STAMP
    )
    with pytest.raises(HrPolicyError, match="stale_version"):
        propose_transition(
            actor, directory(), proposal.after, command(TrainingAction.SUBMIT), now=lambda: STAMP
        )
    # Re-evaluating the original facts is still only another proposal, not idempotent storage.
    again = propose_transition(
        actor, directory(), snapshot(), command(TrainingAction.SUBMIT), now=lambda: STAMP
    )
    assert again == proposal


@pytest.mark.parametrize(
    "stamp,code",
    [
        (STAMP - timedelta(seconds=1), "stale_clock"),
        (STAMP.replace(tzinfo=None), "aware_clock_required"),
    ],
)
def test_invalid_clock_denied(stamp: datetime, code: str) -> None:
    with pytest.raises(HrPolicyError, match=code):
        propose_transition(
            access(HrAction.TRAINING_SUBMIT),
            directory(),
            snapshot(),
            command(TrainingAction.SUBMIT),
            now=lambda: stamp,
        )


def test_version_exhaustion_denied() -> None:
    with pytest.raises(HrPolicyError, match="version_exhausted"):
        propose_transition(
            access(HrAction.TRAINING_SUBMIT),
            directory(),
            snapshot(version=2_147_483_647),
            command(TrainingAction.SUBMIT, 2_147_483_647),
            now=lambda: STAMP,
        )


def test_cross_tenant_transition_denied() -> None:
    actor = access(
        HrAction.TRAINING_DECIDE, GrantScope.CLIENT_OWNER, None, owner=True, tenant_id=UUID(int=2)
    )
    with pytest.raises(HrPolicyError, match="cross_tenant_reference"):
        propose_transition(
            actor,
            directory(),
            snapshot(TrainingStatus.SUBMITTED),
            command(TrainingAction.APPROVE),
            now=lambda: STAMP,
        )


def test_cross_tenant_training_read_denied() -> None:
    actor = access(HrAction.TRAINING_READ, GrantScope.HR, None, tenant_id=UUID(int=2))
    with pytest.raises(HrPolicyError, match="cross_tenant_reference"):
        project_training(actor, directory(), snapshot())


def test_request_employee_mismatch_denied() -> None:
    other = snapshot().model_copy(update={"employee": OUTSIDER})
    with pytest.raises(HrPolicyError, match="request_employee_mismatch"):
        propose_transition(
            access(HrAction.TRAINING_SUBMIT),
            directory(),
            other,
            command(TrainingAction.SUBMIT),
            now=lambda: STAMP,
        )


def test_hr_operator_has_no_decision_grant() -> None:
    with pytest.raises(ValidationError, match="unsupported_grant"):
        access(HrAction.TRAINING_DECIDE, GrantScope.HR, None)


@pytest.mark.parametrize(
    "scope,employee",
    [
        (GrantScope.SELF, EMPLOYEE),
        (GrantScope.TEAM, MANAGER),
        (GrantScope.HR, None),
    ],
)
def test_training_view_requires_scoped_read_and_omits_decision_identity(
    scope: GrantScope, employee: EmployeeRef | None
) -> None:
    view = project_training(
        access(HrAction.TRAINING_READ, scope, employee),
        directory(),
        snapshot(TrainingStatus.APPROVED),
    )
    assert view.status == TrainingStatus.APPROVED
    assert not {"decision", "subject_id", "note"} & set(view.model_dump())


def test_training_content_not_returned_without_read_grant() -> None:
    with pytest.raises(HrPolicyError, match="permission_denied"):
        project_training(access(HrAction.TRAINING_SUBMIT), directory(), snapshot())


async def test_save_draft_without_link_is_no_employee_link(monkeypatch) -> None:
    """A caller with no employee record of their own gets a distinct code, not a 404."""

    @asynccontextmanager
    async def no_db(_pool, _tenant):
        yield object()

    async def unlinked(_conn, _session):
        return access(HrAction.TRAINING_DRAFT, employee=None)

    async def no_receipt(_conn, _session, _key):
        return None

    monkeypatch.setattr(hr_service, "tx", no_db)
    service = HrService(object(), session_idle_seconds=1800)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_access", unlinked)
    monkeypatch.setattr(service, "_key", no_receipt)
    session = HrSession(
        session_id=UUID(int=40),
        subject_id=UUID(int=20),
        tenant_id=TENANT,
        token_hash=SecretStr("synthetic"),
        csrf_hash=SecretStr("synthetic"),
    )
    with pytest.raises(HrPolicyError) as refused:
        await service.save_draft(session, CONTENT, UUID(int=50))
    assert refused.value.code == "no_employee_link"
    error = hr_http_error(refused.value)
    assert (error.status_code, error.detail) == (409, "no_employee_link")


def test_hr_chat_prompt_states_the_wrapper() -> None:
    wrapper = (
        'Return one JSON object: {"action": <one tool call object> or null, "clarification": '
        '"request_details" | "manual_decision_required" | "unsupported_request" or null}; '
        "exactly one of them is non-null."
    )
    assert wrapper in HR_CHAT_SYSTEM
