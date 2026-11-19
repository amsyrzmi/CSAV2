"""The shared browser-test answer for the shell's two count polls."""

from __future__ import annotations

import pytest

from tests.shell_counts_stub import SHELL_COUNT_PATHS, fulfill_shell_counts, shell_counts_answer

NOTIFICATIONS = "/console/v1/notifications/count"
QUEUE = "/console/v1/inbox/queue/counts"


def test_the_two_shell_paths_are_listed() -> None:
    assert SHELL_COUNT_PATHS == (NOTIFICATIONS, QUEUE)


def test_notification_count_is_zero_unread() -> None:
    assert shell_counts_answer(NOTIFICATIONS) == {"unread": 0}


def test_queue_counts_are_zeroed_with_every_field() -> None:
    answer = shell_counts_answer(QUEUE)
    assert answer is not None
    assert answer["needs_us"] == 0
    assert set(answer) == {
        "needs_us",
        "mine",
        "unassigned",
        "overdue",
        "waiting_customer",
        "snoozed",
        "oldest_waiting_at",
    }
    assert answer["oldest_waiting_at"] is None


def test_every_other_path_is_not_answered() -> None:
    assert shell_counts_answer("/console/v1/inbox/conversations") is None
    assert shell_counts_answer("/console/v1/notifications") is None
    assert shell_counts_answer("/console/v1/notifications/read") is None
    assert shell_counts_answer("") is None


def test_a_query_string_or_origin_does_not_change_the_answer() -> None:
    assert shell_counts_answer(f"{NOTIFICATIONS}?x=1") is None
    assert shell_counts_answer(f"http://127.0.0.1:1{NOTIFICATIONS}") is None


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = type("Request", (), {"url": url})()
        self.fulfilled: list[dict[str, object]] = []

    async def fulfill(self, **kwargs: object) -> None:
        self.fulfilled.append(kwargs)


@pytest.mark.asyncio
async def test_fulfill_shell_counts_answers_a_poll_and_leaves_other_calls() -> None:
    poll = FakeRoute(f"http://127.0.0.1:3000{NOTIFICATIONS}")
    assert await fulfill_shell_counts(poll) is True
    assert poll.fulfilled == [{"json": {"unread": 0}}]
    other = FakeRoute("http://127.0.0.1:3000/console/v1/inbox/conversations?limit=1")
    assert await fulfill_shell_counts(other) is False
    assert other.fulfilled == []
    # A query string does not hide a poll from the handler.
    queried = FakeRoute(f"http://127.0.0.1:3000{QUEUE}?x=1")
    assert await fulfill_shell_counts(queried) is True
