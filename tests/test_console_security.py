"""Password and token guarantees for CSAV2 human authentication."""

from __future__ import annotations

import asyncio
import threading

import pytest
from csa.console import security


async def test_password_hash_is_salted_and_verification_rejects_wrong_password() -> None:
    password = "synthetic-password-0123"
    first, second = await asyncio.gather(
        security.hash_password(password), security.hash_password(password)
    )
    assert first != second
    assert password not in first
    assert await security.verify_password(password, first)
    assert not await security.verify_password("different-password-123", first)
    assert not await security.verify_password(password, None)


async def test_password_work_does_not_run_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def derive(password: str) -> str:
        worker_threads.append(threading.get_ident())
        return "synthetic-persisted-hash"

    monkeypatch.setattr(security, "_hash", derive)
    assert await security.hash_password("synthetic-password-0123") == "synthetic-persisted-hash"
    assert worker_threads and all(thread != event_loop_thread for thread in worker_threads)


async def test_invalid_password_or_corrupt_persisted_hash_surfaces_error() -> None:
    with pytest.raises(ValueError):
        await security.hash_password("too-short")
    with pytest.raises(ValueError):
        await security.verify_password("synthetic-password-0123", "broken-persisted-hash")


def test_session_and_lifecycle_tokens_are_unique_and_hashes_do_not_contain_tokens() -> None:
    tokens = {security.token() for _ in range(64)}
    assert len(tokens) == 64
    assert all(len(token) >= 43 for token in tokens)
    assert all(token not in security.digest(token) for token in tokens)
