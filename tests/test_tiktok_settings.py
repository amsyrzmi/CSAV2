"""TikTok app settings: unset, empty and the staging sentinel mean off; synthetic values."""

from __future__ import annotations

import pytest
from csa.core.settings import Settings

from tests.test_console_identity_config import configured

REAL = {
    "TIKTOK_APP_KEY": "synthetic-app-key",
    "TIKTOK_APP_SECRET": "synthetic-app-secret",
    "TIKTOK_SERVICE_ID": "synthetic-service-id",
}


@pytest.mark.parametrize("name", sorted(REAL))
@pytest.mark.parametrize("placeholder", ["", "   ", "disabled", None])
def test_tiktok_enabled_off_for_unset_empty_and_disabled(
    name: str, placeholder: str | None
) -> None:
    off: Settings = configured(**{**REAL, name: placeholder})
    assert getattr(off, name) is None
    assert off.tiktok_enabled is False


def test_tiktok_enabled_off_when_nothing_is_set() -> None:
    off: Settings = configured()
    assert off.TIKTOK_APP_KEY is None
    assert off.TIKTOK_APP_SECRET is None
    assert off.TIKTOK_SERVICE_ID is None
    assert off.tiktok_enabled is False


def test_tiktok_enabled_on_for_real_looking_values() -> None:
    on: Settings = configured(**REAL)
    assert on.tiktok_enabled is True
    assert on.TIKTOK_APP_KEY == "synthetic-app-key"
    assert on.TIKTOK_APP_SECRET is not None
    assert on.TIKTOK_APP_SECRET.get_secret_value() == "synthetic-app-secret"
    assert on.TIKTOK_SERVICE_ID == "synthetic-service-id"
    assert "synthetic-app-secret" not in repr(on)


def test_tiktok_app_id_defaults_and_can_be_overridden() -> None:
    assert configured().TIKTOK_APP_ID == "tiktok_shop"
    assert configured(TIKTOK_APP_ID="second_app").TIKTOK_APP_ID == "second_app"
