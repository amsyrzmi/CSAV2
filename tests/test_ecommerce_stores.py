"""Pure local store-binding/expiry contracts, not marketplace authorization."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from csa.agents.ecommerce.stores import (
    PendingStoreBinding,
    StoreBinding,
    StoreConnection,
    StoreRecord,
    StoreSync,
    expiry_state,
)
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
    assert record.sync is None and record.can_manage is False
    for update in (
        {"authorization": "connected"},
        {"permissions": ["orders.read"]},
        {"provider_reads": "available"},
        {"authorization": "verified"},
        {"permissions": "granted"},
        {"connection": {"status": "connected"}},
        {"connection": {"status": "authorization_recorded", "live_integration": "unavailable"}},
        {"sync": {"problem": "other"}},
        {"sync": {"orders_synced_at": "2026-10-17T00:00:00"}},
        {"can_manage": "maybe"},
        {"access_token": "synthetic-private"},
        {"meta": {"scope": "synthetic-private"}},
        {"updated_at": "2026-10-17T00:00:00"},
    ):
        with pytest.raises(ValidationError):
            StoreRecord.model_validate({**record.model_dump(), **update})


@pytest.mark.parametrize(
    ("status", "live", "authorization", "permissions"),
    [
        ("authorization_recorded", "available", "verified", "granted"),
        ("permissions_missing", "unavailable", "unverified", "missing"),
        ("unverified", "unavailable", "unverified", "unknown"),
        ("expired", "unavailable", "unverified", "unknown"),
        ("disconnected", "unavailable", "unverified", "unknown"),
    ],
)
def test_store_summary_follows_the_connection_status(
    status: str, live: str, authorization: str, permissions: str
) -> None:
    connection = StoreConnection.model_validate({"status": status})
    assert connection.live_integration == live
    base = {
        "id": uuid4(),
        "platform": "tiktok_shop",
        "shop_id": "SYNTH-SHOP",
        "expires_at": None,
        "updated_at": datetime(2026, 10, 17, tzinfo=UTC),
        "expiry": "unknown",
        "connection": connection,
    }
    record = StoreRecord.model_validate(base)
    assert (record.authorization, record.permissions, record.provider_reads) == (
        authorization,
        permissions,
        live,
    )
    # A dumped record round-trips; a contradicting summary or connection is refused.
    assert StoreRecord.model_validate(record.model_dump()) == record
    other = "unavailable" if live == "available" else "available"
    with pytest.raises(ValidationError):
        StoreRecord.model_validate({**base, "provider_reads": other})
    with pytest.raises(ValidationError):
        StoreConnection.model_validate({"status": status, "live_integration": other})
    synced = StoreRecord.model_validate(
        {**base, "sync": StoreSync(problem="sync_failing"), "can_manage": True}
    )
    assert synced.sync is not None and synced.sync.problem == "sync_failing" and synced.can_manage


def test_pending_binding_is_tiktok_only_and_never_carries_a_shop() -> None:
    pending = PendingStoreBinding(client_id=uuid4(), record_id=uuid4(), platform="tiktok_shop")
    with pytest.raises(ValidationError):
        pending.record_id = uuid4()
    for update in (
        {"platform": "shopee"},
        {"shop_id": "SYNTH-SHOP"},
        {"shop_id": None},
        {"access_token": "synthetic-private"},
    ):
        with pytest.raises(ValidationError):
            PendingStoreBinding.model_validate({**pending.model_dump(), **update})
    # The readable binding stays strict: a store without a shop is not a StoreBinding.
    with pytest.raises(ValidationError):
        StoreBinding.model_validate(pending.model_dump())
    with pytest.raises(ValidationError):
        StoreBinding.model_validate({**pending.model_dump(), "shop_id": None})
