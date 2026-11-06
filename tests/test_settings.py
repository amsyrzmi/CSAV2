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


def test_voice_key_is_bound_to_exact_groq_endpoint() -> None:
    exact = "https://api.groq.com/openai/v1/audio/transcriptions"
    key = _settings(VOICE_STT_ENDPOINT=exact).voice_transcription_key()
    assert key is not None
    assert key.get_secret_value() == _SECRET
    for endpoint in ["https://speech.example.test/transcriptions", exact + "?extra=1", exact + "/"]:
        assert _settings(VOICE_STT_ENDPOINT=endpoint).voice_transcription_key() is None
    explicit = _settings(VOICE_STT_ENDPOINT=exact, VOICE_STT_API_KEY="synthetic-voice-key")
    explicit_key = explicit.voice_transcription_key()
    assert explicit_key is not None
    assert explicit_key.get_secret_value() == "synthetic-voice-key"
    with pytest.raises(ValidationError, match="requires HTTPS"):
        _settings(ENV="prod", VOICE_STT_ENDPOINT="http://speech.example.test/transcriptions")


def test_interactive_voice_requires_explicit_complete_secure_configuration() -> None:
    assert _settings().voice_session_config() is None
    with pytest.raises(ValidationError, match="requires explicit LiveKit"):
        _settings(VOICE_INTERACTIVE_ENABLED="true")
    configured = {
        "VOICE_INTERACTIVE_ENABLED": "true",
        "VOICE_LIVEKIT_URL": "wss://voice.example.test",
        "VOICE_LIVEKIT_API_KEY": "synthetic-project-key",
        "VOICE_LIVEKIT_API_SECRET": "synthetic-livekit-" + "x" * 32,
        "VOICE_MEDIA_HMAC_SECRET": "synthetic-media-" + "x" * 32,
    }
    assert _settings(**configured).voice_session_config() is not None
    for url in (
        "http://voice.example.test",
        "ws://voice.example.test",
        "wss://user:pass@voice.example.test",
        "wss://voice.example.test?secret=value",
    ):
        with pytest.raises(ValidationError, match="requires WSS"):
            _settings(**{**configured, "VOICE_LIVEKIT_URL": url})
    assert (
        _settings(
            **{**configured, "VOICE_LIVEKIT_URL": "ws://127.0.0.1:7880"}
        ).voice_session_config()
        is not None
    )
    with pytest.raises(ValidationError, match="requires WSS"):
        _settings(**{**configured, "ENV": "prod", "VOICE_LIVEKIT_URL": "ws://127.0.0.1:7880"})


@pytest.mark.parametrize(
    ("kb", "hr"),
    [
        ("/srv/uploads", "/srv/uploads"),
        ("/srv/uploads", "/srv/uploads/"),
        ("/srv/uploads", "/srv/kb/../uploads"),
        ("/srv/uploads", "/srv/uploads/hr"),
        ("/srv/uploads/kb", "/srv/uploads"),
    ],
)
def test_hr_upload_root_must_be_apart_from_kb_upload_root(kb: str, hr: str) -> None:
    # Both name files <uuid>.<ext> under <root>/<client_id>/, so a shared or nested root would
    # let one side's cleanup delete the other's files.
    with pytest.raises(ValidationError, match="HR_UPLOAD_ROOT"):
        _settings(KB_UPLOAD_ROOT=kb, HR_UPLOAD_ROOT=hr)


def test_hr_upload_root_apart_from_kb_upload_root_is_accepted() -> None:
    s = _settings(KB_UPLOAD_ROOT="/srv/kb-uploads", HR_UPLOAD_ROOT="/srv/hr-uploads")
    assert str(s.HR_UPLOAD_ROOT) == "/srv/hr-uploads"
    assert _settings(HR_UPLOAD_ROOT="/srv/hr-uploads").KB_UPLOAD_ROOT is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_upload_roots_are_unset(blank: str) -> None:
    # .env.example leaves both empty. A blank path would otherwise be the current folder for both.
    s = _settings(KB_UPLOAD_ROOT=blank, HR_UPLOAD_ROOT=blank)
    assert s.KB_UPLOAD_ROOT is None
    assert s.HR_UPLOAD_ROOT is None


_S3 = {
    "STORAGE_BACKEND": "s3",
    "S3_BUCKET": "synthetic-bucket",
    "S3_REGION": "us-east-1",
    "S3_ACCESS_KEY_ID": "synthetic-key-id",
    "S3_SECRET_ACCESS_KEY": "synthetic-secret",
}


def test_storage_backend_defaults_to_local() -> None:
    s = _settings()
    assert s.STORAGE_BACKEND == "local"
    assert s.S3_BUCKET is None and s.S3_ACCESS_KEY_ID is None


@pytest.mark.parametrize(
    "missing", ["S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]
)
def test_s3_backend_requires_bucket_region_and_both_keys(missing: str) -> None:
    values = {name: value for name, value in _S3.items() if name != missing}
    message = (
        "STORAGE_BACKEND=s3 requires S3_BUCKET, S3_REGION, "
        "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY"
    )
    with pytest.raises(ValidationError, match=message):
        _settings(**values)
    # A blank value (an empty .env line or secret file) counts as missing.
    with pytest.raises(ValidationError, match=message):
        _settings(**{**values, missing: "  "})


def test_s3_backend_secrets_are_masked() -> None:
    s = _settings(**_S3)
    assert "synthetic-secret" not in repr(s)
    assert s.S3_SECRET_ACCESS_KEY is not None
    assert s.S3_SECRET_ACCESS_KEY.get_secret_value() == "synthetic-secret"


@pytest.mark.parametrize("root", ["KB_UPLOAD_ROOT", "HR_UPLOAD_ROOT"])
def test_s3_backend_refuses_local_upload_roots(root: str) -> None:
    with pytest.raises(ValidationError, match=f"STORAGE_BACKEND=s3 conflicts with {root}"):
        _settings(**_S3, **{root: "/srv/uploads"})


def test_s3_backend_ingest_does_not_need_kb_upload_root() -> None:
    s = _settings(**_S3, ENV="prod", KB_INGEST_ENABLED="true", TEI_ENDPOINT="http://tei.test")
    assert s.KB_INGEST_ENABLED and s.KB_UPLOAD_ROOT is None


def test_local_backend_ingest_still_needs_kb_upload_root() -> None:
    with pytest.raises(ValidationError, match="KB_INGEST_ENABLED requires KB_UPLOAD_ROOT"):
        _settings(KB_INGEST_ENABLED="true")
