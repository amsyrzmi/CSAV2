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


def test_langfuse_pseudonym_key_is_derived_from_the_credentials_key_when_unset() -> None:
    derived = _settings().langfuse_pseudonym_key()
    assert len(derived) == 32
    assert derived == _settings(LANGFUSE_PSEUDONYM_KEY="").langfuse_pseudonym_key()
    assert derived != b"\x01" * 32  # a sub-key, never the credentials key itself
    other = "base64:" + base64.b64encode(b"\x02" * 32).decode("ascii")
    assert _settings(CREDENTIALS_KEY=other).langfuse_pseudonym_key() != derived


def test_langfuse_pseudonym_key_uses_the_dedicated_setting_and_refuses_a_short_one() -> None:
    explicit = "p" * 40
    assert _settings(LANGFUSE_PSEUDONYM_KEY=explicit).langfuse_pseudonym_key() == explicit.encode()
    with pytest.raises(ValidationError):
        _settings(LANGFUSE_PSEUDONYM_KEY="too-short")


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


def test_training_root_blank_is_unset() -> None:
    assert _settings().TRAINING_UPLOAD_ROOT is None
    assert _settings(TRAINING_UPLOAD_ROOT="").TRAINING_UPLOAD_ROOT is None
    assert _settings(TRAINING_UPLOAD_ROOT="   ").TRAINING_UPLOAD_ROOT is None


def test_training_root_apart_from_kb_and_hr_roots_is_accepted() -> None:
    s = _settings(
        KB_UPLOAD_ROOT="/srv/kb-uploads",
        HR_UPLOAD_ROOT="/srv/hr-uploads",
        TRAINING_UPLOAD_ROOT="/srv/training-uploads",
    )
    assert str(s.TRAINING_UPLOAD_ROOT) == "/srv/training-uploads"
    assert _settings(TRAINING_UPLOAD_ROOT="/srv/training-uploads").KB_UPLOAD_ROOT is None


@pytest.mark.parametrize("other", ["KB_UPLOAD_ROOT", "HR_UPLOAD_ROOT"])
@pytest.mark.parametrize(
    ("training", "root"),
    [
        ("/srv/uploads", "/srv/uploads"),
        ("/srv/uploads", "/srv/uploads/"),
        ("/srv/uploads", "/srv/kb/../uploads"),
        ("/srv/uploads/training", "/srv/uploads"),
        ("/srv/uploads", "/srv/uploads/training"),
    ],
)
def test_training_root_must_not_overlap_kb_or_hr_root(training: str, root: str, other: str) -> None:
    # Training files are <uuid>.pdf under <root>/<client_id>/ and its sweep deletes the
    # uuid-named files it does not know, so a shared or nested root would let it delete the
    # other store's files.
    with pytest.raises(ValidationError, match="TRAINING_UPLOAD_ROOT must not be, contain or sit"):
        _settings(TRAINING_UPLOAD_ROOT=training, **{other: root})


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


@pytest.mark.parametrize("root", ["KB_UPLOAD_ROOT", "HR_UPLOAD_ROOT", "TRAINING_UPLOAD_ROOT"])
def test_s3_backend_refuses_local_upload_roots(root: str) -> None:
    with pytest.raises(ValidationError, match=f"STORAGE_BACKEND=s3 conflicts with {root}"):
        _settings(**_S3, **{root: "/srv/uploads"})


def test_s3_backend_refuses_training_root() -> None:
    message = "STORAGE_BACKEND=s3 conflicts with TRAINING_UPLOAD_ROOT; unset TRAINING_UPLOAD_ROOT"
    with pytest.raises(ValidationError, match=message):
        _settings(**_S3, TRAINING_UPLOAD_ROOT="/srv/training-uploads")


def test_s3_backend_ingest_does_not_need_kb_upload_root() -> None:
    s = _settings(**_S3, ENV="prod", KB_INGEST_ENABLED="true", TEI_ENDPOINT="http://tei.test")
    assert s.KB_INGEST_ENABLED and s.KB_UPLOAD_ROOT is None


def test_local_backend_ingest_still_needs_kb_upload_root() -> None:
    with pytest.raises(ValidationError, match="KB_INGEST_ENABLED requires KB_UPLOAD_ROOT"):
        _settings(KB_INGEST_ENABLED="true")


_GOOGLE = {
    "GOOGLE_CLIENT_ID": "synthetic-client.example.test",
    "GOOGLE_CLIENT_SECRET": "synthetic-client-secret-value",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://console.example.test/console/v1/email/oauth/callback",
}


def test_the_booking_google_flags_default_off() -> None:
    s = _settings()
    assert s.BOOKINGS_GOOGLE_CALENDAR_ENABLED is False
    assert s.BOOKINGS_GOOGLE_MEET_ENABLED is False


def test_meet_without_the_calendar_is_refused() -> None:
    with pytest.raises(ValidationError, match="BOOKINGS_GOOGLE_MEET_ENABLED requires"):
        _settings(BOOKINGS_GOOGLE_MEET_ENABLED="true", **_GOOGLE)


