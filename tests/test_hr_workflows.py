"""Policy-bearing boundaries for the Malaysia HR workflow contracts."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from csa.hr.agents import HR_AGENT_INPUT, HrAgentDispatcher
from csa.hr.workflows import WORKFLOW_INPUT, PayrollInput, ReviewedPayrollAmounts, WorkflowGrant
from pydantic import ValidationError


@pytest.mark.parametrize("region", ["peninsular_labuan", "sabah", "sarawak"])
def test_all_regions_require_explicit_payroll_cycle_entity_and_rules(region: str) -> None:
    content = {
        "domain": "payroll",
        "region": region,
        "legal_entity_reference": "synthetic-entity",
        "starts_on": "2026-10-01",
        "ends_on": "2026-10-31",
        "cycle": "monthly",
        "currency": "MYR",
        "rule_set_reference": uuid4(),
    }
    model = PayrollInput.model_validate(content)
    assert model.starts_on == date(2026, 10, 1)
    for field in ("cycle", "legal_entity_reference", "rule_set_reference", "region"):
        with pytest.raises(ValidationError):
            PayrollInput.model_validate(
                {key: value for key, value in content.items() if key != field}
            )
    for changed in ({"currency": "USD"}, {"ends_on": "2026-11-30"}, {"cycle": "weekly"}):
        with pytest.raises(ValidationError):
            PayrollInput.model_validate({**content, **changed})


@pytest.mark.parametrize("domain", ["attendance", "leave", "recruitment"])
def test_agent_cannot_receive_approval_or_authority_commands(domain: str) -> None:
    for action in ("workflow.approve", "workflow.reject", "payroll.pay", "recruitment.hire"):
        with pytest.raises(ValidationError):
            HR_AGENT_INPUT.validate_python({"action": action, "domain": domain, "case_id": uuid4()})
    with pytest.raises(ValidationError):
        HR_AGENT_INPUT.validate_python(
            {"action": "workflow.read", "domain": domain, "case_id": uuid4(), "client_owner": True}
        )


def test_automatic_recruitment_is_a_job_guide_without_candidate_ranking() -> None:
    content = WORKFLOW_INPUT.validate_python(
        {
            "domain": "recruitment",
            "region": "sabah",
            "role_title": "Synthetic developer",
            "competencies": ["Testing", "Planning"],
        }
    )
    assert content.domain == "recruitment"
    assert len(content.guide()) == 2
    assert "Testing" in content.guide()[0]
    for extra in ({"candidate_score": 100}, {"candidate_reference": str(uuid4())}):
        with pytest.raises(ValidationError):
            WORKFLOW_INPUT.validate_python({**content.model_dump(), **extra})


@pytest.mark.parametrize(
    "changes",
    [
        {"starts_at": "2026-10-01T09:00:00"},
        {"ends_at": "2026-10-01T08:00:00+08:00"},
        {"ends_at": "2026-10-03T18:00:00+08:00"},
        {"starts_at": "2026-10-01T09:00:00+00:00"},
    ],
)
def test_attendance_rejects_naive_reversed_long_or_wrong_offset(changes: dict) -> None:
    with pytest.raises(ValidationError):
        WORKFLOW_INPUT.validate_python(
            {
                "domain": "attendance",
                "region": "sarawak",
                "timezone": "Asia/Kuching",
                "starts_at": "2026-10-01T09:00:00+08:00",
                "ends_at": "2026-10-01T18:00:00+08:00",
                "reason": "Synthetic",
                **changes,
            }
        )


@pytest.mark.parametrize("scope", ["self", "assigned_manager", "hr_operator", "client_owner"])
def test_financial_permissions_never_come_from_general_hr_scope(scope: str) -> None:
    with pytest.raises(ValidationError):
        WorkflowGrant(domain="payroll", action="prepare", scope=scope)  # type: ignore[arg-type]


def test_private_reviewed_amounts_use_decimal_and_reject_negative_net() -> None:
    amounts = ReviewedPayrollAmounts.model_validate(
        {
            "gross": "1000.00",
            "employee_epf": "110.00",
            "employee_socso": "1.00",
            "employee_eis": "1.00",
            "pcb": "10.00",
            "reviewed_other_deductions": "0.00",
            "employer_epf": "0.00",
            "employer_socso": "0.00",
            "employer_eis": "0.00",
        }
    )
    assert amounts.net == Decimal("878.00")
    for changes in ({"gross": "1.00"}, {"pcb": "-1.00"}, {"pcb": "NaN"}, {"pcb": "0.001"}):
        with pytest.raises(ValidationError):
            ReviewedPayrollAmounts.model_validate({**amounts.model_dump(), **changes})


def test_staff_capability_is_selected_from_typed_action() -> None:
    action = HR_AGENT_INPUT.validate_python(
        {"action": "workflow.read", "domain": "leave", "case_id": uuid4()}
    )
    assert HrAgentDispatcher.name(action) == "hr_attendance"
    assert HrAgentDispatcher.risk(action) == "read"


def test_permission_and_calendar_fingerprints_ignore_set_and_grant_order() -> None:
    from csa.hr.automation_models import AccessSave, CalendarSave
    from csa.hr.operations import HrOperations

    grants = [
        {"action": "directory.read", "scope": "self", "fields": ["display_name", "work_email"]},
        {"action": "training.read", "scope": "self", "fields": []},
    ]
    body = {"operation": "access.save", "user_id": str(uuid4()), "grants": grants, "workflows": []}
    left = AccessSave.model_validate(body)
    right = AccessSave.model_validate(
        {**body, "grants": [grants[1], {**grants[0], "fields": ["work_email", "display_name"]}]}
    )
    assert HrOperations.fingerprint(left) == HrOperations.fingerprint(right)
    calendar = {
        "operation": "calendar.save",
        "region": "sabah",
        "year": 2026,
        "working_weekdays": [0, 1],
        "holidays": ["2026-01-01", "2026-12-25"],
    }
    assert HrOperations.fingerprint(
        CalendarSave.model_validate(calendar)
    ) == HrOperations.fingerprint(
        CalendarSave.model_validate(
            {
                **calendar,
                "working_weekdays": [1, 0],
                "holidays": list(reversed(calendar["holidays"])),
            }
        )
    )
