"""Generic reminders: a timer table, schedule and cancel, the handler registry and the fire job.

This package knows nothing about conversations or the Inbox; see ``pyproject.toml`` for the import
contract that keeps it so. The packages that own a subject register its kinds with
:func:`register_kind`; the ``reminders_fire`` job (:func:`fire_due`) calls them.
"""

from csa.reminders.fire import fire_due
from csa.reminders.registry import (
    Decision,
    HandlerContext,
    Notice,
    Reminder,
    ReminderHandler,
    ReminderRegistry,
    register_kind,
)
from csa.reminders.store import cancel, cancel_skip_locked, schedule

__all__ = [
    "Decision",
    "HandlerContext",
    "Notice",
    "Reminder",
    "ReminderHandler",
    "ReminderRegistry",
    "cancel",
    "cancel_skip_locked",
    "fire_due",
    "register_kind",
    "schedule",
]
