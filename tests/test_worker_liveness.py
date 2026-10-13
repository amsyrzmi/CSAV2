"""Worker/delivery touch-file liveness tests (LG01 §9.1).

The loop must create and refresh its alive file within one ``LOOP_INTERVAL`` on
BOTH a busy iteration (``do_work`` returned ``True``) and an idle tick
(``do_work`` returned ``False`` / no work), so an idle process never looks dead.
"""  # LG01

from __future__ import annotations

from pathlib import Path

import pytest
from csa.core.liveness import DEFAULT_LOOP_INTERVAL, liveness_loop

pytestmark = pytest.mark.asyncio


async def test_loop_creates_alive_file_on_first_tick(tmp_path: Path) -> None:
    """The alive file does not exist, then exists after the first iteration."""
    alive = tmp_path / "worker.alive"
    assert not alive.exists()

    ticks = 0

    def stop() -> bool:
        nonlocal ticks
        ticks += 1
        return ticks >= 1  # stop right after the first touch

    async def no_sleep(_seconds: float) -> None:  # never reached (stop fires first)
        raise AssertionError("idle sleep should not run before stop")

    await liveness_loop(alive, sleep=no_sleep, stop=stop)
    assert alive.exists()


async def test_loop_refreshes_on_busy_iteration(tmp_path: Path) -> None:
    """A busy iteration (do_work True) refreshes the mtime and skips the sleep."""
    alive = tmp_path / "worker.alive"
    alive.touch()
    # Backdate well beyond 2x the interval so a refresh is observable.
    stale = alive.stat().st_mtime - (10 * DEFAULT_LOOP_INTERVAL)
    import os

    os.utime(alive, (stale, stale))

    iterations = 0

    async def do_work() -> bool:
        nonlocal iterations
        iterations += 1
        return True  # busy: loop continues immediately, no idle sleep

    slept = False

    async def record_sleep(_seconds: float) -> None:
        nonlocal slept
        slept = True

    def stop() -> bool:
        return iterations >= 2  # let two busy iterations run

    await liveness_loop(alive, do_work=do_work, sleep=record_sleep, stop=stop)

    assert alive.stat().st_mtime > stale  # refreshed on a busy tick
    assert slept is False  # busy iterations never hit the idle sleep


async def test_loop_refreshes_on_idle_tick(tmp_path: Path) -> None:
    """An idle tick (do_work False) still refreshes within one interval."""
    alive = tmp_path / "worker.alive"
    sleeps: list[float] = []

    async def do_work() -> bool:
        return False  # idle: the loop should sleep one interval, then re-touch

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    ticks = 0

    def stop() -> bool:
        nonlocal ticks
        ticks += 1
        return ticks >= 2  # two idle ticks

    await liveness_loop(alive, do_work=do_work, sleep=record_sleep, stop=stop)

    assert alive.exists()  # touched on every idle tick
    # The idle sleep granularity is exactly one LOOP_INTERVAL.
    assert sleeps and all(s == DEFAULT_LOOP_INTERVAL for s in sleeps)
