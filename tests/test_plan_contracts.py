"""Contracts for the plan proposal and the batch email action (migration 0542 is tested in
tests/test_bookings_bot_schema.py)."""

from __future__ import annotations

import typing

import pytest
from csa.workspace.contracts import (
    EmailBatchProposal,
    PlanProposal,
    PlanProposalPayload,
    PlanStep,
    ProposalKind,
    RecipientSpec,
)
from csa.workspace.planner import PlannerOutput
from pydantic import ValidationError

ALLOWED_KINDS = {
    "email_send",
    "email_export",
    "booking_action",
    "inbox_decision",
    "training_submit",
    "hr_person",
}


def _step(kind: str = "email_export") -> dict[str, object]:
    return {"kind": kind, "payload": {"draft_id": "x"}, "version": None}


def _batch(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "action": "email_batch",
        "recipients": [{"kind": "address", "value": "a@example.test"}],
        "subject": "Hello",
        "instructions": "",
        "language": "en",
    }
    return {**base, **changes}


def test_plan_is_a_proposal_kind_and_steps_allow_only_the_six_kinds() -> None:
    assert "plan" in typing.get_args(ProposalKind)
    assert set(typing.get_args(PlanStep.model_fields["kind"].annotation)) == ALLOWED_KINDS
    for kind in sorted(ALLOWED_KINDS):
        PlanProposalPayload.model_validate({"steps": [_step(kind)]})


@pytest.mark.parametrize("kind", ["plan", "subscription_action", "email_batch", "nonsense"])
def test_plan_payload_rejects_plan_and_subscription_action_steps(kind: str) -> None:
    with pytest.raises(ValidationError):
        PlanProposalPayload.model_validate({"steps": [_step(kind)]})


def test_plan_payload_rejects_zero_and_twenty_one_steps() -> None:
    with pytest.raises(ValidationError):
        PlanProposalPayload.model_validate({"steps": []})
    with pytest.raises(ValidationError):
        PlanProposalPayload.model_validate({"steps": [_step() for _ in range(21)]})
    assert len(PlanProposalPayload.model_validate({"steps": [_step()] * 20}).steps) == 20
    assert PlanProposalPayload.model_validate({"steps": [_step()]}).steps[0].version is None


def test_email_batch_requires_a_recipient_and_caps_at_forty() -> None:
    with pytest.raises(ValidationError):
        EmailBatchProposal.model_validate(_batch(recipients=[]))
    one = {"kind": "customer", "value": "Some Customer"}
    with pytest.raises(ValidationError):
        EmailBatchProposal.model_validate(_batch(recipients=[one] * 41))
    forty = EmailBatchProposal.model_validate(_batch(recipients=[one] * 40))
    assert len(forty.recipients) == 40
    assert forty.send is False and forty.offset == 0
    with pytest.raises(ValidationError):
        EmailBatchProposal.model_validate(_batch(offset=-1))


def test_group_recipient_needs_group_and_renewing_soon_needs_days() -> None:
    with pytest.raises(ValidationError):
        RecipientSpec.model_validate({"kind": "group", "value": ""})
    with pytest.raises(ValidationError):
        RecipientSpec.model_validate({"kind": "group", "value": "", "group": "renewing_soon"})
    with pytest.raises(ValidationError):
        RecipientSpec.model_validate(
            {"kind": "group", "value": "", "group": "renewing_soon", "days": 0}
        )
    active = RecipientSpec.model_validate({"kind": "group", "group": "active_subscribers"})
    assert active.days is None
    soon = RecipientSpec.model_validate({"kind": "group", "group": "renewing_soon", "days": 14})
    assert soon.days == 14
    with pytest.raises(ValidationError):
        RecipientSpec.model_validate({"kind": "customer", "value": ""})
    with pytest.raises(ValidationError):
        RecipientSpec.model_validate({"kind": "someone_else", "value": "x"})


def test_planner_output_accepts_email_batch_and_plan() -> None:
    batch = PlannerOutput.model_validate({"proposal": _batch(send=True)})
    assert isinstance(batch.proposal, EmailBatchProposal) and batch.proposal.send is True
    plan = PlannerOutput.model_validate(
        {
            "proposal": {
                "action": "plan",
                "steps": [
                    _batch(),
                    {
                        "action": "training_submit",
                        "title": "Safety",
                        "learning_objective": "Learn the safety rules",
                    },
                ],
            }
        }
    )
    assert isinstance(plan.proposal, PlanProposal)
    assert [type(s).__name__ for s in plan.proposal.steps] == [
        "EmailBatchProposal",
        "TrainingSubmitProposal",
    ]


def test_a_plan_cannot_hold_a_plan_or_an_unlisted_action() -> None:
    inner = {"action": "plan", "steps": [_batch()]}
    for step in (
        inner,
        {"action": "email_compose", "recipient_address": "a@example.test", "subject": "s"},
        {"action": "subscription_action"},
    ):
        with pytest.raises(ValidationError):
            PlannerOutput.model_validate({"proposal": {"action": "plan", "steps": [step]}})
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate({"proposal": {"action": "plan", "steps": []}})


def test_email_batch_mode_and_knowledge_base_are_optional_planner_hints() -> None:
    plain = EmailBatchProposal.model_validate(_batch())
    assert plain.mode is None and plain.use_knowledge_base is False
    shared = EmailBatchProposal.model_validate(_batch(mode="shared", use_knowledge_base=True))
    assert shared.mode == "shared" and shared.use_knowledge_base is True
    assert EmailBatchProposal.model_validate(_batch(mode="personalised")).mode == "personalised"
    with pytest.raises(ValidationError):
        EmailBatchProposal.model_validate(_batch(mode="both"))
