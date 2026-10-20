"""Synthetic evidence checks; no credential access or claim of live authorization."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokGrantState,
    TikTokOperationEvidence,
    TikTokReadOperation,
    require_tiktok_order_read,
)
from csa.agents.ecommerce.stores import StoreBinding
from pydantic import ValidationError

NOW = datetime(2026, 10, 17, 20, tzinfo=UTC)


def contracts() -> tuple[TikTokReadOperation, TikTokGrantState, TikTokOperationEvidence]:
    context = TikTokAuthorizationContext(
        binding=StoreBinding(
            client_id=uuid4(), record_id=uuid4(), platform="tiktok_shop", shop_id="SYNTH-SHOP"
        ),
        app_id="SYNTH-APP",
        credential_generation=1,
    )
    operation = TikTokReadOperation(
        id=uuid4(), context=context, started_at=NOW - timedelta(seconds=1)
    )
    grant = TikTokGrantState(
        id=uuid4(),
        context=context,
        revision=1,
        state="active",
        source="verified_authorization_exchange",
        scopes=frozenset({"seller.order.info", "seller.authorization.info"}),
        verified_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(hours=1),
    )
    evidence = TikTokOperationEvidence(
        operation_id=operation.id,
        context=context,
        grant_id=grant.id,
        grant_revision=grant.revision,
        source="verified_authorization_and_authorized_shops",
        verified_at=NOW,
    )
    return operation, grant, evidence


@pytest.mark.parametrize(
    "scopes",
    [
        {"seller.order.info", "seller.authorization.info"},
        {"seller.fs.order&fufillment.management"},
        {"seller.order.info", "seller.fs.order&fufillment.management"},
    ],
)
def test_exact_documented_scope_alternatives(scopes: set[str]) -> None:
    operation, grant, evidence = contracts()
    grant = TikTokGrantState.model_validate({**grant.model_dump(), "scopes": scopes})
    assert require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW) is None


@pytest.mark.parametrize(
    "scopes",
    [
        set(),
        {"seller.order.info"},
        {"seller.authorization.info"},
        {"seller.fs.order&fulfillment.management"},
        {"seller.order.info.extra", "seller.authorization.info"},
    ],
)
def test_no_scope_inference_or_spelling_correction(scopes: set[str]) -> None:
    operation, grant, evidence = contracts()
    grant = TikTokGrantState.model_validate({**grant.model_dump(), "scopes": scopes})
    with pytest.raises(TikTokAuthorizationError, match="insufficient_scope"):
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)


@pytest.mark.parametrize("surface", ["grant", "evidence"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_id", uuid4()),
        ("record_id", uuid4()),
        ("shop_id", "OTHER-SHOP"),
        ("app_id", "OTHER-APP"),
        ("app_revision", 2),
        ("credential_generation", 2),
    ],
)
def test_every_binding_dimension_is_exact(surface: str, field: str, value: Any) -> None:
    operation, grant, evidence = contracts()
    raw = operation.context.model_dump()
    if field in ("app_id", "app_revision", "credential_generation"):
        raw[field] = value
    else:
        raw["binding"][field] = value
    context = TikTokAuthorizationContext.model_validate(raw)
    if surface == "grant":
        grant = TikTokGrantState.model_validate({**grant.model_dump(), "context": context})
    else:
        evidence = TikTokOperationEvidence.model_validate(
            {**evidence.model_dump(), "context": context}
        )
    with pytest.raises(TikTokAuthorizationError, match="binding_mismatch"):
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)


@pytest.mark.parametrize("state", ["revoked", "disconnected"])
def test_known_revocation_or_disconnect_denies_existing_operation(state: str) -> None:
    operation, grant, evidence = contracts()
    grant = TikTokGrantState.model_validate({**grant.model_dump(), "state": state, "revision": 2})
    with pytest.raises(TikTokAuthorizationError, match="inactive_grant"):
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)


@pytest.mark.parametrize("change", ["revision", "grant_id"])
def test_lifecycle_change_invalidates_old_evidence_even_when_active(change: str) -> None:
    operation, grant, evidence = contracts()
    raw = grant.model_dump()
    raw["revision" if change == "revision" else "id"] = 2 if change == "revision" else uuid4()
    grant = TikTokGrantState.model_validate(raw)
    with pytest.raises(TikTokAuthorizationError, match="stale_grant"):
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)


def test_evidence_cannot_be_reused_for_next_read_or_page() -> None:
    operation, grant, evidence = contracts()
    next_operation = TikTokReadOperation(
        id=uuid4(), context=operation.context, started_at=operation.started_at
    )
    with pytest.raises(TikTokAuthorizationError, match="operation_mismatch"):
        require_tiktok_order_read(next_operation, grant=grant, evidence=evidence, now=NOW)


@pytest.mark.parametrize("missing", ["grant", "evidence"])
def test_unknown_evidence_is_not_authorized(missing: str) -> None:
    operation, grant, evidence = contracts()
    with pytest.raises(
        TikTokAuthorizationError,
        match="missing_grant" if missing == "grant" else "missing_operation_evidence",
    ):
        require_tiktok_order_read(
            operation,
            grant=None if missing == "grant" else grant,
            evidence=None if missing == "evidence" else evidence,
            now=NOW,
        )


@pytest.mark.parametrize("offset", [0, -1])
def test_expiry_boundary_denies(offset: int) -> None:
    operation, grant, evidence = contracts()
    grant = TikTokGrantState.model_validate(
        {**grant.model_dump(), "expires_at": NOW + timedelta(microseconds=offset)}
    )
    with pytest.raises(TikTokAuthorizationError, match="expired_grant"):
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)


@pytest.mark.parametrize("boundary", ["operation_start", "now"])
def test_verification_time_boundaries_are_inclusive(boundary: str) -> None:
    operation, grant, evidence = contracts()
    evidence = TikTokOperationEvidence.model_validate(
        {
            **evidence.model_dump(),
            "verified_at": operation.started_at if boundary == "operation_start" else NOW,
        }
    )
    require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)


@pytest.mark.parametrize("when", [NOW - timedelta(seconds=2), NOW + timedelta(microseconds=1)])
def test_prior_operation_or_future_verification_rejected(when: datetime) -> None:
    operation, grant, evidence = contracts()
    evidence = TikTokOperationEvidence.model_validate(
        {**evidence.model_dump(), "verified_at": when}
    )
    with pytest.raises(TikTokAuthorizationError, match="invalid_evidence_time"):
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)


@pytest.mark.parametrize("surface", ["clock", "operation", "grant"])
def test_future_or_naive_time_is_not_silently_accepted(surface: str) -> None:
    operation, grant, evidence = contracts()
    now = NOW
    if surface == "clock":
        now = NOW.replace(tzinfo=None)
    elif surface == "operation":
        operation = TikTokReadOperation.model_validate(
            {**operation.model_dump(), "started_at": NOW + timedelta(seconds=1)}
        )
    else:
        grant = TikTokGrantState.model_validate(
            {**grant.model_dump(), "verified_at": NOW + timedelta(seconds=1)}
        )
    with pytest.raises(TikTokAuthorizationError, match="invalid_(clock|grant_time)"):
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=now)


def test_contracts_are_immutable_and_reject_untrusted_or_secret_fields() -> None:
    operation, grant, evidence = contracts()
    for contract in (operation, grant, evidence, operation.context):
        with pytest.raises(ValidationError):
            type(contract).model_validate({**contract.model_dump(), "access_token": "SYNTH"})
    for raw in (
        {**grant.model_dump(), "source": "local_metadata"},
        {**grant.model_dump(), "verified_at": NOW.replace(tzinfo=None)},
        {**grant.model_dump(), "expires_at": grant.verified_at},
        {**grant.model_dump(), "revision": True},
    ):
        with pytest.raises(ValidationError):
            TikTokGrantState.model_validate(raw)
    with pytest.raises(ValidationError):
        TikTokAuthorizationContext.model_validate(
            {**operation.context.model_dump(), "credential_generation": True}
        )
    with pytest.raises(ValidationError):
        TikTokAuthorizationContext.model_validate(
            {
                **operation.context.model_dump(),
                "binding": {**operation.context.binding.model_dump(), "platform": "shopee"},
            }
        )
    with pytest.raises(ValidationError):
        grant.state = "revoked"
    with pytest.raises(ValidationError):
        evidence.verified_at = NOW - timedelta(seconds=1)
    assert isinstance(grant.scopes, frozenset)


def test_rejection_messages_do_not_include_binding_or_evidence_contents() -> None:
    operation, grant, evidence = contracts()
    grant = TikTokGrantState.model_validate({**grant.model_dump(), "scopes": {"PRIVATE-SCOPE"}})
    with pytest.raises(TikTokAuthorizationError) as caught:
        require_tiktok_order_read(operation, grant=grant, evidence=evidence, now=NOW)
    assert str(caught.value) == "TikTok authorization evidence rejected: insufficient_scope"
    assert caught.value.code == "insufficient_scope"
