"""Synthetic A0 evidence only: no authenticated adapter, persistence or dispatch."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from csa.workspace.contracts import (
    MAX_EVIDENCE_AGE,
    MAX_WORK_TTL,
    AnswerProposal,
    AnswerResult,
    ClarificationProposal,
    DirectoryLookupProposal,
    DirectoryResult,
    EmailComposeProposal,
    GuidanceProposal,
    GuidanceResult,
    InboxDecisionProposal,
    LocalOrderSummary,
    NavigateProposal,
    OrderHint,
    OrderPageHint,
    OrderReadProposal,
    OrderReadResult,
    PageContext,
    ProposalCard,
    RecordRef,
    ResolvedWorkspaceAuthority,
    TrainingSubmitProposal,
    VerifiedWorkspacePrincipal,
    WorkspaceActionProposal,
    WorkspaceActionResult,
    WorkspaceAllowed,
    WorkspaceDeniedResult,
    WorkspaceProposal,
    WorkspaceTurnInput,
    WorkspaceWorkBinding,
    validate_workspace_execution,
)
from pydantic import BaseModel, TypeAdapter, ValidationError

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)
VERSION = NOW - timedelta(hours=1)
ORDER = OrderHint(kind="local_order", order_id="synthetic-order")
PROPOSAL = OrderReadProposal(action="local_order_read", resource=ORDER)


def changed[M: BaseModel](model: M, **values: object) -> M:
    return type(model).model_validate({**model.model_dump(), **values})


def evidence() -> tuple[
    WorkspaceWorkBinding, VerifiedWorkspacePrincipal, ResolvedWorkspaceAuthority
]:
    binding = WorkspaceWorkBinding(
        work_id=uuid4(),
        thread_id=uuid4(),
        owner_user_id=uuid4(),
        session_id=uuid4(),
        tenant_id=uuid4(),
        request_key=uuid4(),
        created_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=1),
        proposal=PROPOSAL,
        expected_resource_updated_at=VERSION,
    )
    principal = VerifiedWorkspacePrincipal(
        user_id=binding.owner_user_id,
        session_id=binding.session_id,
        tenant_id=binding.tenant_id,
        role="tenant_staff",
        verified_at=NOW,
    )
    authority = ResolvedWorkspaceAuthority(
        work_id=binding.work_id,
        thread_id=binding.thread_id,
        tenant_id=binding.tenant_id,
        user_id=binding.owner_user_id,
        session_id=binding.session_id,
        proposal=binding.proposal,
        checked_at=NOW,
        capability_enabled=True,
        action_allowed=True,
        resource_allowed=True,
        resource_updated_at=VERSION,
    )
    return binding, principal, authority


@pytest.mark.parametrize("size", [1, 4000])
def test_turn_body_exact_bounds_and_json_roundtrip(size: int) -> None:
    turn = WorkspaceTurnInput(
        request_key=uuid4(),
        body="x" * size,
        page_hint=OrderPageHint(page="business_order", entity=ORDER),
    )
    assert WorkspaceTurnInput.model_validate_json(turn.model_dump_json()) == turn


@pytest.mark.parametrize("body", ["", " \n ", "x" * 4001, 123, True])
def test_invalid_text(body: object) -> None:
    with pytest.raises(ValidationError):
        WorkspaceTurnInput.model_validate({"request_key": uuid4(), "body": body})


@pytest.mark.parametrize(
    "field",
    [
        "actor",
        "actor_id",
        "user_id",
        "owner_user_id",
        "role",
        "grants",
        "tenant_id",
        "client_id",
        "session_id",
        "contact_id",
        "employee_id",
        "channel",
        "callback",
        "voice",
        "attachments",
        "csrf_hash",
    ],
)
def test_browser_authority_claims_are_forbidden(field: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceTurnInput.model_validate(
            {"request_key": uuid4(), "body": "synthetic", field: "untrusted"}
        )


@pytest.mark.parametrize(
    "hint",
    [
        {"page": "payroll"},
        {"page": "/arbitrary/url"},
        {"page": "workspace_home", "entity": ORDER.model_dump()},
        {"page": "business_order"},
        {"page": "business_order", "entity": {**ORDER.model_dump(), "role": "hr"}},
        {"page": "business_orders", "html": "untrusted DOM"},
    ],
)
def test_page_hints_are_bounded_not_authority(hint: object) -> None:
    with pytest.raises(ValidationError):
        WorkspaceTurnInput.model_validate(
            {"request_key": uuid4(), "body": "synthetic", "page_hint": hint}
        )


@pytest.mark.parametrize("identifier", ["", "  ", "x" * 101, "x\n", "x\x00", "x\u200b"])
def test_order_identifier_rejects_invalid_values(identifier: str) -> None:
    with pytest.raises(ValidationError):
        OrderHint(kind="local_order", order_id=identifier)


def test_order_identifier_is_opaque_not_uuid_or_normalized() -> None:
    for identifier in ("x", "x" * 100, " synthetic/order+? "):
        assert OrderHint(kind="local_order", order_id=identifier).order_id == identifier
    with pytest.raises(ValidationError):
        OrderHint.model_validate({"kind": "local_order", "order_id": 123})


@pytest.mark.parametrize(
    "proposal",
    [
        {"action": "hr_directory_read"},
        {"action": "hr_training_request"},
        {"action": "payroll_read"},
        {"action": "order_confirm", "resource": ORDER.model_dump()},
        {"action": "ticket_note_draft"},
        {"action": "local_order_read", "resource": ORDER.model_dump(), "risk": "read"},
        {"action": "page_guidance", "page": "workspace_home", "grants": ["all"]},
        {"action": "page_guidance", "page": "workspace_home", "instructions": "execute"},
    ],
)
def test_proposals_cannot_supply_authority_or_expand_actions(proposal: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(WorkspaceActionProposal).validate_python(proposal)


@pytest.mark.parametrize("role", ["tenant_staff", "tenant_admin", "platform_admin"])
def test_current_authorized_human_read(role: str) -> None:
    binding, principal, authority = evidence()
    result = validate_workspace_execution(
        binding, changed(principal, role=role), authority, now=NOW
    )
    assert result == WorkspaceAllowed()


def test_guidance_needs_identity_and_action_authority_without_entity() -> None:
    binding, principal, authority = evidence()
    proposal = GuidanceProposal(action="page_guidance", page="workspace_home")
    binding = changed(binding, proposal=proposal, expected_resource_updated_at=None)
    authority = changed(authority, proposal=proposal, resource_updated_at=None)
    assert validate_workspace_execution(binding, principal, authority, now=NOW).status == "allowed"
    decision = validate_workspace_execution(
        binding, principal, changed(authority, action_allowed=False), now=NOW
    )
    assert decision == WorkspaceDeniedResult(code="action_forbidden")


def test_missing_revoked_principal_or_authority_is_denied() -> None:
    binding, principal, authority = evidence()
    assert validate_workspace_execution(binding, None, authority, now=NOW) == WorkspaceDeniedResult(
        code="principal_unavailable"
    )
    assert validate_workspace_execution(binding, principal, None, now=NOW) == WorkspaceDeniedResult(
        code="authority_unavailable"
    )


@pytest.mark.parametrize("field", ["user_id", "session_id", "tenant_id"])
def test_identity_binding_cannot_transfer_to_new_session_or_tenant(field: str) -> None:
    binding, principal, authority = evidence()
    assert validate_workspace_execution(
        binding, changed(principal, **{field: uuid4()}), authority, now=NOW
    ) == WorkspaceDeniedResult(code="binding_mismatch")


@pytest.mark.parametrize("field", ["work_id", "thread_id", "tenant_id", "user_id", "session_id"])
def test_authority_is_bound_to_original_work_and_owner(field: str) -> None:
    binding, principal, authority = evidence()
    assert validate_workspace_execution(
        binding, principal, changed(authority, **{field: uuid4()}), now=NOW
    ) == WorkspaceDeniedResult(code="binding_mismatch")


def test_hint_does_not_grant_access_and_other_entity_cannot_replace_original() -> None:
    binding, principal, authority = evidence()
    other = OrderReadProposal(
        action="local_order_read", resource=OrderHint(kind="local_order", order_id="other")
    )
    assert validate_workspace_execution(
        binding, principal, changed(authority, proposal=other), now=NOW
    ) == WorkspaceDeniedResult(code="binding_mismatch")
    assert validate_workspace_execution(
        binding,
        principal,
        changed(authority, resource_allowed=False, resource_updated_at=None),
        now=NOW,
    ) == WorkspaceDeniedResult(code="resource_unavailable")


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"capability_enabled": False}, "capability_unavailable"),
        ({"action_allowed": False}, "action_forbidden"),
        ({"resource_allowed": False}, "resource_unavailable"),
        ({"resource_updated_at": VERSION + timedelta(seconds=1)}, "stale_resource"),
        (
            {
                "proposal": GuidanceProposal(action="page_guidance", page="business_order"),
                "resource_updated_at": None,
            },
            "binding_mismatch",
        ),
    ],
)
def test_current_policy_changes_deny_next_attempt(changes: dict, code: str) -> None:
    binding, principal, authority = evidence()
    assert validate_workspace_execution(binding, principal, authority, now=NOW).status == "allowed"
    result = validate_workspace_execution(
        binding, principal, changed(authority, **changes), now=NOW
    )
    assert isinstance(result, WorkspaceDeniedResult) and result.code == code


def test_membership_downgrade_denies_even_with_old_action_grant() -> None:
    binding, principal, authority = evidence()
    assert validate_workspace_execution(
        binding, changed(principal, role="read_only"), authority, now=NOW
    ) == WorkspaceDeniedResult(code="action_forbidden")


@pytest.mark.parametrize("offset", [-61, -31, 1])
@pytest.mark.parametrize("kind", ["principal", "authority"])
def test_stale_or_future_evidence_is_denied(kind: str, offset: int) -> None:
    binding, principal, authority = evidence()
    when = NOW + timedelta(seconds=offset)
    if kind == "principal":
        principal = changed(principal, verified_at=when)
        code = "principal_unavailable"
    else:
        authority = changed(authority, checked_at=when)
        code = "authority_unavailable"
    result = validate_workspace_execution(binding, principal, authority, now=NOW)
    assert isinstance(result, WorkspaceDeniedResult) and result.code == code


def test_freshness_and_expiry_exact_boundaries() -> None:
    binding, principal, authority = evidence()
    principal = changed(principal, verified_at=NOW - MAX_EVIDENCE_AGE)
    authority = changed(authority, checked_at=NOW - MAX_EVIDENCE_AGE)
    assert validate_workspace_execution(binding, principal, authority, now=NOW).status == "allowed"
    assert validate_workspace_execution(
        binding, principal, authority, now=binding.expires_at
    ) == WorkspaceDeniedResult(code="proposal_expired")
    before_expiry = binding.expires_at - timedelta(microseconds=1)
    assert (
        validate_workspace_execution(
            binding,
            changed(principal, verified_at=before_expiry),
            changed(authority, checked_at=before_expiry),
            now=before_expiry,
        ).status
        == "allowed"
    )
    assert validate_workspace_execution(
        binding, principal, authority, now=binding.created_at - timedelta(microseconds=1)
    ) == WorkspaceDeniedResult(code="binding_mismatch")
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_workspace_execution(binding, principal, authority, now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    "ttl", [timedelta(), timedelta(seconds=-1), MAX_WORK_TTL + timedelta(microseconds=1)]
)
def test_work_lifetime_bound(ttl: timedelta) -> None:
    binding, _, _ = evidence()
    with pytest.raises(ValidationError):
        changed(binding, expires_at=binding.created_at + ttl)


def test_version_and_aware_time_requirements() -> None:
    binding, principal, authority = evidence()
    assert changed(binding, expires_at=binding.created_at + MAX_WORK_TTL)
    for model, updates in (
        (binding, {"expected_resource_updated_at": None}),
        (binding, {"created_at": NOW.replace(tzinfo=None)}),
        (binding, {"expected_resource_updated_at": NOW + timedelta(seconds=1)}),
        (principal, {"verified_at": NOW.replace(tzinfo=None)}),
        (authority, {"resource_updated_at": None}),
        (authority, {"resource_updated_at": NOW + timedelta(seconds=1)}),
        (authority, {"capability_enabled": "true"}),
    ):
        with pytest.raises(ValidationError):
            changed(model, **updates)


def test_server_evidence_excludes_credentials_and_role_grant_snapshots() -> None:
    binding, principal, authority = evidence()
    for model in (binding, principal, authority):
        for field in ("cookie", "bearer", "csrf_hash", "csrf_token", "employee_id", "grants"):
            with pytest.raises(ValidationError):
                changed(model, **{field: "not-a-credential"})
        assert type(model).model_validate_json(model.model_dump_json()) == model
    with pytest.raises(ValidationError):
        changed(binding, role="tenant_admin")
    with pytest.raises(ValidationError):
        principal.role = "platform_admin"


def test_tenant_and_supported_role_are_required_even_for_platform_admin() -> None:
    _, principal, _ = evidence()
    for updates in (
        {"tenant_id": None, "role": "platform_admin"},
        {"role": "manager"},
        {"role": "machine"},
    ):
        with pytest.raises(ValidationError):
            changed(principal, **updates)


def test_guidance_cannot_carry_an_entity_version() -> None:
    binding, _, authority = evidence()
    proposal = GuidanceProposal(action="page_guidance", page="business_orders")
    for model in (binding, authority):
        with pytest.raises(ValidationError, match="guidance has no resource version"):
            changed(model, proposal=proposal)


def summary() -> LocalOrderSummary:
    return LocalOrderSummary(
        resource=ORDER,
        status="shipped",
        payment_status="paid",
        total=Decimal("12.34"),
        currency="MYR",
        channel="manual",
        external_order_id=None,
        external_shop_id=None,
        tracking_no="SYNTHETIC-TRACK",
        carrier=None,
        placed_at=None,
        shipped_at=None,
        created_at=VERSION,
        updated_at=VERSION,
    )


def test_result_discriminators_source_freshness_and_decimal_serialization() -> None:
    adapter = TypeAdapter(WorkspaceActionResult)
    results = [
        GuidanceResult(page="business_order", instruction_key="business_order_help", as_of=NOW),
        OrderReadResult(order=summary(), as_of=NOW),
        WorkspaceDeniedResult(code="resource_unavailable"),
        adapter.validate_python(
            {"status": "unavailable", "code": "business_unavailable", "retryable": True}
        ),
    ]
    for result in results:
        assert adapter.validate_json(result.model_dump_json()) == result
    order = results[1].model_dump(mode="json")
    assert order["source"] == "local_records"
    assert order["marketplace_sync"] == "unavailable"
    assert order["order"]["total"] == "12.34"
    assert results[2].model_dump() == {"status": "denied", "code": "resource_unavailable"}
    schema = adapter.json_schema()
    assert len(schema["anyOf"]) == 9


@pytest.mark.parametrize(
    "updates",
    [
        {"ship_to": {}},
        {"contact_id": uuid4()},
        {"salary": Decimal("123")},
        {"provider_payload": {}},
        {"total": Decimal("NaN")},
        {"total": -1},
        {"currency": "myr"},
        {"currency": "MYR\n"},
    ],
)
def test_safe_order_projection_rejects_unbounded_or_invalid_data(updates: dict) -> None:
    with pytest.raises(ValidationError):
        changed(summary(), **updates)


def test_completed_result_cannot_claim_live_source_or_unrelated_guidance() -> None:
    with pytest.raises(ValidationError):
        GuidanceResult(page="workspace_home", instruction_key="business_order_help", as_of=NOW)
    result = OrderReadResult(order=summary(), as_of=NOW)
    for updates in (
        {"source": "live_provider"},
        {"marketplace_sync": "available"},
        {"status": "queued"},
    ):
        with pytest.raises(ValidationError):
            changed(result, **updates)


# --- Task 2: page context, proposals and registry contracts ---


def test_email_compose_requires_exactly_one_recipient() -> None:
    base = {"action": "email_compose", "subject": "Hi", "instructions": ""}
    EmailComposeProposal.model_validate({**base, "recipient_address": "a@example.com"})
    EmailComposeProposal.model_validate({**base, "recipient_employee_id": uuid4()})
    with pytest.raises(ValidationError):
        EmailComposeProposal.model_validate(
            {**base, "recipient_address": "a@example.com", "recipient_employee_id": uuid4()}
        )
    with pytest.raises(ValidationError):
        EmailComposeProposal.model_validate(base)


def test_training_submit_request_xor_new_fields() -> None:
    action = {"action": "training_submit"}
    TrainingSubmitProposal.model_validate({**action, "request_id": uuid4()})
    TrainingSubmitProposal.model_validate({**action, "title": "T", "learning_objective": "O"})
    for bad in (
        {"request_id": uuid4(), "title": "T"},
        {"request_id": uuid4(), "title": "T", "learning_objective": "O"},
        {"title": "T"},
        {"learning_objective": "O"},
        {},
    ):
        with pytest.raises(ValidationError):
            TrainingSubmitProposal.model_validate({**action, **bad})


def test_page_context_and_record_ref() -> None:
    ctx = PageContext(tab="hr", record=RecordRef(kind="training_request", id="x"))
    assert ctx.section is None
    with pytest.raises(ValidationError):
        RecordRef.model_validate({"kind": "customer", "id": "x"})
    with pytest.raises(ValidationError):
        RecordRef.model_validate({"kind": "order", "id": ""})
    with pytest.raises(ValidationError):
        PageContext.model_validate({"tab": "nope"})
    turn = WorkspaceTurnInput(request_key=uuid4(), body="hi", page_context=ctx)
    assert turn.page_context == ctx and turn.page_hint is None


def test_subscriptions_is_a_gated_page_context_tab() -> None:
    from csa.console.contracts import Role
    from csa.console.services import PackageState
    from csa.workspace.planner import PlannerFacts
    from csa.workspace.service import _tab_allowed

    ctx = PageContext(tab="subscriptions", section="requests")
    assert ctx.tab == "subscriptions" and ctx.record is None

    def facts(role: Role, *packages: PackageState) -> PlannerFacts:
        return PlannerFacts(
            role=role,
            packages=list(packages),
            page_context=ctx,
            record_summary=None,
            language="en",
        )

    active = PackageState(key="subscriptions", status="active")
    assert _tab_allowed(facts(Role.TENANT_STAFF, active), "subscriptions")
    assert not _tab_allowed(facts(Role.TENANT_STAFF), "subscriptions")
    assert not _tab_allowed(
        facts(Role.TENANT_STAFF, PackageState(key="subscriptions", status="suspended")),
        "subscriptions",
    )


def test_workspace_proposal_discriminator_parses_every_kind() -> None:
    adapter = TypeAdapter(WorkspaceProposal)
    cid = uuid4()
    cases = {
        "answer": {"markdown": "x", "reads": ["my_profile"]},
        "navigate": {"tab": "inbox", "section": None, "record": None},
        "directory_lookup": {"query": "ann"},
        "email_compose": {"recipient_address": "a@example.com", "subject": "s", "instructions": ""},
        "email_reply": {"thread_id": "t1", "cc": ["c@example.com"], "send": True},
        "email_team": {"subject": "s", "instructions": "i"},
        "inbox_summary": {},
        "training_submit": {"request_id": uuid4()},
        "inbox_decision": {
            "approval_id": 1,
            "conversation_id": cid,
            "decision": "approve",
            "expected_version": 0,
        },
        "clarification": {"question": "which?", "options": [{"label": "a", "value": "b"}]},
        "page_guidance": {"page": "workspace_home"},
        "local_order_read": {"resource": {"kind": "local_order", "order_id": "o"}},
    }
    for action, rest in cases.items():
        assert adapter.validate_python({"action": action, **rest}).action == action
    assert WorkspaceActionProposal is not None
    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "unknown"})


def test_answer_reads_and_clarification_options_bounded() -> None:
    reads = ["read_current_record", "my_profile", "my_packages"]
    AnswerProposal(action="answer", markdown="x", reads=reads)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AnswerProposal.model_validate({"action": "answer", "markdown": "x", "reads": reads * 2})
    with pytest.raises(ValidationError):
        AnswerProposal.model_validate({"action": "answer", "markdown": "x", "reads": ["secrets"]})
    opts = [{"label": str(i), "value": str(i)} for i in range(6)]
    ClarificationProposal.model_validate(
        {"action": "clarification", "question": "q", "options": opts[:5]}
    )
    with pytest.raises(ValidationError):
        ClarificationProposal.model_validate(
            {"action": "clarification", "question": "q", "options": opts}
        )


def test_proposal_card_forbids_extra_and_results_join_union() -> None:
    card = {
        "proposal_id": uuid4(),
        "kind": "email_export",
        "title": "t",
        "summary": "s",
        "fields": [{"label": "a", "value": "b"}],
        "expires_at": NOW,
        "expected_version": None,
    }
    ProposalCard.model_validate(card)
    with pytest.raises(ValidationError):
        ProposalCard.model_validate({**card, "extra": 1})
    adapter = TypeAdapter(WorkspaceActionResult)
    answer = adapter.validate_python({"action": "answer", "markdown": "x"})
    assert isinstance(answer, AnswerResult)
    people = adapter.validate_python({"action": "directory_lookup", "candidates": []})
    assert isinstance(people, DirectoryResult)
    assert InboxDecisionProposal and NavigateProposal and DirectoryLookupProposal
