"""Pure local store-binding/expiry contracts, not marketplace authorization."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from csa.agents.ecommerce.stores import StoreBinding, StoreRecord, expiry_state
from pydantic import ValidationError


def test_binding_is_exact_immutable_and_never_accepts_credentials() -> None:
    binding = StoreBinding(
        client_id=uuid4(), record_id=uuid4(), platform="tiktok_shop", shop_id="SYNTH/+?&=%"
    )
    assert binding.shop_id == "SYNTH/+?&=%"
    with pytest.raises(ValidationError):
        binding.shop_id = "other"
    for update in (
        {"shop_id": ""},
        {"shop_id": "x" * 101},
        {"platform": "telegram"},
        {"access_token": "synthetic-private"},
        {"permissions": ["orders.read"]},
    ):
        with pytest.raises(ValidationError):
            StoreBinding.model_validate({**binding.model_dump(), **update})


def test_expiry_is_aware_exact_and_does_not_imply_authorization() -> None:
    now = datetime(2026, 10, 17, tzinfo=UTC)
    assert expiry_state(None, now=now) == "unknown"
    assert expiry_state(now, now=now) == "expired"
    assert expiry_state(now - timedelta(microseconds=1), now=now) == "expired"
    assert expiry_state(now + timedelta(microseconds=1), now=now) == "not_expired"
    with pytest.raises(ValueError, match="timezone-aware"):
        expiry_state(now.replace(tzinfo=None), now=now)
    with pytest.raises(ValueError, match="timezone-aware"):
        expiry_state(None, now=now.replace(tzinfo=None))


def test_store_metadata_cannot_claim_provider_authority_or_include_secrets() -> None:
    record = StoreRecord(
        id=uuid4(),
        platform="tiktok_shop",
        shop_id=None,
        expires_at=None,
        updated_at=datetime(2026, 10, 17, tzinfo=UTC),
        expiry="unknown",
    )
    assert record.authorization == "unverified"
    assert record.permissions == "unknown"
    assert record.provider_reads == "unavailable"
    assert record.connection.status == "unverified"
    assert record.connection.live_integration == "unavailable"
    for update in (
        {"authorization": "connected"},
        {"permissions": ["orders.read"]},
        {"provider_reads": "available"},
        {"connection": {"status": "connected"}},
        {"connection": {"status": "authorization_recorded", "live_integration": "available"}},
        {"access_token": "synthetic-private"},
        {"meta": {"scope": "synthetic-private"}},
        {"updated_at": "2026-10-17T00:00:00"},
    ):
        with pytest.raises(ValidationError):
            StoreRecord.model_validate({**record.model_dump(), **update})
