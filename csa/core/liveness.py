"""Touch-file liveness for the worker and delivery loops (LG01 §9.1).

The worker and delivery processes expose no HTTP port, so compose cannot curl a
``/healthz``. Instead each loop touches a file (updates its mtime) on the
container's ``tmpfs`` ``/tmp`` at the top of **every iteration AND on every idle
tick**. A single ``LOOP_INTERVAL`` (default 5s) governs both the busy cadence
and the idle sleep granularity, so the file is refreshed at least every
``LOOP_INTERVAL`` regardless of workload — freshness tracks "the loop is alive",
not "work happened", which is what stops the healthcheck from flapping.

The compose healthcheck asserts the file's mtime is within ``2 × LOOP_INTERVAL``
(10s with the default). See ``deploy/compose.yml``.
"""  # LG01

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

# Default liveness paths on the container tmpfs (design §9.1).
WORKER_ALIVE_PATH = Path("/tmp/worker.alive")  # noqa: S108 - tmpfs mount in compose
DELIVERY_ALIVE_PATH = Path("/tmp/delivery.alive")  # noqa: S108 - tmpfs mount in compose

# A single interval drives both busy iterations and idle sleep granularity.
DEFAULT_LOOP_INTERVAL = 5.0


def touch(path: Path) -> None:
    """Create ``path`` if missing and bump its mtime to now.

    ``Path.touch`` updates the mtime of an existing file and creates an empty
    one otherwise — exactly the "I'm alive" signal the healthcheck reads.
    """
    path.touch(exist_ok=True)


async def liveness_loop(
    alive_path: Path,
    *,
    loop_interval: float = DEFAULT_LOOP_INTERVAL,
    do_work: Callable[[], Awaitable[bool]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Run the liveness-governed busy/idle loop.

    Each iteration touches ``alive_path`` first, then calls ``do_work`` (if
    given). When ``do_work`` returns ``False`` (no work found) the loop sleeps
    one ``loop_interval`` as an idle tick; the next iteration touches the file
    again, so an idle process still refreshes within one interval.

    ``do_work`` returns ``True`` when it handled work this tick (so the loop
    continues immediately) and ``False`` when idle. Without ``do_work`` every
    tick is an idle tick. ``stop`` lets tests end the loop deterministically;
    in production it is ``None`` and the loop runs until the task is cancelled.
    """
    while True:
        touch(alive_path)
        did_work = await do_work() if do_work is not None else False
        if stop is not None and stop():
            return
        if not did_work:
            await sleep(loop_interval)
