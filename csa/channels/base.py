"""Shared channel adapter contracts (LG03)."""

from __future__ import annotations

from typing import Protocol

from csa.contracts.envelope import Envelope


class InboundAdapter(Protocol):
    """Channel-specific verifier/normalizer used by the gateway."""

    channel: str

    def verify(self, supplied_secret: str | None, expected_secret: str) -> bool: ...

    def parse(self, update: object, *, public_id: str) -> object: ...


class ChannelNotSupportedError(ValueError):
    """Raised when a request targets an adapter that is not implemented."""


class NormalizedInbound(Protocol):
    """Minimum shape the gateway needs from a normalized inbound event."""

    idempotency_key: str
    channel_thread: str
    envelope: Envelope
