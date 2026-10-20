"""Synthetic offline count contracts; no identity or database access."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from csa.inventory.contracts import (
    InventoryAccountKey,
    StockChangeAuditEvent,
    StockChangeCommand,
    StockChangeDecision,
    StockChangeRecord,
    TenantAdminEvidence,
    reload_tenant_admin,
    require_exact_stock_change_replay,
)
from pydantic import ValidationError


def _id(number: int) -> UUID:
    return UUID(int=number)


def _actor(user: int = 4, tenant: int = 1) -> TenantAdminEvidence:
    return TenantAdminEvidence(
        client_id=_id(tenant),
        user_id=_id(user),
        session_id=_id(5),
        role="tenant_admin",
        verified_at=datetime(2026, 10, 19, tzinfo=UTC),
    )


def _command() -> StockChangeCommand:
    return StockChangeCommand(
        command_id=_id(6),
        account=InventoryAccountKey(client_id=_id(1), product_id=_id(2), warehouse_id=_id(3)),
        submitter=_actor(),
        kind="physical_count",
        reason="Synthetic count",
        expected_version=0,
        expected_authority_revision=1,
        before_on_hand=5,
        value=3,
    )


def test_roundtrip_and_session_independent_canonical_replay() -> None:
    command = _command()
    restored = StockChangeCommand.model_validate_json(command.model_dump_json())
    assert restored == command
    assert len(command.fingerprint()) == 64
    data = command.model_dump()
    data["submitter"] = _actor().model_dump() | {
        "session_id": _id(99),
        "verified_at": datetime(2026, 10, 20, tzinfo=UTC),
    }
    data["reason"] = "  Synthetic count  "
    renewed = StockChangeCommand.model_validate(data)
    require_exact_stock_change_replay(command, renewed)
    assert renewed.fingerprint() == restored.fingerprint()
    with pytest.raises(ValidationError):
        command.value = 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_id", _id(99)),
        ("kind", "manual_adjustment"),
        ("reason", "Other reason"),
        ("expected_version", 1),
        ("expected_authority_revision", 2),
        ("before_on_hand", 6),
        ("value", 4),
    ],
)
def test_each_business_field_is_bound_to_replay(field: str, value: object) -> None:
    data = _command().model_dump()
    data[field] = value
    candidate = StockChangeCommand.model_validate(data)
    with pytest.raises(ValueError, match="replay differs"):
        require_exact_stock_change_replay(_command(), candidate)


@pytest.mark.parametrize("field", ["client_id", "product_id", "warehouse_id"])
def test_account_scope_is_bound_to_fingerprint(field: str) -> None:
    data = _command().model_dump()
    data["account"][field] = _id(99)
    if field == "client_id":
        data["submitter"]["client_id"] = _id(99)
    candidate = StockChangeCommand.model_validate(data)
    assert candidate.fingerprint() != _command().fingerprint()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("value", True),
        ("value", "3"),
        ("value", 3.0),
        ("value", -1),
        ("before_on_hand", -1),
        ("expected_version", True),
        ("expected_version", -1),
        ("expected_authority_revision", 0),
        ("reason", " \n "),
        ("reason", "x" * 501),
        ("unexpected", "not accepted"),
    ],
)
def test_invalid_command_contract_is_rejected(field: str, value: object) -> None:
    data = _command().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        StockChangeCommand.model_validate(data)


def test_actor_tenant_role_time_and_identity_are_not_caller_defaults() -> None:
    data = _command().model_dump()
    data["submitter"]["client_id"] = _id(9)
    with pytest.raises(ValidationError, match="tenant"):
        StockChangeCommand.model_validate(data)
    for patch in [
        {"role": "tenant_staff"},
        {"verified_at": datetime(2026, 10, 19)},
        {"session_id": None},
    ]:
        with pytest.raises(ValidationError):
            TenantAdminEvidence.model_validate(_actor().model_dump() | patch)
    data = _command().model_dump()
    data["submitter"]["user_id"] = _id(9)
    assert StockChangeCommand.model_validate(data).fingerprint() != _command().fingerprint()
    offset = _actor().model_dump() | {
        "verified_at": datetime.fromisoformat("2026-10-19T08:00:00+08:00")
    }
    assert TenantAdminEvidence.model_validate(offset).verified_at == _actor().verified_at


@pytest.mark.parametrize("outcome", ["approved", "rejected"])
def test_review_requires_distinct_same_tenant_admin(outcome: str) -> None:
    data = {
        "command": _command(),
        "reviewer": _actor(9),
        "outcome": outcome,
        "reason": "Synthetic review",
    }
    assert StockChangeDecision.model_validate(data).reviewer.user_id == _id(9)
    for actor in [_actor(), _actor(9, tenant=8)]:
        with pytest.raises(ValidationError):
            StockChangeDecision.model_validate(data | {"reviewer": actor})


@pytest.mark.parametrize(
    ("kind", "value", "proposed", "review"),
    [
        ("physical_count", 5, 5, False),
        ("manual_adjustment", 0, 5, False),
        ("manual_adjustment", -5, 0, True),
        ("manual_adjustment", 2, 7, True),
    ],
)
def test_zero_and_nonzero_changes_have_explicit_review_requirement(
    kind: str, value: int, proposed: int, review: bool
) -> None:
    command = StockChangeCommand.model_validate(
        _command().model_dump() | {"kind": kind, "value": value}
    )
    assert command.proposed_on_hand == proposed
    assert command.requires_second_admin is review
    if not review:
        with pytest.raises(ValidationError, match="without review"):
            StockChangeDecision(
                command=command, reviewer=_actor(9), outcome="approved", reason="Synthetic"
            )


def test_adjustment_cannot_underflow_or_overflow_quantity() -> None:
    for before, delta in [(5, -6), (2_147_483_647, 1)]:
        with pytest.raises(ValidationError, match="quantity range"):
            StockChangeCommand.model_validate(
                _command().model_dump()
                | {"kind": "manual_adjustment", "before_on_hand": before, "value": delta}
            )


def test_audit_envelope_roundtrips_and_covers_request_approval_and_stale_rejection() -> None:
    command = _command()
    submit = StockChangeAuditEvent(
        event_id=_id(20),
        command=command,
        kind="pending",
        actor=_actor(),
        occurred_at=datetime(2026, 10, 19, 1, tzinfo=UTC),
        fingerprint=command.fingerprint(),
        observed_version=0,
        observed_authority_revision=1,
        before_on_hand=5,
        after_on_hand=5,
        resulting_version=0,
    )
    assert StockChangeAuditEvent.model_validate_json(submit.model_dump_json()) == submit
    reviewer = _actor(9)
    decision = StockChangeDecision(
        command=command, reviewer=reviewer, outcome="approved", reason="Count checked"
    )
    approved = StockChangeAuditEvent(
        event_id=_id(21),
        command=command,
        kind="approved",
        actor=reviewer,
        occurred_at=datetime(2026, 10, 19, 2, tzinfo=UTC),
        fingerprint=command.fingerprint(),
        observed_version=0,
        observed_authority_revision=1,
        before_on_hand=5,
        after_on_hand=3,
        resulting_version=1,
        decision=decision,
    )
    assert StockChangeAuditEvent.model_validate_json(approved.model_dump_json()) == approved
    rejected_decision = StockChangeDecision(
        command=command, reviewer=reviewer, outcome="rejected", reason="Rescan required"
    )
    stale_rejection = StockChangeAuditEvent(
        event_id=_id(22),
        command=command,
        kind="rejected",
        actor=reviewer,
        occurred_at=datetime(2026, 10, 19, 3, tzinfo=UTC),
        fingerprint=command.fingerprint(),
        observed_version=2,
        observed_authority_revision=3,
        before_on_hand=4,
        after_on_hand=4,
        resulting_version=2,
        decision=rejected_decision,
    )
    assert StockChangeAuditEvent.model_validate_json(stale_rejection.model_dump_json()) == (
        stale_rejection
    )
    invalid_events = [
        submit.model_dump() | {"fingerprint": "0" * 64},
        submit.model_dump() | {"after_on_hand": 4},
        submit.model_dump() | {"resulting_version": 2},
        submit.model_dump()
        | {
            "actor": _actor(tenant=9),
        },
        submit.model_dump()
        | {
            "occurred_at": datetime(2026, 10, 18, 23, tzinfo=UTC),
        },
        submit.model_dump() | {"kind": "recorded"},
    ]
    for event in invalid_events:
        with pytest.raises(ValidationError):
            StockChangeAuditEvent.model_validate(event)


@pytest.mark.parametrize("state", ["pending", "recorded", "approved", "rejected"])
def test_outcome_roundtrip_replay_and_freeze(state: str) -> None:
    command = _command()
    if state == "recorded":
        command = StockChangeCommand.model_validate(command.model_dump() | {"value": 5})
    decision = (
        StockChangeDecision.model_validate(
            {"command": command, "reviewer": _actor(9), "outcome": state, "reason": "Review"}
        )
        if state in {"approved", "rejected"}
        else None
    )
    version = 1 if state == "approved" else 0 if state == "recorded" else None
    record = StockChangeRecord.model_validate(
        {"command": command, "state": state, "decision": decision, "resulting_version": version}
    )
    assert StockChangeRecord.model_validate_json(record.model_dump_json()) == record
    assert record.freezes_new_reservations is (state == "pending")
    assert record.replay(command) is record
    changed = StockChangeCommand.model_validate(command.model_dump() | {"reason": "Changed"})
    with pytest.raises(ValueError, match="replay differs"):
        record.replay(changed)
    with pytest.raises(ValidationError):
        StockChangeRecord.model_validate(record.model_dump() | {"resulting_version": 99})


@pytest.mark.parametrize("state", ["recorded", "approved", "rejected"])
def test_invalid_terminal_records_are_rejected(state: str) -> None:
    with pytest.raises(ValidationError):
        StockChangeRecord.model_validate({"command": _command(), "state": state})


@pytest.mark.asyncio
async def test_actor_reload_is_explicit_bound_and_fail_closed() -> None:
    claimed = _actor()
    calls = []

    async def loader(*, client_id: UUID, user_id: UUID, session_id: UUID) -> TenantAdminEvidence:
        calls.append((client_id, user_id, session_id))
        return claimed

    assert await reload_tenant_admin(claimed, loader=loader) == claimed
    assert calls == [(claimed.client_id, claimed.user_id, claimed.session_id)]
    for field, value in [
        ("client_id", _id(99)),
        ("user_id", _id(99)),
        ("session_id", _id(99)),
        ("verified_at", datetime(2026, 10, 18, tzinfo=UTC)),
    ]:
        evidence = TenantAdminEvidence.model_validate(claimed.model_dump() | {field: value})

        async def mismatch(
            *,
            client_id: UUID,
            user_id: UUID,
            session_id: UUID,
            evidence: TenantAdminEvidence = evidence,
        ) -> TenantAdminEvidence:
            return evidence

        with pytest.raises(ValueError, match="does not match"):
            await reload_tenant_admin(claimed, loader=mismatch)

    async def revoked(*, client_id: UUID, user_id: UUID, session_id: UUID) -> TenantAdminEvidence:
        raise PermissionError("synthetic membership revoked")

    with pytest.raises(PermissionError, match="revoked"):
        await reload_tenant_admin(claimed, loader=revoked)
