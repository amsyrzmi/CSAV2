"""The worker starts the marketplace sync loop only when TikTok is configured (no database)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from apps.worker import main as worker_main
from csa.marketplace.processor import MarketplaceSyncProcessor

from tests.test_console_identity_config import configured

TIKTOK_ON = {
    "TIKTOK_APP_KEY": "SYNTHETIC-WORKER-APP-KEY",
    "TIKTOK_APP_SECRET": "SYNTHETIC-WORKER-APP-SECRET",
    "TIKTOK_SERVICE_ID": "SYNTHETIC-WORKER-SERVICE-ID",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [{}, {**TIKTOK_ON, "TIKTOK_SERVICE_ID": "disabled"}, {**TIKTOK_ON, "TIKTOK_APP_KEY": ""}],
)
async def test_sync_loop_is_not_started_while_tiktok_is_off(
    overrides: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[Any] = []

    async def fake_loop(processor: Any, **kwargs: Any) -> None:
        started.append(processor)

    monkeypatch.setattr(worker_main, "marketplace_sync_loop", fake_loop)
    settings = configured(**overrides)
    assert settings.tiktok_enabled is False
    assert worker_main.register_marketplace_sync_task(settings, object(), object()) is None  # type: ignore[arg-type]
    await asyncio.sleep(0)
    assert started == []


@pytest.mark.asyncio
async def test_sync_loop_starts_when_tiktok_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[Any, dict[str, Any]]] = []

    async def fake_loop(processor: Any, **kwargs: Any) -> None:
        started.append((processor, kwargs))

    monkeypatch.setattr(worker_main, "marketplace_sync_loop", fake_loop)
    settings = configured(**TIKTOK_ON)
    assert settings.tiktok_enabled is True
    task = worker_main.register_marketplace_sync_task(settings, object(), object())  # type: ignore[arg-type]
    assert task is not None
    await task
    ((processor, kwargs),) = started
    assert isinstance(processor, MarketplaceSyncProcessor)
    assert kwargs["poll_seconds"] > 0
