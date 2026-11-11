"""The conversation memory read: every company and channel, a kill switch, and the handoff flag."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from csa.contracts.envelope import Channel
from csa.contracts.state import RECENT_WINDOW, BookingContext, RecentMessage
from csa.core import db
from csa.turn import deps
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_booking_agent import conversation, envelope_for, say
from tests.test_bookings_bot import Shop, a_type, shop

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]
__all__ = ["shop"]  # the fixture, imported so this module can use it


async def seeded(shop: Shop, count: int = 25) -> tuple[uuid.UUID, uuid.UUID]:
    """A customer with a chat of `count` alternating messages (customer first)."""
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    for n in range(count):
        await say(shop, chat, n, "inbound" if n % 2 == 0 else "outbound", f"message {n}")
    return contact, chat


async def no_bookings(shop: Shop) -> None:
    """Turn the booking agent off, so the company has no booking context (which reads messages)."""
    await shop.w.q(
        "UPDATE capability_registry SET client_enabled = false"
        " WHERE client_id = %s AND name = 'booking_agent'",
        shop.tenant,
    )


async def escalate(shop: Shop, chat: uuid.UUID, reason: str, hours_ago: float) -> None:
    await shop.w.q(
        "INSERT INTO escalations (client_id, trace_id, conversation_id, reason, created_at)"
        " VALUES (%s, %s, %s, %s, now() - %s * interval '1 hour')",
        shop.tenant,
        str(uuid.uuid4()),
        str(chat),
        reason,
        hours_ago,
    )


async def test_load_context_reads_recent_for_a_company_without_bookings(shop: Shop) -> None:
    contact, chat = await seeded(shop)
    own_trace = uuid.uuid4()
    await say(shop, chat, 30, "inbound", "the message being answered now", trace=own_trace)
    await no_bookings(shop)
    envelope = envelope_for(shop, contact, chat).model_copy(update={"trace_id": own_trace})
    ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(envelope)
    assert ctx.booking is None
    assert len(ctx.recent) == RECENT_WINDOW
    # The last 20 of messages 0..24, oldest first; the current trace's message is left out.
    assert [m.text for m in ctx.recent] == [f"message {n}" for n in range(5, 25)]
    assert [m.author for m in ctx.recent] == [
        "customer" if n % 2 == 0 else "assistant" for n in range(5, 25)
    ]
    assert all(len(m.text) <= 200 for m in ctx.recent)
    assert ctx.flags["handoff_recent"] is False


async def test_recent_keeps_the_end_of_a_long_assistant_message(shop: Shop) -> None:
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    long = "A" * 150 + "B" * 150 + " the part that matters is at the end."
    await say(shop, chat, 1, "inbound", "hi")
    await say(shop, chat, 2, "outbound", long)
    ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(
        envelope_for(shop, contact, chat)
    )
    reply = ctx.recent[-1]
    assert reply.author == "assistant"
    assert len(reply.text) == 200
    assert reply.text.startswith("A" * 120 + "…")
    assert reply.text.endswith("the part that matters is at the end.")


async def test_recent_empty_when_memory_off(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    await shop.policy({"conversation_memory": False})
    contact, chat = await seeded(shop, 5)
    # No booking context, which reads the same messages for itself (six of them) and would hide
    # whether the memory read ran.
    await no_bookings(shop)
    seen: list[str] = []
    real = db.q.fetch_all

    async def spy(path: str, *args: Any, **kwargs: Any) -> Any:
        seen.append(path)
        return await real(path, *args, **kwargs)

    monkeypatch.setattr(db.q, "fetch_all", spy)
    ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(
        envelope_for(shop, contact, chat)
    )
    assert ctx.recent == []
    assert "turn/recent_messages.sql" not in seen


async def test_recent_read_failure_degrades(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    contact, chat = await seeded(shop, 5)
    dependencies = deps.PostgresTurnDependencies(shop.w.pool)
    envelope = envelope_for(shop, contact, chat)

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the database said aina@example.test")

    monkeypatch.setattr(deps, "_handoff_recent", boom)
    with capture_logs() as logs:
        failed = await dependencies.load_context(envelope)
    assert failed.recent == [] and failed.flags["handoff_recent"] is False
    assert failed.clarify_count == 0  # the turn goes on
    assert any(entry.get("category") == "recent_messages_failed" for entry in logs)
    assert "aina@example.test" not in repr(logs)

    async def slow(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(deps, "_handoff_recent", slow)
    monkeypatch.setattr(deps, "RECENT_BUDGET_S", 0.05)
    with capture_logs() as logs:
        late = await dependencies.load_context(envelope)
    assert late.recent == [] and late.flags["handoff_recent"] is False
    assert late.clarify_count == 0
    assert any(entry.get("category") == "recent_messages_failed" for entry in logs)


async def test_handoff_recent_counts_only_real_handoffs(shop: Shop) -> None:
    dependencies = deps.PostgresTurnDependencies(shop.w.pool)
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    envelope = envelope_for(shop, contact, chat)

    async def flag() -> bool:
        return (await dependencies.load_context(envelope)).flags["handoff_recent"]

    assert await flag() is False
    await escalate(shop, chat, "fail_safe:signals:GroqError", 1)
    await escalate(shop, chat, "approval_expired", 1)
    assert await flag() is False
    await escalate(shop, chat, "human_requested", 30)
    assert await flag() is False
    # A real handoff of another conversation does not count either.
    other = await conversation(shop, contact)
    await escalate(shop, other, "human_requested", 1)
    assert await flag() is False
    await escalate(shop, chat, "human_requested", 2)
    assert await flag() is True


async def test_web_channel_reads_recent(shop: Shop) -> None:
    contact, chat = await seeded(shop, 4)
    envelope = envelope_for(shop, contact, chat).model_copy(update={"channel": Channel.WEB})
    ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(envelope)
    assert ctx.booking is None  # a preview has no booking context
    assert [m.text for m in ctx.recent] == [f"message {n}" for n in range(4)]


async def test_memory_off_marks_the_context_and_skips_the_handoff_read(shop: Shop) -> None:
    # A booking company: its booking context still reads its own six messages for the planner.
    await a_type(shop)
    await shop.policy({"conversation_memory": False})
    contact, chat = await seeded(shop, 4)
    await escalate(shop, chat, "human_requested", 1)
    ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(
        envelope_for(shop, contact, chat)
    )
    assert ctx.recent == []
    assert ctx.flags["memory_off"] is True and ctx.flags["handoff_recent"] is False
    assert ctx.booking is not None and ctx.booking.recent  # the planner keeps its history
    await shop.policy({"conversation_memory": True})
    on = await deps.PostgresTurnDependencies(shop.w.pool).load_context(
        envelope_for(shop, contact, chat)
    )
    assert "memory_off" not in on.flags and on.flags["handoff_recent"] is True


async def test_the_two_context_reads_run_side_by_side(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each read has its own 1 s budget inside the node's 2 s: one after the other, two slow
    # reads would cancel the node and fail the turn.
    contact, chat = await seeded(shop, 2)
    told = [RecentMessage(author="assistant", text="Haircut is free at 3:00 pm.")]
    booking = BookingContext(today="Monday 2031-03-03", timezone="UTC", recent=told)

    async def slow_recent(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(0.9)
        return told, True, False

    async def slow_booking(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(0.9)
        return booking

    dependencies = deps.PostgresTurnDependencies(shop.w.pool)
    monkeypatch.setattr(dependencies, "_recent_messages", slow_recent)
    monkeypatch.setattr(dependencies, "_booking_context", slow_booking)
    started = time.monotonic()
    ctx = await dependencies.load_context(envelope_for(shop, contact, chat))
    assert time.monotonic() - started < 1.6
    assert ctx.recent == told and ctx.flags["handoff_recent"] is True
    assert ctx.booking == booking


async def test_both_context_reads_failing_still_give_a_context(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    await a_type(shop)
    contact, chat = await seeded(shop, 2)

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("down")

    monkeypatch.setattr(deps, "_handoff_recent", boom)
    monkeypatch.setattr(deps, "booking_context", boom)
    with capture_logs() as logs:
        ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(
            envelope_for(shop, contact, chat)
        )
    assert ctx.recent == [] and ctx.booking is None
    assert ctx.flags["handoff_recent"] is False and ctx.clarify_count == 0
    categories = {entry.get("category") for entry in logs}
    assert {"recent_messages_failed", "booking_context_failed"} <= categories


async def _loaded_count(shop: Shop, *, stored: int, hours_ago: float | None) -> int:
    """The unresolved-turns count `load_context` hands the turn, for a chat that stores `stored`
    and whose previous message is `hours_ago` old (None: no earlier message)."""
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    if hours_ago is not None:
        await say(
            shop, chat, 0, "inbound", "earlier", at=datetime.now(UTC) - timedelta(hours=hours_ago)
        )
    own_trace = uuid.uuid4()
    await say(shop, chat, 1, "inbound", "the message being answered", trace=own_trace)
    await shop.w.q("UPDATE conversations SET clarify_count = %s WHERE id = %s", stored, str(chat))
    envelope = envelope_for(shop, contact, chat).model_copy(update={"trace_id": own_trace})
    ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(envelope)
    return ctx.clarify_count


async def test_the_ladder_count_is_zero_when_the_previous_message_is_over_a_day_old(
    shop: Shop,
) -> None:
    assert deps.LADDER_STALE_AFTER == timedelta(hours=24)
    assert await _loaded_count(shop, stored=2, hours_ago=25) == 0


async def test_the_ladder_count_is_kept_when_the_previous_message_is_recent(shop: Shop) -> None:
    assert await _loaded_count(shop, stored=2, hours_ago=1) == 2


async def test_the_ladder_count_is_kept_when_there_is_no_earlier_message(shop: Shop) -> None:
    assert await _loaded_count(shop, stored=2, hours_ago=None) == 2
    assert await _loaded_count(shop, stored=0, hours_ago=None) == 0


async def test_a_recent_staff_reply_does_not_keep_a_stale_ladder_count_alive(shop: Shop) -> None:
    contact = await shop.customer()
    chat = await conversation(shop, contact)
    day_and_a_half = datetime.now(UTC) - timedelta(hours=36)
    await say(shop, chat, 0, "inbound", "earlier", at=day_and_a_half)
    await say(shop, chat, 1, "outbound", "a reminder", at=datetime.now(UTC) - timedelta(hours=2))
    own_trace = uuid.uuid4()
    await say(shop, chat, 2, "inbound", "the message being answered", trace=own_trace)
    await shop.w.q("UPDATE conversations SET clarify_count = 2 WHERE id = %s", str(chat))
    envelope = envelope_for(shop, contact, chat).model_copy(update={"trace_id": own_trace})
    ctx = await deps.PostgresTurnDependencies(shop.w.pool).load_context(envelope)
    assert ctx.clarify_count == 0


async def test_the_ladder_count_goes_stale_with_memory_off_too(shop: Shop) -> None:
    await shop.policy({"conversation_memory": False})
    assert await _loaded_count(shop, stored=2, hours_ago=25) == 0
