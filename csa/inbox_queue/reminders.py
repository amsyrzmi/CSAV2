"""The Inbox's reminder kinds (plan 2a): registered with csa.reminders when the worker starts.

Task 5 of the plan fills this in with the six Inbox kinds and their handlers; until then it
registers nothing, so the worker's call is in place and the fire job has no Inbox kind to fire.
"""

from __future__ import annotations


def register_inbox_reminder_kinds() -> None:
    """Register the Inbox's reminder kinds in the process registry (none yet)."""
