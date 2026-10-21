"""Preparation and lifecycle proposals only. No storage, provider or reply approvals."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from csa.hr.models import (
    DirectoryRecord,
    HrAction,
    TrainingAction,
    TrainingCommandInput,
    TrainingDecision,
    TrainingDraftInput,
    TrainingDraftProposal,
    TrainingProjection,
    TrainingSnapshot,
    TrainingStatus,
    TrainingTransitionProposal,
    TrustedAccess,
)
from csa.hr.policy import HrPolicyError, matching_grants


def propose_draft(
    access: TrustedAccess, record: DirectoryRecord, content: TrainingDraftInput
) -> TrainingDraftProposal:
    matching_grants(access, HrAction.TRAINING_DRAFT, record)
    return TrainingDraftProposal(employee=record.identity.employee, content=content)


def project_training(
    access: TrustedAccess, record: DirectoryRecord, current: TrainingSnapshot
) -> TrainingProjection:
    if current.employee != record.identity.employee:
        raise HrPolicyError("request_employee_mismatch")
    matching_grants(access, HrAction.TRAINING_READ, record)
    return TrainingProjection.model_validate(current.model_dump(exclude={"decision"}))


def propose_transition(
    access: TrustedAccess,
    record: DirectoryRecord,
    current: TrainingSnapshot,
    command: TrainingCommandInput,
    *,
    now: Callable[[], datetime],
) -> TrainingTransitionProposal:
    """Caller must load current facts/grants and later persist with version CAS.

    Client-owner approval (including own requests) requires an assigned manager.
    No HR-operator fallback, automatic approval, expiry or implicit persistence.
    """
    if current.employee != record.identity.employee:
        raise HrPolicyError("request_employee_mismatch")
    action = {
        TrainingAction.SUBMIT: HrAction.TRAINING_SUBMIT,
        TrainingAction.APPROVE: HrAction.TRAINING_DECIDE,
        TrainingAction.REJECT: HrAction.TRAINING_DECIDE,
        TrainingAction.WITHDRAW: HrAction.TRAINING_WITHDRAW,
    }[command.action]
    matching_grants(access, action, record)
    if command.expected_version != current.version:
        raise HrPolicyError("stale_version")
    allowed = {
        (TrainingStatus.DRAFT, TrainingAction.SUBMIT): TrainingStatus.SUBMITTED,
        (TrainingStatus.DRAFT, TrainingAction.WITHDRAW): TrainingStatus.WITHDRAWN,
        (TrainingStatus.SUBMITTED, TrainingAction.WITHDRAW): TrainingStatus.WITHDRAWN,
        (TrainingStatus.SUBMITTED, TrainingAction.APPROVE): TrainingStatus.APPROVED,
        (TrainingStatus.SUBMITTED, TrainingAction.REJECT): TrainingStatus.REJECTED,
    }
    status = allowed.get((current.status, command.action))
    if status is None:
        raise HrPolicyError("invalid_transition")
    timestamp = now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise HrPolicyError("aware_clock_required")
    if timestamp < current.updated_at:
        raise HrPolicyError("stale_clock")
    if current.version == 2_147_483_647:
        raise HrPolicyError("version_exhausted")
    decision = None
    if command.action in {TrainingAction.APPROVE, TrainingAction.REJECT}:
        decision = TrainingDecision(
            subject_id=access.subject_id,
            action=(
                TrainingAction.APPROVE
                if command.action == TrainingAction.APPROVE
                else TrainingAction.REJECT
            ),
            note=command.decision_note,
        )
    after = TrainingSnapshot(
        request_id=current.request_id,
        employee=current.employee,
        content=current.content,
        status=status,
        version=current.version + 1,
        updated_at=timestamp,
        decision=decision,
    )
    return TrainingTransitionProposal(before=current, after=after)
