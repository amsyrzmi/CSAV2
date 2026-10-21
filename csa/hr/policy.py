"""Pure policy checks over trusted adapter inputs; these functions authenticate nobody."""

from __future__ import annotations

from csa.hr.models import (
    DirectoryField,
    DirectoryProjection,
    DirectoryRecord,
    DirectoryValues,
    GrantScope,
    HrAction,
    ResolvedGrant,
    TrustedAccess,
)


class HrPolicyError(ValueError):
    """Stable, content-free domain error; adapters must sanitize validation errors too."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def matching_grants(
    access: TrustedAccess, action: HrAction, record: DirectoryRecord
) -> tuple[ResolvedGrant, ...]:
    employee = record.identity.employee
    if access.tenant_id != employee.tenant_id:
        raise HrPolicyError("cross_tenant_reference")
    manager = record.values.assigned_manager
    is_self = access.employee == employee

    def matches(grant: ResolvedGrant) -> bool:
        if grant.action != action:
            return False
        match grant.scope:
            case GrantScope.SELF:
                return is_self
            case GrantScope.TEAM:
                return (
                    access.employee is not None
                    and access.employee == manager
                    and (not is_self or access.client_owner)
                )
            case GrantScope.HR:
                return True  # explicit HR read grant, never inferred from a role
            case GrantScope.CLIENT_OWNER:
                return access.client_owner and manager is not None

    grants = tuple(grant for grant in access.grants if matches(grant))
    if not grants:
        raise HrPolicyError("permission_denied")
    return grants


def project_directory(access: TrustedAccess, record: DirectoryRecord) -> DirectoryProjection:
    """Only explicitly granted safe fields; hidden values are absent, not copied."""
    grants = matching_grants(access, HrAction.DIRECTORY_READ, record)
    fields: set[DirectoryField] = set().union(*(grant.fields for grant in grants))
    values = record.values.model_dump(include={field.value for field in fields}, exclude_none=True)
    projected = DirectoryValues.model_validate(values)
    return DirectoryProjection(employee=record.identity.employee, values=projected)
