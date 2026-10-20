"""Synthetic selected exchange data only; no settings, credentials or networking."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.token_response import parse_tiktok_token_response
from pydantic import ValidationError

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def payload():
    return {
        "code": 0,
        "message": "synthetic-private-message",
        "request_id": "synthetic-private-request",
        "data": {
            "access_token": "synthetic-private-access",
            "refresh_token": "synthetic-private-refresh",
            "access_token_expire_in": int((NOW + timedelta(hours=1)).timestamp()),
            "refresh_token_expire_in": int((NOW + timedelta(days=30)).timestamp()),
            "user_type": 0,
            "granted_scopes": ["seller.order.info", "seller.authorization.info"],
            "open_id": "synthetic-private-user-not-shop",
            "seller_name": "synthetic-private-name",
            "shop_cipher": "synthetic-private-untrusted",
        },
    }


def test_absolute_unix_expiry_selected_frozen_and_masked(caplog):
    result = parse_tiktok_token_response(payload(), now=NOW)
    assert result.access_expires_at == NOW + timedelta(hours=1)
    assert result.refresh_expires_at == NOW + timedelta(days=30)
    assert result.access_token.get_secret_value() == "synthetic-private-access"
    assert result.refresh_token.get_secret_value() == "synthetic-private-refresh"
    assert set(result.model_dump()) == {
        "access_token",
        "refresh_token",
        "access_expires_at",
        "refresh_expires_at",
        "scopes",
    }
    assert "synthetic-private" not in repr(result) + result.model_dump_json()
    assert "synthetic-private" not in caplog.text
    with pytest.raises(ValidationError):
        result.access_expires_at = NOW
    with pytest.raises(ValidationError):
        type(result).model_validate({**result.model_dump(), "shop_id": "not-derived"})


@pytest.mark.parametrize("field", ["access_token_expire_in", "refresh_token_expire_in"])
@pytest.mark.parametrize("value", [None, True, "1792285200", 1.5, 0, -1, 604800, 253402300800])
def test_expiry_requires_explicit_valid_future_absolute_integer_seconds(field, value):
    raw = payload()
    raw["data"][field] = value
    with pytest.raises(TikTokAuthorizationError):
        parse_tiktok_token_response(raw, now=NOW)


@pytest.mark.parametrize("field", ["access_token_expire_in", "refresh_token_expire_in"])
def test_exact_expiry_boundary_is_expired(field):
    raw = payload()
    raw["data"][field] = int(NOW.timestamp())
    with pytest.raises(TikTokAuthorizationError, match="expired_exchange_token"):
        parse_tiktok_token_response(raw, now=NOW)


@pytest.mark.parametrize(
    "field",
    [
        "access_token",
        "refresh_token",
        "access_token_expire_in",
        "refresh_token_expire_in",
        "user_type",
        "granted_scopes",
    ],
)
def test_required_fields_have_no_defaults(field):
    raw = payload()
    del raw["data"][field]
    with pytest.raises(TikTokAuthorizationError, match="invalid_token_response"):
        parse_tiktok_token_response(raw, now=NOW)


@pytest.mark.parametrize("value", [True, "0", 0.0, 1, 2, 3, 4, 5, -1])
def test_only_strict_standard_seller_type_supported(value):
    raw = payload()
    raw["data"]["user_type"] = value
    with pytest.raises(TikTokAuthorizationError):
        parse_tiktok_token_response(raw, now=NOW)


@pytest.mark.parametrize(
    "scopes",
    [
        None,
        "seller.order.info",
        [],
        [1],
        [""],
        [" seller.order.info"],
        ["seller.order.info", "seller.order.info"],
        ["seller.order.info"],
        ["seller.authorization.info"],
        ["seller.order.info.extra", "seller.authorization.info"],
        ["seller.fs.order&fulfillment.management"],
    ],
)
def test_no_scope_coercion_duplicates_or_inference(scopes):
    raw = payload()
    raw["data"]["granted_scopes"] = scopes
    with pytest.raises(TikTokAuthorizationError):
        parse_tiktok_token_response(raw, now=NOW)


def test_exact_documented_fs_scope_and_additional_scope_preserved():
    raw = payload()
    raw["data"]["granted_scopes"] = ["seller.fs.order&fufillment.management", "other.scope"]
    result = parse_tiktok_token_response(raw, now=NOW)
    assert result.scopes == frozenset(raw["data"]["granted_scopes"])


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"code": False},
        {"code": "0"},
        {"code": 0, "data": []},
        {"code": 0, "data": None},
        {
            "code": 123,
            "message": "synthetic-private-rejection",
            "data": {"secret": "synthetic-private"},
        },
    ],
)
def test_malformed_and_provider_rejection_never_echo_payload(raw, caplog):
    with pytest.raises(TikTokAuthorizationError) as caught:
        parse_tiktok_token_response(raw, now=NOW)
    assert "synthetic-private" not in str(caught.value) + caplog.text
    assert caught.value.__context__ is None or caught.value.__suppress_context__


@pytest.mark.parametrize("field", ["access_token", "refresh_token"])
@pytest.mark.parametrize("value", [None, "", 123, {"private": "synthetic-private"}])
def test_invalid_secret_fields_fail_safely(field, value, caplog):
    raw = payload()
    raw["data"][field] = value
    with pytest.raises(TikTokAuthorizationError) as caught:
        parse_tiktok_token_response(raw, now=NOW)
    assert "synthetic-private" not in str(caught.value) + caplog.text
    assert caught.value.__suppress_context__


def test_naive_clock_rejected():
    with pytest.raises(TikTokAuthorizationError, match="invalid_clock"):
        parse_tiktok_token_response(payload(), now=NOW.replace(tzinfo=None))


def test_aware_clock_offset_compares_same_instant_without_reinterpreting_expiry():
    local_now = NOW.astimezone(timezone(timedelta(hours=8)))
    assert parse_tiktok_token_response(payload(), now=local_now) == parse_tiktok_token_response(
        payload(), now=NOW
    )


def test_scope_container_must_be_json_array_not_tuple():
    raw = payload()
    raw["data"]["granted_scopes"] = tuple(raw["data"]["granted_scopes"])
    with pytest.raises(TikTokAuthorizationError, match="invalid_token_response"):
        parse_tiktok_token_response(raw, now=NOW)


def test_maximum_representable_unix_second_is_supported_without_overflow():
    raw = payload()
    raw["data"]["access_token_expire_in"] = 253402300799
    raw["data"]["refresh_token_expire_in"] = 253402300799
    result = parse_tiktok_token_response(raw, now=NOW)
    assert result.access_expires_at == datetime.max.replace(tzinfo=UTC, microsecond=0)
    assert result.refresh_expires_at == result.access_expires_at
