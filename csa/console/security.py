"""Opaque random secrets and bounded, off-event-loop scrypt password work."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from weakref import WeakKeyDictionary

# scrypt N=2**17,r=8,p=1: 128 MiB memory; at most two concurrent hashes/process.
_SCRYPT_N = 2**17
_PASSWORD_WORK: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    WeakKeyDictionary()
)


def token() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=8,
        p=1,
        maxmem=256 * 1024 * 1024,
        dklen=32,
    )


def _hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = _derive(password, salt)
    return (
        "scrypt$131072$8$1$"
        + base64.b64encode(salt).decode()
        + "$"
        + base64.b64encode(key).decode()
    )


def _verify(password: str, encoded: str | None) -> bool:
    if encoded is None:
        _derive(password, b"\x00" * 16)
        return False
    # Corrupt persisted configuration is an operational error, never a login fallback.
    algorithm, n, r, p, salt, expected = encoded.split("$")
    if (algorithm, n, r, p) != ("scrypt", "131072", "8", "1"):
        raise ValueError("unsupported persisted password hash")
    actual = _derive(password, base64.b64decode(salt, validate=True))
    return hmac.compare_digest(actual, base64.b64decode(expected, validate=True))


async def _offload[T](function: Callable[..., T], *args: object) -> T:
    # A semaphore belongs to its event loop; tests and CLI each own their loop.
    loop = asyncio.get_running_loop()
    semaphore = _PASSWORD_WORK.setdefault(loop, asyncio.Semaphore(2))
    async with semaphore:
        work = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # A disconnected request cannot free capacity while its hash still runs.
            await work
            raise


async def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("password must contain between 12 and 1024 characters")
    return await _offload(_hash, password)


async def verify_password(password: str, encoded: str | None) -> bool:
    return await _offload(_verify, password, encoded)
