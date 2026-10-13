"""Application-level envelope encryption for DB credentials (LG01 §6).

AES-256-GCM via :class:`cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
Ciphertext is versioned ``v1:<base64url(nonce(12) || ciphertext || tag(16))>``.
The AAD binds a value to its exact location (table, column, row) so a value
cannot be moved between fields or rows.

Credential plaintext and ciphertext are NEVER logged; only the key *version*
(``v1``) and a boolean "decrypt ok" may be logged by callers.
"""  # LG01

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from csa.core.settings import get_settings

_VERSION_PREFIX = "v1:"
_NONCE_BYTES = 12
# AES-GCM tag is 16 bytes and is appended by AESGCM.encrypt to the ciphertext.
_TAG_BYTES = 16


class CryptoError(Exception):
    """Raised on bad version, authentication-tag failure, or key error."""


def credential_aad(table: str, column: str, row_id: str) -> bytes:
    """Build the pinned additional-authenticated-data for a credential cell.

    The construction is byte-identical at encrypt time (migration path) and
    decrypt time (adapter path): ``f"{table}.{column}:{row_id}".encode()`` with
    no normalization. ``row_id`` must be the canonical UUID text form
    (``str(row.id)``), never the raw 16 bytes and never a ``UUID`` object.
    """
    return f"{table}.{column}:{row_id}".encode()


def _key() -> bytes:
    """Return the decoded 32-byte key from settings."""
    return get_settings().credentials_key_bytes()


def encrypt(plaintext: str, aad: bytes) -> str:
    """Encrypt ``plaintext`` under the current key, binding it to ``aad``.

    Returns ``v1:<base64url(nonce || ciphertext || tag)>``.
    """
    try:
        aesgcm = AESGCM(_key())
    except ValueError as exc:  # malformed key length
        raise CryptoError("invalid encryption key") from exc
    nonce = os.urandom(_NONCE_BYTES)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext.encode(), aad)
    blob = nonce + ct_and_tag
    return _VERSION_PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt(token: str, aad: bytes) -> str:
    """Decrypt a ``v1:`` token, verifying ``aad``.

    Raises :class:`CryptoError` on a bad version prefix, a malformed payload, a
    wrong key, a tampered tag, or an AAD mismatch.
    """
    if not token.startswith(_VERSION_PREFIX):
        raise CryptoError("unsupported ciphertext version")
    encoded = token[len(_VERSION_PREFIX) :]
    try:
        blob = base64.urlsafe_b64decode(encoded)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise CryptoError("malformed ciphertext payload") from exc
    if len(blob) < _NONCE_BYTES + _TAG_BYTES:
        raise CryptoError("ciphertext too short")
    nonce, ct_and_tag = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    try:
        aesgcm = AESGCM(_key())
    except ValueError as exc:
        raise CryptoError("invalid encryption key") from exc
    try:
        plaintext = aesgcm.decrypt(nonce, ct_and_tag, aad)
    except InvalidTag as exc:
        raise CryptoError("authentication failed (bad key, tag, or AAD)") from exc
    return plaintext.decode()
