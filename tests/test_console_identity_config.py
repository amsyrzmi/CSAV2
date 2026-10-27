"""Identity configuration and deliberate bootstrap boundaries without dotenv files."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import Mock

import pytest
from csa.core.settings import Settings
from pydantic import ValidationError
from scripts import bootstrap_console_admin


def configured(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://csa_app:synthetic@localhost:1/disposable_fixture",
        "LG_DATABASE_URL": "postgresql://csa_app:synthetic@localhost:1/disposable_fixture",
        "GROQ_API_KEY": "synthetic-no-provider-access",
        "PUBLIC_BASE_URL": "https://console.example.test",
        "CREDENTIALS_KEY": "base64:BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ=",
        "IDENTITY_DATABASE_URL": "postgresql://csa_identity:synthetic@localhost:1/disposable_fixture",
        "CONSOLE_ALLOWED_ORIGIN": "https://console.example.test",
        "CONSOLE_API_TOKEN": None,
        "CONSOLE_MACHINE_CLIENT_IDS": [],
    }
    values.update(overrides)
    return Settings(_env_file=None, _secrets_dir=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "overrides",
    [
        {"IDENTITY_DATABASE_URL": "postgresql://csa:synthetic@localhost:1/disposable_fixture"},
        {"IDENTITY_DATABASE_URL": "postgresql://csa_identity:synthetic@localhost:1/other_database"},
        {"CONSOLE_ALLOWED_ORIGIN": None},
        {"CONSOLE_ALLOWED_ORIGIN": "https://console.example.test/path"},
        {"CONSOLE_ALLOWED_ORIGIN": "https://user:synthetic@console.example.test"},
        {"CONSOLE_ALLOWED_ORIGIN": "https://console.example.test?query=yes"},
        {"CONSOLE_ALLOWED_ORIGIN": "http://console.example.test"},
        {"CONSOLE_COOKIE_SECURE": False},
        {"CONSOLE_SESSION_IDLE_SECONDS": 3600, "CONSOLE_SESSION_ABSOLUTE_SECONDS": 1800},
        {"CONSOLE_MACHINE_CLIENT_IDS": ["arbitrary-tenant"]},
        {"ENV": "prod", "CONSOLE_ALLOWED_ORIGIN": "http://127.0.0.1"},
    ],
)
def test_unsafe_identity_configuration_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        configured(**overrides)


def test_human_identity_does_not_require_machine_token_and_loopback_is_explicit() -> None:
    settings = configured()
    assert settings.IDENTITY_DATABASE_URL is not None and settings.CONSOLE_API_TOKEN is None
    assert settings.CONSOLE_COOKIE_SECURE is True
    loopback = configured(
        CONSOLE_ALLOWED_ORIGIN="http://127.0.0.1:13000", CONSOLE_COOKIE_SECURE=False
    )
    assert loopback.CONSOLE_ALLOWED_ORIGIN == "http://127.0.0.1:13000"


def test_bootstrap_cli_requires_deliberate_flag_before_reading_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["bootstrap_console_admin", "--email", "admin@example.test", "--display-name", "Synthetic"],
    )
    password_prompt = Mock(side_effect=AssertionError("must not prompt before deliberate flag"))
    monkeypatch.setattr(bootstrap_console_admin.getpass, "getpass", password_prompt)
    with pytest.raises(SystemExit) as error:
        bootstrap_console_admin.main()
    assert error.value.code == 2
    password_prompt.assert_not_called()


async def test_bootstrap_cli_rejects_missing_or_owner_dsn_before_opening_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = Mock(side_effect=AssertionError("must not open an owner or missing identity connection"))
    monkeypatch.setattr(bootstrap_console_admin, "build_pool", pool)
    for value in ("", "postgresql://csa:synthetic@localhost:1/disposable_fixture"):
        monkeypatch.setenv("IDENTITY_DATABASE_URL", value)
        with pytest.raises(ValueError, match="csa_identity"):
            await bootstrap_console_admin.run(
                "admin@example.test", "Synthetic", "synthetic-pass-123"
            )
    pool.assert_not_called()


def test_add_admin_cli_requires_deliberate_flag_before_reading_passwords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import add_platform_admin

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "add_platform_admin",
            "--approver-email",
            "a@example.test",
            "--email",
            "b@example.test",
            "--display-name",
            "B",
        ],
    )
    prompt = Mock(side_effect=AssertionError("must not prompt before deliberate flag"))
    monkeypatch.setattr(add_platform_admin.getpass, "getpass", prompt)
    with pytest.raises(SystemExit) as error:
        add_platform_admin.main()
    assert error.value.code == 2
    prompt.assert_not_called()


async def test_add_admin_cli_rejects_missing_or_owner_dsn_before_opening_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import add_platform_admin

    pool = Mock(side_effect=AssertionError("must not open an owner or missing identity connection"))
    monkeypatch.setattr(add_platform_admin, "build_pool", pool)
    for value in ("", "postgresql://csa:synthetic@localhost:1/disposable_fixture"):
        monkeypatch.setenv("IDENTITY_DATABASE_URL", value)
        with pytest.raises(ValueError, match="csa_identity"):
            await add_platform_admin.run(
                "a@example.test", "approver-pass-123", "b@example.test", "B", "synthetic-pass-123"
            )
    pool.assert_not_called()
