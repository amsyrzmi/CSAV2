"""The browser-side answers for the console shell's two count polls.

The shell (`components/console/shell/useShellCounts.ts`) reads the notification count and the
Inbox queue counts on every page. Browser test modules that fail on any unknown `/console/v1`
call call `shell_counts_answer` first in their route handler and fulfil with the result when it
is not None. A module whose own handler answers these routes keeps priority.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

NOTIFICATION_COUNT_PATH = "/console/v1/notifications/count"
QUEUE_COUNTS_PATH = "/console/v1/inbox/queue/counts"

# Tests that assert the exact requests a page made filter these out.
SHELL_COUNT_PATHS = (NOTIFICATION_COUNT_PATH, QUEUE_COUNTS_PATH)


def shell_counts_answer(path: str) -> dict[str, Any] | None:
    """The JSON body for a shell count poll at `path` (no origin, no query), else None."""
    if path == NOTIFICATION_COUNT_PATH:
        return {"unread": 0}
    if path == QUEUE_COUNTS_PATH:
        return {
            "needs_us": 0,
            "mine": 0,
            "unassigned": 0,
            "overdue": 0,
            "waiting_customer": 0,
            "snoozed": 0,
            "oldest_waiting_at": None,
        }
    return None


async def fulfill_shell_counts(route: Any) -> bool:
    """Answer a shell count poll on a Playwright route; True when it was one (and is handled).

    Call it first in a module's `**/console/v1/**` handler: `if await fulfill_shell_counts(route):
    return`. The poll then never reaches the module's own request log or its unexpected-call list.
    """
    answer = shell_counts_answer(urlsplit(route.request.url).path)
    if answer is None:
        return False
    # Explicit status: some modules wrap the route in a shim whose `fulfill` requires it.
    await route.fulfill(status=200, json=answer)
    return True
