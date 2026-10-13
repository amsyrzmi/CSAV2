"""Uniform agent interface shared by every spoke (LG05)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from csa.contracts.callbacks import PendingActionStore
from csa.contracts.facts import AgentResult
from csa.contracts.plan import RiskClass

SideEffects = Literal["none", "reversible", "irreversible"]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AgentContext:
    """Per-task execution context; ids come from the envelope, never from a model."""

    client_id: UUID
    contact_id: UUID
    trace_id: UUID
    conversation_id: UUID | None
    idempotency_key: str
    channel: str
    pool: AsyncConnectionPool
    pending_actions: PendingActionStore
    timezone: str = "Asia/Kuala_Lumpur"
    locale: str = "en"
    callback_action_id: UUID | None = None
    now: Callable[[], datetime] = utc_now


class Agent(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def Input(self) -> type[BaseModel]: ...  # noqa: N802
    @property
    def side_effects(self) -> SideEffects: ...
    @property
    def timeout_ms(self) -> int: ...
    @property
    def max_retries(self) -> int: ...

    def risk(self, inp: BaseModel) -> RiskClass:
        """Risk class of this validated input's action."""
        ...

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult: ...
