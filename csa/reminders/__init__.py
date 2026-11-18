"""Generic reminders: a timer table, schedule and cancel (the fire job and handlers come later).

This package knows nothing about conversations or the Inbox; see ``pyproject.toml`` for the import
contract that keeps it so.
"""

from csa.reminders.store import cancel, schedule

__all__ = ["cancel", "schedule"]
