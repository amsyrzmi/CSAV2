"""Stop a long-running process on SIGTERM or SIGINT so its shutdown runs to the end.

The worker and delivery processes are PID 1 in their containers, where a signal with no handler is
ignored: `docker stop` would end in SIGKILL after the grace, and nothing after the loop would run.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def stop_on_signals(task: asyncio.Task[Any]) -> Callable[[], None]:
    """Cancel `task` on the first SIGTERM or SIGINT, so its `finally` runs.

    Returns `neutralise`, which swaps both handlers for ones that only log. The first signal calls
    it before cancelling, and a shutdown that began without a signal calls it itself: either way a
    second signal can't cancel the task again and cut its shutdown short.
    """
    loop = asyncio.get_running_loop()

    def ignore(number: signal.Signals) -> None:
        logger.warning("shutdown_signal_ignored", signal=number.name)

    def neutralise() -> None:
        for number in STOP_SIGNALS:
            loop.add_signal_handler(number, ignore, number)

    def stop() -> None:
        neutralise()
        task.cancel()

    for number in STOP_SIGNALS:
        loop.add_signal_handler(number, stop)
    return neutralise


async def run_until_signalled(work: Awaitable[Any], cleanup: Callable[[], Awaitable[None]]) -> None:
    """Run `work` until it ends or a stop signal comes, then run `cleanup` to the end.

    A stop cancels the calling task, so the `CancelledError` reaches the caller after `cleanup`.
    """
    neutralise = stop_on_signals(asyncio.current_task())  # type: ignore[arg-type]
    try:
        await work
    finally:
        neutralise()
        await cleanup()
