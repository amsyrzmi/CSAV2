"""AES-256-GCM envelope crypto tests (LG01 §6)."""  # LG01

from __future__ import annotations

import base64
import logging

import pytest
from csa.core import crypto, settings

_KEY_A = "base64:" + base64.b64encode(b"\x02" * 32).decode("ascii")
_KEY_B = "base64:" + base64.b64encode(b"\x03" * 32).decode("ascii")


def _set_key(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv("ENV", "ci")
    monkeypatch.setenv("DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa")
    monkeypatch.setenv("LG_DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("CREDENTIALS_KEY", key)
    settings.get_settings.cache_clear()


_ROW_ID = "11111111-1111-1111-1111-111111111111"
_AAD = crypto.credential_aad("channel_credentials", "access_token", _ROW_ID)


def test_credential_aad_shape() -> None:
    aad = crypto.credential_aad("channel_credentials", "access_token", "abc")
    assert aad == b"channel_credentials.access_token:abc"


def test_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, _KEY_A)
    token = crypto.encrypt("hunter2", _AAD)
    assert token.startswith("v1:")
    assert crypto.decrypt(token, _AAD) == "hunter2"


def test_tampered_tag_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, _KEY_A)
    token = crypto.encrypt("hunter2", _AAD)
    # Flip a byte in the base64url payload.
    body = bytearray(base64.urlsafe_b64decode(token[3:]))
    body[-1] ^= 0x01
    tampered = "v1:" + base64.urlsafe_b64encode(bytes(body)).decode("ascii")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(tampered, _AAD)


def test_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, _KEY_A)
    token = crypto.encrypt("hunter2", _AAD)
    _set_key(monkeypatch, _KEY_B)
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token, _AAD)


def test_bad_version_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, _KEY_A)
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt("v2:abcd", _AAD)


def test_aad_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, _KEY_A)
    token = crypto.encrypt("hunter2", _AAD)
    other = crypto.credential_aad("channel_credentials", "refresh_token", _ROW_ID)
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token, other)


def test_logs_never_contain_plaintext(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _set_key(monkeypatch, _KEY_A)
    secret = "top-secret-token"
    with caplog.at_level(logging.DEBUG):
        token = crypto.encrypt(secret, _AAD)
        assert crypto.decrypt(token, _AAD) == secret
    assert secret not in caplog.text
