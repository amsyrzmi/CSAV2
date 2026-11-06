"""The object-store interface the knowledge base and HR documents write through."""

from __future__ import annotations

import asyncio
from typing import BinaryIO, Protocol
from uuid import UUID

MAX_NAME_LENGTH = 255


class StorageError(Exception):
    """The store could not complete the operation."""


class StorageNotFound(StorageError, FileNotFoundError):
    """The named object does not exist for this tenant."""


class InvalidObjectName(StorageError, ValueError):
    """The object name is not a single safe path segment."""


class ObjectStore(Protocol):
    """Tenant-scoped blob storage; every method blocks, so call it from a thread."""

    def put(self, tenant: UUID, name: str, data: bytes) -> None:
        """Store the object atomically and durably, replacing any existing one."""
        ...

    def open(self, tenant: UUID, name: str) -> BinaryIO:
        """Open the object for streaming reads; the caller closes the stream."""
        ...

    def read(self, tenant: UUID, name: str, limit: int) -> bytes:
        """Return at most ``limit`` bytes from the start of the object."""
        ...

    def delete(self, tenant: UUID, name: str) -> None:
        """Remove the object; a missing object is not an error."""
        ...

    def list_names(self, tenant: UUID) -> list[str]:
        """Return every name stored for the tenant, including unfinished uploads."""
        ...

    def check(self) -> None:
        """Raise ``StorageError`` when the store is unusable."""
        ...


def validate_name(name: str) -> str:
    """Return ``name`` unchanged when it is a single safe path segment."""
    unsafe = name in {"", ".", ".."} or any(char in name for char in "/\\\x00")
    if unsafe or len(name) > MAX_NAME_LENGTH:
        raise InvalidObjectName(f"invalid object name: {name[:80]!r}")
    return name


async def durable_put(store: ObjectStore, tenant: UUID, name: str, data: bytes) -> None:
    """Run ``store.put`` in a thread and finish it even if the caller is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(store.put, tenant, name, data))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Finish publication before the caller releases its lock and reconciles.
        await task
        raise
