"""Pure field and relationship policy tests over synthetic trusted inputs."""

from uuid import UUID

import pytest
from csa.hr.models import (
    DirectoryField,
    DirectoryRecord,
    DirectoryValues,
    EmployeeIdentity,
    EmployeeRef,
    GrantScope,
    HrAction,
    ResolvedGrant,
    TrustedAccess,
)
from csa.hr.policy import HrPolicyError, matching_grants, project_directory

TENANT = UUID(int=1)
EMPLOYEE = EmployeeRef(tenant_id=TENANT, employee_id=UUID(int=10))
MANAGER = EmployeeRef(tenant_id=TENANT, employee_id=UUID(int=11))
OUTSIDER = EmployeeRef(tenant_id=TENANT, employee_id=UUID(int=12))


def record(manager: EmployeeRef | None = MANAGER) -> DirectoryRecord:
    return DirectoryRecord(
        identity=EmployeeIdentity(employee=EMPLOYEE, console_account_id=UUID(int=20)),
        values=DirectoryValues(
            display_name="Synthetic Staff",
            job_title="Trainer",
            department="Learning",
            work_email="staff@example.test",
            assigned_manager=manager,
        ),
    )


@pytest.mark.parametrize(
    "scope,actor,fields",
    [
        (GrantScope.SELF, EMPLOYEE, {DirectoryField.DISPLAY_NAME, DirectoryField.WORK_EMAIL}),
        (GrantScope.TEAM, MANAGER, {DirectoryField.DISPLAY_NAME, DirectoryField.DEPARTMENT}),
        (GrantScope.HR, None, {DirectoryField.JOB_TITLE, DirectoryField.ASSIGNED_MANAGER}),
    ],
)
def test_explicit_self_team_hr_field_filtering(
    scope: GrantScope, actor: EmployeeRef | None, fields: set[DirectoryField]
) -> None:
    access = TrustedAccess(
        tenant_id=TENANT,
        subject_id=UUID(int=21),
        employee=actor,
        grants=(
            ResolvedGrant(action=HrAction.DIRECTORY_READ, scope=scope, fields=frozenset(fields)),
        ),
    )
    projection = project_directory(access, record())
    assert set(projection.values.model_dump(exclude_none=True)) == {field.value for field in fields}
    assert "console_account_id" not in projection.model_dump_json()
    assert projection.employee == EMPLOYEE


def test_fields_only_union_across_matching_grants() -> None:
    access = TrustedAccess(
        tenant_id=TENANT,
        subject_id=UUID(int=21),
        employee=EMPLOYEE,
        grants=(
            ResolvedGrant(
                action=HrAction.DIRECTORY_READ,
                scope=GrantScope.SELF,
                fields=frozenset({DirectoryField.DISPLAY_NAME}),
            ),
            ResolvedGrant(
                action=HrAction.DIRECTORY_READ,
                scope=GrantScope.TEAM,
                fields=frozenset({DirectoryField.WORK_EMAIL}),
            ),
        ),
    )
    assert project_directory(access, record()).values.work_email is None


@pytest.mark.parametrize(
    "scope,actor",
    [
        (GrantScope.SELF, OUTSIDER),
        (GrantScope.TEAM, OUTSIDER),
        (GrantScope.TEAM, None),
    ],
)
def test_unrelated_employee_and_manager_outside_team_denied(
    scope: GrantScope, actor: EmployeeRef | None
) -> None:
    access = TrustedAccess(
        tenant_id=TENANT,
        subject_id=UUID(int=21),
        employee=actor,
        grants=(ResolvedGrant(action=HrAction.DIRECTORY_READ, scope=scope),),
    )
    with pytest.raises(HrPolicyError, match="permission_denied"):
        project_directory(access, record())


def test_missing_grants_deny_even_client_owner() -> None:
    access = TrustedAccess(
        tenant_id=TENANT, subject_id=UUID(int=21), employee=EMPLOYEE, client_owner=True
    )
    with pytest.raises(HrPolicyError, match="permission_denied"):
        project_directory(access, record())


def test_cross_tenant_read_denied_even_with_hr_grant() -> None:
    access = TrustedAccess(
        tenant_id=UUID(int=2),
        subject_id=UUID(int=21),
        grants=(ResolvedGrant(action=HrAction.DIRECTORY_READ, scope=GrantScope.HR),),
    )
    with pytest.raises(HrPolicyError, match="cross_tenant_reference"):
        project_directory(access, record())


def test_empty_field_grant_reveals_only_employee_reference() -> None:
    access = TrustedAccess(
        tenant_id=TENANT,
        subject_id=UUID(int=21),
        employee=EMPLOYEE,
        grants=(ResolvedGrant(action=HrAction.DIRECTORY_READ, scope=GrantScope.SELF),),
    )
    assert project_directory(access, record()).values.model_dump(exclude_none=True) == {}


def test_read_grant_does_not_authorize_write() -> None:
    access = TrustedAccess(
        tenant_id=TENANT,
        subject_id=UUID(int=21),
        employee=EMPLOYEE,
        grants=(ResolvedGrant(action=HrAction.TRAINING_READ, scope=GrantScope.SELF),),
    )
    with pytest.raises(HrPolicyError, match="permission_denied"):
        matching_grants(access, HrAction.TRAINING_SUBMIT, record())
