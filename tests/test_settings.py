"""Settings validation and secret-masking tests (LG01 §3)."""  # LG01

from __future__ import annotations

import base64

import pytest
from csa.core.settings import Settings
from pydantic import ValidationError

_SECRET = "super-secret-groq-token-value"
_VALID_KEY = "base64:" + base64.b64encode(b"\x01" * 32).decode("ascii")


def _base_env() -> dict[str, str]:
    return {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "LG_DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "GROQ_API_KEY": _SECRET,
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": _VALID_KEY,
    }


def _settings(**overrides: str) -> Settings:
    env = _base_env()
    env.update(overrides)
    # _env_file=None disables any ambient .env; _secrets_dir=None avoids /run/secrets.
    return Settings(_env_file=None, _secrets_dir=None, **env)  # type: ignore[call-arg]


def test_valid_settings_construct() -> None:
    s = _settings()
    assert s.ENV == "ci"
    assert s.OTEL_EXPORTER == "console"
    assert s.CHECKPOINT_RETENTION_DAYS == 14
    assert s.credentials_key_bytes() == b"\x01" * 32


def test_missing_required_field_raises() -> None:
    env = _base_env()
    del env["DATABASE_URL"]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, _secrets_dir=None, **env)  # type: ignore[call-arg]


def test_bad_credentials_key_prefix_raises() -> None:
    with pytest.raises(ValidationError):
        _settings(CREDENTIALS_KEY="notbase64:whatever")


def test_bad_credentials_key_wrong_length_raises() -> None:
    short = "base64:" + base64.b64encode(b"\x01" * 16).decode("ascii")
    with pytest.raises(ValidationError):
        _settings(CREDENTIALS_KEY=short)


def test_bad_credentials_key_bad_base64_raises() -> None:
    with pytest.raises(ValidationError):
        _settings(CREDENTIALS_KEY="base64:!!!not-base64!!!")


def test_repr_does_not_leak_secrets() -> None:
    s = _settings()
    rendered = repr(s) + str(s)
    assert _SECRET not in rendered
    assert _VALID_KEY not in rendered


def test_optional_typesafe_defaults_none() -> None:
    s = _settings()
    assert s.TYPESAFE_API_KEY is None


def test_blank_optional_gemini_key_disables_fallback() -> None:
    s = _settings(GOOGLE_AI_API_KEY="")
    assert s.GOOGLE_AI_API_KEY is None
    assert s.GOOGLE_AI_MODEL == "gemini-3.1-flash-lite"
