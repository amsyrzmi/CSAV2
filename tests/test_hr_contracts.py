"""Synthetic external-boundary checks. These are not HTTP authentication tests."""

from uuid import UUID

import pytest
from csa.hr.models import (
    DirectoryField,
    DirectoryLookupInput,
    DirectoryRecord,
    DirectoryValues,
    EmployeeIdentity,
    EmployeeRef,
    GrantScope,
    HrAction,
    ResolvedGrant,
    TrainingCommandInput,
    TrainingDraftInput,
    TrainingSnapshot,
    TrustedAccess,
)
from pydantic import ValidationError


def test_employee_identity_is_tenant_scoped_and_account_optional() -> None:
    first = EmployeeRef(tenant_id=UUID(int=1), employee_id=UUID(int=10))
    other = EmployeeRef(tenant_id=UUID(int=2), employee_id=UUID(int=10))
    assert first != other
    assert EmployeeIdentity(employee=first).console_account_id is None
    assert "contact_id" not in EmployeeIdentity.model_fields


@pytest.mark.parametrize(
    "model,payload",
    [
        (DirectoryLookupInput, {"employee_id": str(UUID(int=10))}),
        (TrainingDraftInput, {"title": "Safety workshop", "learning_objective": "Practice drills"}),
        (TrainingCommandInput, {"action": "submit", "expected_version": 0}),
    ],
)
@pytest.mark.parametrize(
    "injected",
    [
        "actor",
        "subject_id",
        "tenant_id",
        "grants",
        "role",
        "client_owner",
        "assigned_manager",
        "employee",
        "permissions",
        "client_id",
    ],
)
def test_untrusted_inputs_reject_authority_injection(
    model: type[DirectoryLookupInput | TrainingDraftInput | TrainingCommandInput],
    payload: dict[str, object],
    injected: str,
) -> None:
    with pytest.raises(ValidationError) as exc:
        model.model_validate({**payload, injected: "untrusted"})
    assert any(error["type"] == "extra_forbidden" for error in exc.value.errors())


@pytest.mark.parametrize(
    "title,objective",
    [
        ("", "Learn"),
        ("   ", "Learn"),
        ("x" * 161, "Learn"),
        ("Training", ""),
        ("Training", "x" * 1001),
        ("Training\x00", "Learn"),
        ("Training", "line\ncontrol"),
    ],
)
def test_bounded_training_content(title: str, objective: str) -> None:
    with pytest.raises(ValidationError):
        TrainingDraftInput(title=title, learning_objective=objective)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "execute_payroll", "expected_version": 0},
        {"action": "submit", "expected_version": -1},
        {"action": "submit", "expected_version": True},
        {"action": "submit", "expected_version": "0"},
        {"action": "submit", "expected_version": 2_147_483_648},
        {"action": "reject", "expected_version": 0},
        {"action": "approve", "expected_version": 0, "decision_note": "x" * 501},
        {"action": "withdraw", "expected_version": 0, "decision_note": "Decision"},
    ],
)
def test_bounded_command_and_enum_validation(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TrainingCommandInput.model_validate(payload)


@pytest.mark.parametrize("field", ["salary", "bank", "medical", "cv"])
def test_sensitive_fields_have_no_value_or_grant_contract(field: str) -> None:
    # No sensitive values, even synthetic ones, enter these fixtures.
    with pytest.raises(ValidationError):
        DirectoryValues.model_validate({field: None})
    with pytest.raises(ValidationError):
        ResolvedGrant.model_validate(
            {"action": "directory.read", "scope": "hr_operator", "fields": [field]}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "directory.read", "scope": "tenant_admin"},
        {"action": "directory.read", "scope": "platform_admin"},
        {"action": "directory.read", "scope": "tenant_staff"},
        {"action": "payroll.read", "scope": "hr_operator"},
        {"action": "training.decide", "scope": "hr_operator"},
        {"action": "training.decide", "scope": "self"},
        {"action": "training.submit", "scope": "assigned_manager"},
        {"action": "training.read", "scope": "self", "fields": ["display_name"]},
    ],
)
def test_unsupported_grants_denied(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResolvedGrant.model_validate(payload)


def test_cross_tenant_trusted_facts_rejected() -> None:
    employee = EmployeeRef(tenant_id=UUID(int=1), employee_id=UUID(int=10))
    manager = EmployeeRef(tenant_id=UUID(int=2), employee_id=UUID(int=11))
    with pytest.raises(ValidationError, match="cross_tenant_reference"):
        DirectoryRecord(
            identity=EmployeeIdentity(employee=employee),
            values=DirectoryValues(assigned_manager=manager),
        )
    with pytest.raises(ValidationError, match="cross_tenant_reference"):
        TrustedAccess(tenant_id=UUID(int=2), subject_id=UUID(int=20), employee=employee)


def test_strict_trusted_flag_and_grant_bounds() -> None:
    with pytest.raises(ValidationError):
        TrustedAccess.model_validate(
            {"tenant_id": str(UUID(int=1)), "subject_id": str(UUID(int=20)), "client_owner": "true"}
        )
    grant = ResolvedGrant(
        action=HrAction.DIRECTORY_READ,
        scope=GrantScope.SELF,
        fields=frozenset({DirectoryField.DISPLAY_NAME}),
    )
    with pytest.raises(ValidationError):
        TrustedAccess(tenant_id=UUID(int=1), subject_id=UUID(int=20), grants=(grant,) * 33)


@pytest.mark.parametrize(
    "values",
    [
        {"display_name": "x" * 121},
        {"job_title": ""},
        {"department": "x" * 121},
        {"work_email": "x" * 255},
        {"work_email": "not a mailbox"},
        {"work_email": "a@example.test,b@example.test"},
        {"department": "bad\x00"},
    ],
)
def test_directory_value_bounds(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DirectoryValues.model_validate(values)


def test_invalid_snapshot_state_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid_decision_state"):
        TrainingSnapshot.model_validate(
            {
                "request_id": str(UUID(int=30)),
                "employee": {"tenant_id": str(UUID(int=1)), "employee_id": str(UUID(int=10))},
                "content": {"title": "Workshop", "learning_objective": "Practice"},
                "status": "approved",
                "version": 1,
                "updated_at": "2026-10-21T08:00:00Z",
            }
        )


def test_access_change_needs_a_unique_base() -> None:
    from csa.hr.access_contracts import AccessChange

    user = str(UUID(int=40))
    change = AccessChange.model_validate({"user_id": user, "on": ["directory"], "base": []})
    assert change.base == []
    for bad in (
        {"user_id": user, "on": ["directory"]},
        {"user_id": user, "on": [], "base": ["directory", "directory"]},
        {"user_id": user, "on": [], "base": ["unknown"]},
    ):
        with pytest.raises(ValidationError):
            AccessChange.model_validate(bad)
