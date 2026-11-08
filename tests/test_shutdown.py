"""A stop signal ends a process loop through its shutdown, and a second one can't cut it short."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator

import pytest
from apps.delivery.main import run_until_stopped as run_delivery_until_stopped
from csa.core.shutdown import run_until_signalled

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def restore_signals() -> AsyncIterator[None]:
    yield
    loop = asyncio.get_running_loop()
    loop.remove_signal_handler(signal.SIGTERM)
    loop.remove_signal_handler(signal.SIGINT)


class _Closable:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name, self.events = name, events

    async def close(self) -> None:
        self.events.append(f"close {self.name}")

    async def aclose(self) -> None:
        self.events.append(f"close {self.name}")


@pytest.mark.usefixtures("restore_signals")
async def test_sigterm_runs_the_cleanup_and_a_second_signal_cannot_cut_it_short() -> None:
    events: list[str] = []
    cleanup_started = asyncio.Event()
    release = asyncio.Event()

    async def cleanup() -> None:
        cleanup_started.set()
        await release.wait()  # a slow drain, still running when the next signals come
        events.append("cleanup done")

    task = asyncio.create_task(run_until_signalled(asyncio.Event().wait(), cleanup))
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(cleanup_started.wait(), 1)
    os.kill(os.getpid(), signal.SIGTERM)
    os.kill(os.getpid(), signal.SIGINT)
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["cleanup done"]


@pytest.mark.usefixtures("restore_signals")
async def test_a_signal_after_the_work_ended_on_its_own_does_not_cut_the_cleanup() -> None:
    cleanup_started = asyncio.Event()
    release = asyncio.Event()
    done: list[str] = []

    async def work() -> None:
        return None

    async def cleanup() -> None:
        cleanup_started.set()
        await release.wait()
        done.append("cleanup done")

    task = asyncio.create_task(run_until_signalled(work(), cleanup))
    await asyncio.wait_for(cleanup_started.wait(), 1)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(0.05)
    release.set()
    await task
    assert done == ["cleanup done"]


@pytest.mark.usefixtures("restore_signals")
async def test_sigterm_stops_delivery_through_the_client_and_pool_close() -> None:
    events: list[str] = []
    task = asyncio.create_task(
        run_delivery_until_stopped(
            asyncio.Event().wait(),
            http=_Closable("http", events),  # type: ignore[arg-type]
            pools=(_Closable("app", events), _Closable("owner", events)),
        )
    )
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["close http", "close app", "close owner"]
