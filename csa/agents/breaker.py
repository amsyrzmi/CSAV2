"""Per-tenant, per-agent circuit breaker (LG02 §5, LG05 wrapper step 4)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

CircuitState = Literal["closed", "open", "half_open"]


class BreakerStore(Protocol):
    async def save(
        self,
        client_id: UUID,
        agent: str,
        state: CircuitState,
        fail_count: int,
        opened_at_epoch: float | None,
    ) -> None: ...


@dataclass
class _Circuit:
    state: CircuitState = "closed"
    failures: deque[float] = field(default_factory=deque)
    opened_at: float | None = None
    probe_in_flight: bool = False


class CircuitBreaker:
    """Opens after ``threshold`` failures within ``window_s``; probes after ``open_s``."""

    def __init__(
        self,
        *,
        threshold: int = 5,
        window_s: float = 120.0,
        open_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        store: BreakerStore | None = None,
    ) -> None:
        self._threshold = threshold
        self._window_s = window_s
        self._open_s = open_s
        self._clock = clock
        self._store = store
        self._circuits: dict[tuple[UUID, str], _Circuit] = {}

    def state(self, client_id: UUID, agent: str) -> CircuitState:
        return self._circuits.get((client_id, agent), _Circuit()).state

    async def allow(self, client_id: UUID, agent: str) -> bool:
        circuit = self._circuits.setdefault((client_id, agent), _Circuit())
        if circuit.state == "closed":
            return True
        now = self._clock()
        if circuit.state == "open":
            if circuit.opened_at is None or now - circuit.opened_at < self._open_s:
                return False
            await self._transition(client_id, agent, circuit, "half_open")
        if circuit.probe_in_flight:
            return False
        circuit.probe_in_flight = True
        return True

    async def record_success(self, client_id: UUID, agent: str) -> None:
        circuit = self._circuits.setdefault((client_id, agent), _Circuit())
        circuit.probe_in_flight = False
        circuit.failures.clear()
        if circuit.state != "closed":
            circuit.opened_at = None
            await self._transition(client_id, agent, circuit, "closed")

    async def record_failure(self, client_id: UUID, agent: str) -> None:
        circuit = self._circuits.setdefault((client_id, agent), _Circuit())
        now = self._clock()
        circuit.probe_in_flight = False
        if circuit.state == "half_open":
            circuit.opened_at = now
            await self._transition(client_id, agent, circuit, "open")
            return
        circuit.failures.append(now)
        while circuit.failures and now - circuit.failures[0] > self._window_s:
            circuit.failures.popleft()
        if circuit.state == "closed" and len(circuit.failures) >= self._threshold:
            circuit.opened_at = now
            await self._transition(client_id, agent, circuit, "open")
        elif self._store is not None:
            await self._store.save(client_id, agent, circuit.state, len(circuit.failures), None)

    async def _transition(
        self,
        client_id: UUID,
        agent: str,
        circuit: _Circuit,
        state: CircuitState,
    ) -> None:
        previous = circuit.state
        circuit.state = state
        logger.warning(
            "circuit_transition",
            client_id=str(client_id),
            agent=agent,
            previous=previous,
            state=state,
            notify=state == "open",
        )
        if self._store is not None:
            await self._store.save(
                client_id,
                agent,
                state,
                len(circuit.failures),
                circuit.opened_at,
            )
