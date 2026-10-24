"""Application settings loaded from env and Docker secrets (LG01 §3).

No secret literal lives in the repo. Every secret is read from the environment
or a Docker secret file under ``/run/secrets``. ``Settings`` is constructed once
per process via the cached :func:`get_settings`.
"""  # LG01

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_CREDENTIALS_KEY_PREFIX = "base64:"
_CREDENTIALS_KEY_BYTES = 32


class Settings(BaseSettings):
    """Runtime configuration for every CSA process.

    Values resolve in order: a Docker secret file (``/run/secrets/<field>``),
    then the environment, then the field default. Missing required fields raise
    :class:`pydantic.ValidationError` at construction so a misconfigured process
    exits before serving traffic.
    """

    model_config = SettingsConfigDict(
        secrets_dir="/run/secrets",
        env_file=None,
        extra="ignore",
        case_sensitive=True,
    )

    ENV: Literal["dev", "ci", "prod"]

    DATABASE_URL: PostgresDsn
    LG_DATABASE_URL: PostgresDsn

    GROQ_API_KEY: SecretStr
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    HR_ASSISTANT_ENABLED: bool = False
    # Staff email assistant (draft-only Gmail). The OAuth client is shared with future
    # Google integrations; tokens live encrypted per connecting user, never in settings.
    EMAIL_ASSISTANT_ENABLED: bool = False
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: SecretStr | None = None
    GOOGLE_OAUTH_REDIRECT_URI: str | None = None
    GOOGLE_AI_API_KEY: SecretStr | None = None
    GOOGLE_AI_MODEL: str = "gemini-3.1-flash-lite"
    DEV_STAFF_CHAT_ID: str | None = None
    CONSOLE_API_TOKEN: SecretStr | None = None
    # Human identity uses its own least-privilege role; never use DATABASE_URL here.
    IDENTITY_DATABASE_URL: PostgresDsn | None = None
    CONSOLE_ALLOWED_ORIGIN: str | None = None
    CONSOLE_COOKIE_SECURE: bool = True
    CONSOLE_SESSION_IDLE_SECONDS: int = Field(default=1800, ge=60, le=86400)
    CONSOLE_SESSION_ABSOLUTE_SECONDS: int = Field(default=43200, ge=300, le=604800)
    # Service token reads are limited to an explicitly configured tenant allowlist.
    CONSOLE_MACHINE_CLIENT_IDS: list[str] = Field(default_factory=list)
    TYPESAFE_API_KEY: SecretStr | None = None
    TEI_ENDPOINT: HttpUrl | None = None
    KB_EMBED_MODEL_LABEL: str = Field(default="tei-384", min_length=1, max_length=64)
    # E5-family models (e.g. intfloat/multilingual-e5-small) expect these prefixes.
    TEI_QUERY_PREFIX: str = "query: "
    TEI_PASSAGE_PREFIX: str = "passage: "
    # Console uploads land in KB_UPLOAD_ROOT/<client_id>/ and are queued in kb_ingest_jobs.
    KB_UPLOAD_ROOT: Path | None = None
    KB_INGEST_ENABLED: bool = False
    KB_INGEST_POLL_SECONDS: float = Field(default=10.0, gt=0)
    KB_MAX_FILES_PER_JOB: int = Field(default=3, ge=1, le=20)
    KB_MAX_FILE_BYTES: int = Field(default=10 * 1024 * 1024, ge=1024)
    TIKTOK_APP_KEY: str | None = None
    TIKTOK_APP_SECRET: SecretStr | None = None
    INVOICE_SIGNING_SECRET: SecretStr | None = None
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: SecretStr | None = None
    LANGFUSE_BASE_URL: str | None = None

    # Local adapter bootstrap inputs; normal channel traffic reads encrypted
    # credentials from channel_credentials instead.
    TELEGRAM_BOT_TOKEN: SecretStr | None = None
    TELEGRAM_WEBHOOK_SECRET: SecretStr | None = None

    PUBLIC_BASE_URL: HttpUrl

    CREDENTIALS_KEY: SecretStr

    OTEL_EXPORTER: Literal["console", "otlp"] = "console"
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None

    TURN_ENGINE: Literal["echo", "coordinator"] = "echo"
    CHANNEL_MODE: Literal["polling", "webhook"] = "webhook"
    CHECKPOINT_RETENTION_DAYS: int = 14

    @field_validator("CONSOLE_API_TOKEN", mode="before")
    @classmethod
    def _validate_console_token(cls, value: object) -> object:
        """Blank means disabled; a set token must be long enough to resist guessing."""
        if value is None or value == "":
            return None
        if isinstance(value, str) and len(value) < 32:
            raise ValueError("CONSOLE_API_TOKEN must be at least 32 characters")
        return value

    @field_validator("GOOGLE_AI_API_KEY", mode="before")
    @classmethod
    def _validate_google_api_key(cls, value: object) -> object:
        """Treat an empty optional key as disabled, as in the checked-in env template."""
        return None if value == "" else value

    @field_validator("CREDENTIALS_KEY")
    @classmethod
    def _validate_credentials_key(cls, value: SecretStr) -> SecretStr:
        """Enforce the single ``base64:<base64 of exactly 32 raw bytes>`` rule.

        There is no hex form and no raw-bytes form, so two readers cannot
        diverge on the encoding.
        """
        raw = value.get_secret_value()
        if not raw.startswith(_CREDENTIALS_KEY_PREFIX):
            raise ValueError("CREDENTIALS_KEY must be 'base64:<base64 of exactly 32 raw bytes>'")
        encoded = raw[len(_CREDENTIALS_KEY_PREFIX) :]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("CREDENTIALS_KEY base64 payload is not valid base64") from exc
        if len(decoded) != _CREDENTIALS_KEY_BYTES:
            raise ValueError(
                f"CREDENTIALS_KEY must decode to exactly {_CREDENTIALS_KEY_BYTES} bytes, "
                f"got {len(decoded)}"
            )
        return value

    @model_validator(mode="after")
    def _validate_email_assistant(self) -> Settings:
        if not self.EMAIL_ASSISTANT_ENABLED:
            return self
        if not (
            self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET and self.GOOGLE_OAUTH_REDIRECT_URI
        ):
            raise ValueError(
                "EMAIL_ASSISTANT_ENABLED requires GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET "
                "and GOOGLE_OAUTH_REDIRECT_URI"
            )
        redirect = urlsplit(self.GOOGLE_OAUTH_REDIRECT_URI)
        if (
            redirect.scheme not in {"http", "https"}
            or not redirect.hostname
            or not redirect.path.endswith("/console/v1/email/oauth/callback")
            or (self.ENV == "prod" and redirect.scheme != "https")
        ):
            raise ValueError(
                "GOOGLE_OAUTH_REDIRECT_URI must be the console origin plus "
                "/console/v1/email/oauth/callback (HTTPS in prod)"
            )
        return self

    @model_validator(mode="after")
    def _validate_kb_ingest(self) -> Settings:
        if self.KB_INGEST_ENABLED and self.KB_UPLOAD_ROOT is None:
            raise ValueError("KB_INGEST_ENABLED requires KB_UPLOAD_ROOT")
        if self.ENV == "prod" and self.KB_INGEST_ENABLED and self.TEI_ENDPOINT is None:
            raise ValueError("KB_INGEST_ENABLED in prod requires TEI_ENDPOINT")
        return self

    @model_validator(mode="after")
    def _validate_console(self) -> Settings:
        from uuid import UUID

        for client_id in self.CONSOLE_MACHINE_CLIENT_IDS:
            UUID(client_id)
        if self.IDENTITY_DATABASE_URL is None:
            return self
        if any(host["username"] != "csa_identity" for host in self.IDENTITY_DATABASE_URL.hosts()):
            raise ValueError("IDENTITY_DATABASE_URL must use the csa_identity role")
        identity_hosts = {
            (host["host"], host["port"] or 5432) for host in self.IDENTITY_DATABASE_URL.hosts()
        }
        domain_hosts = {(host["host"], host["port"] or 5432) for host in self.DATABASE_URL.hosts()}
        if (
            identity_hosts != domain_hosts
            or self.IDENTITY_DATABASE_URL.path != self.DATABASE_URL.path
        ):
            raise ValueError("identity and domain pools must target the same database")
        if not self.CONSOLE_ALLOWED_ORIGIN:
            raise ValueError("IDENTITY_DATABASE_URL requires CONSOLE_ALLOWED_ORIGIN")
        origin = urlsplit(self.CONSOLE_ALLOWED_ORIGIN)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("CONSOLE_ALLOWED_ORIGIN must be a single HTTP(S) origin")
        loopback = origin.hostname in {"localhost", "127.0.0.1", "::1"}
        if origin.scheme != "https" and not (self.ENV != "prod" and loopback):
            raise ValueError("console requires HTTPS except loopback dev/ci")
        if not self.CONSOLE_COOKIE_SECURE and not (self.ENV != "prod" and loopback):
            raise ValueError("insecure console cookies are allowed only on loopback dev/ci")
        if self.CONSOLE_SESSION_IDLE_SECONDS > self.CONSOLE_SESSION_ABSOLUTE_SECONDS:
            raise ValueError("console idle expiry must not exceed absolute expiry")
        self.CONSOLE_ALLOWED_ORIGIN = self.CONSOLE_ALLOWED_ORIGIN.rstrip("/")
        return self

    def credentials_key_bytes(self) -> bytes:
        """Return the decoded 32-byte AES-256-GCM key.

        Validated by :meth:`_validate_credentials_key`, so decoding is total.
        """
        raw = self.CREDENTIALS_KEY.get_secret_value()
        return base64.b64decode(raw[len(_CREDENTIALS_KEY_PREFIX) :], validate=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance.

    In ``dev`` the ``.env.local`` file is consulted unless ``NO_LOCAL_ENV=1``;
    other environments never read a dotenv file. Environment values take precedence.
    """
    import os

    if os.environ.get("ENV") == "dev" and os.environ.get("NO_LOCAL_ENV") != "1":
        return Settings(_env_file=".env.local")  # type: ignore[call-arg]
    return Settings()  # type: ignore[call-arg]