@pytest.mark.parametrize(
    "missing", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI"]
)
def test_the_calendar_without_the_google_client_is_refused(missing: str) -> None:
    env = {k: v for k, v in _GOOGLE.items() if k != missing}
    with pytest.raises(ValidationError, match="BOOKINGS_GOOGLE_CALENDAR_ENABLED requires"):
        _settings(BOOKINGS_GOOGLE_CALENDAR_ENABLED="true", **env)


def test_the_calendar_and_meet_with_the_google_client_construct() -> None:
    s = _settings(
        BOOKINGS_GOOGLE_CALENDAR_ENABLED="true", BOOKINGS_GOOGLE_MEET_ENABLED="true", **_GOOGLE
    )
    assert s.BOOKINGS_GOOGLE_CALENDAR_ENABLED and s.BOOKINGS_GOOGLE_MEET_ENABLED


def test_the_calendar_callback_is_the_console_origin_plus_its_own_path() -> None:
    s = _settings(BOOKINGS_GOOGLE_CALENDAR_ENABLED="true", **_GOOGLE)
    assert s.bookings_calendar_redirect_uri() == (
        "https://console.example.test/console/v1/bookings/calendar/callback"
    )
    assert _settings().bookings_calendar_redirect_uri() is None


def test_the_calendar_origin_must_be_a_web_address() -> None:
    env = {**_GOOGLE, "GOOGLE_OAUTH_REDIRECT_URI": "not-a-url"}
    with pytest.raises(ValidationError, match="web address"):
        _settings(BOOKINGS_GOOGLE_CALENDAR_ENABLED="true", **env)


def test_assistant_decider_defaults() -> None:
    s = _settings()
    assert s.ASSISTANT_DECIDER_EMAIL == "auto"
    assert s.ASSISTANT_DECIDER_ROUTING == "auto"
    assert s.ASSISTANT_DECIDER_INBOUND_INJECTION == "auto"
    assert s.ASSISTANT_JEV_FLOOR_NOUL == 0.7
    assert s.ASSISTANT_JEV_FLOOR_CHOICE == 0.7
    assert s.ASSISTANT_DECIDER_AUDIT_SAMPLE == 0.10


@pytest.mark.parametrize("area", ["EMAIL", "ROUTING", "INBOUND_INJECTION"])
@pytest.mark.parametrize("mode", ["shadow", "cascade"])
def test_explicit_cascade_without_a_key_refuses_startup(area: str, mode: str) -> None:
    with pytest.raises(ValidationError, match="TYPESAFE_API_KEY"):
        _settings(**{f"ASSISTANT_DECIDER_{area}": mode})
    # With a key the same mode is accepted; auto and llm never need one.
    assert _settings(**{f"ASSISTANT_DECIDER_{area}": mode}, TYPESAFE_API_KEY="k")
    assert _settings(**{f"ASSISTANT_DECIDER_{area}": "llm"})
    assert _settings(**{f"ASSISTANT_DECIDER_{area}": "auto"})


@pytest.mark.parametrize(
    "name",
    ["ASSISTANT_JEV_FLOOR_NOUL", "ASSISTANT_JEV_FLOOR_CHOICE", "ASSISTANT_DECIDER_AUDIT_SAMPLE"],
)
def test_assistant_jev_fractions_are_bounded(name: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{name: "1.5"})
    with pytest.raises(ValidationError):
        _settings(**{name: "-0.1"})


def test_assistant_draft_model_defaults_to_the_same_model() -> None:
    assert _settings().ASSISTANT_DRAFT_MODEL is None
    assert _settings(ASSISTANT_DRAFT_MODEL="synthetic/drafter").ASSISTANT_DRAFT_MODEL == (
        "synthetic/drafter"
    )


def test_customer_decide_mode_default_is_llm_and_bounded() -> None:
    assert _settings().CUSTOMER_DECIDE_MODE_DEFAULT == "llm"
    # cascade needs no key to start: with no Jev provider the customer path runs on the LLM.
    assert _settings(CUSTOMER_DECIDE_MODE_DEFAULT="cascade").CUSTOMER_DECIDE_MODE_DEFAULT == (
        "cascade"
    )
    for mode in ("jev", "shadow", "auto", ""):
        with pytest.raises(ValidationError):
            _settings(CUSTOMER_DECIDE_MODE_DEFAULT=mode)


@pytest.mark.parametrize("placeholder", ["", "   ", "disabled", "staging-disabled-no-model-access"])
def test_a_jev_placeholder_reads_as_no_key_never_an_invalid_one(placeholder: str) -> None:
    from scripts.staging_secrets import DISABLED_MODEL_KEY

    assert DISABLED_MODEL_KEY == "staging-disabled-no-model-access"
    settings = _settings(TYPESAFE_API_KEY=placeholder)
    assert settings.TYPESAFE_API_KEY is None
    # So no area is switched on by it: auto stays on the LLM, and an explicit cascade refuses.
    with pytest.raises(ValidationError, match="TYPESAFE_API_KEY"):
        _settings(TYPESAFE_API_KEY=placeholder, ASSISTANT_DECIDER_EMAIL="cascade")


def test_a_real_jev_key_is_kept() -> None:
    settings = _settings(TYPESAFE_API_KEY="synthetic-jev-key")
    assert settings.TYPESAFE_API_KEY is not None
    assert settings.TYPESAFE_API_KEY.get_secret_value() == "synthetic-jev-key"
