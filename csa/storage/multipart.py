"""Piece-by-piece uploads beside ``ObjectStore``: training videos arrive in fixed-size pieces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Protocol, runtime_checkable
from uuid import UUID

from csa.storage.base import StorageError


@dataclass(frozen=True)
class Part:
    """A piece the store accepted: its 1-based number, the store's ETag and its SHA-256."""

    number: int
    etag: str
    sha256: bytes


@dataclass(frozen=True)
class OpenUpload:
    """An unfinished upload the store still holds."""

    name: str
    upload_id: str
    started_at: datetime


class PartMismatch(StorageError):
    """Stored bytes of these pieces do not match their recorded hashes; send them again."""

    def __init__(self, numbers: Sequence[int]) -> None:
        self.numbers = sorted(set(numbers))
        # Piece numbers only: never a key, URL or file name.
        super().__init__(f"object storage: pieces do not match: {self.numbers}")


@runtime_checkable
class MultipartStore(Protocol):
    """Tenant-scoped piece uploads and playback; every method blocks, so call it from a thread."""

    def start_upload(self, tenant: UUID, name: str, media_type: str) -> str | None:
        """Open an upload for ``name``; return the store's upload id (``None`` on local disk)."""
        ...

    def write_part(
        self,
        tenant: UUID,
        name: str,
        upload_id: str | None,
        number: int,
        offset: int,
        data: bytes,
        sha256: bytes,
    ) -> str:
        """Store piece ``number`` (replacing an earlier copy) and return its ETag.

        Raises ``StorageNotFound`` when the upload no longer exists.
        """
        ...

    def complete_upload(
        self,
        tenant: UUID,
        name: str,
        upload_id: str | None,
        parts: Sequence[Part],
        piece_size: int,
        total_bytes: int,
    ) -> None:
        """Check every piece against its hash and publish ``name``.

        Raises ``PartMismatch`` naming the pieces to send again. Completing an upload that
        was already published is done, so a lost answer can be retried.
        """
        ...

    def abort_upload(self, tenant: UUID, name: str, upload_id: str | None) -> None:
        """Discard the upload and its pieces; a missing upload is not an error."""
        ...

    def list_uploads(self, tenant: UUID) -> list[OpenUpload]:
        """Return the tenant's unfinished uploads the store tracks by id."""
        ...

    def media_source(self, tenant: UUID, name: str, ttl_seconds: int) -> str:
        """Return a path or short-lived URL a media tool can read the published object from."""
        ...

    def presigned_playback(
        self, tenant: UUID, name: str, ttl_seconds: int, media_type: str, file_name: str
    ) -> str | None:
        """Return a short-lived inline playback URL, or ``None`` when the gateway serves it."""
        ...

    def object_size(self, tenant: UUID, name: str) -> int:
        """Return the published object's size in bytes, as the store reports it.

        Raises ``StorageNotFound`` when it does not exist; never echoes a key or URL.
        """
        ...

    def open_range(self, tenant: UUID, name: str) -> BinaryIO:
        """Open the published object as a seekable stream; the caller closes it."""
        ...


def piece_range(number: int, piece_size: int, total_bytes: int) -> tuple[int, int]:
    """Return ``(offset, length)`` of piece ``number`` in an object of ``total_bytes``."""
    if piece_size < 1 or total_bytes < 1:
        raise ValueError("piece size and total bytes must be positive")
    offset = (number - 1) * piece_size
    if number < 1 or offset >= total_bytes:
        raise ValueError("piece number out of range")
    return offset, min(piece_size, total_bytes - offset)


def missing_numbers(parts: Sequence[Part], piece_size: int, total_bytes: int) -> list[int]:
    """Return the piece numbers ``parts`` lacks to cover ``total_bytes``."""
    numbers = [part.number for part in parts]
    if len(numbers) != len(set(numbers)):
        raise ValueError("a piece number appears twice")
    for number in numbers:
        piece_range(number, piece_size, total_bytes)
    count = -(-total_bytes // piece_size)
    return sorted(set(range(1, count + 1)) - set(numbers))
