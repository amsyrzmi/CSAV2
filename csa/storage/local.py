"""Local-disk object store: ``<root>/<tenant>/<name>``, published atomically."""

from __future__ import annotations

import errno
import hashlib
import os
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from csa.agents.kb.extract import KbFileError, resolve_upload_path
from csa.storage.base import InvalidObjectName, StorageError, StorageNotFound, validate_name
from csa.storage.multipart import OpenUpload, Part, PartMismatch, missing_numbers, piece_range

MAX_FILE_NAME_BYTES = 255
HASH_READ_BYTES = 64 * 1024


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def check(self) -> None:
        # The root is created on demand, so a missing root is fine; a file in its place is not.
        if self.root.exists() and not self.root.is_dir():
            raise StorageError(f"object storage root is not a directory: {self.root}")

    def put(self, tenant: UUID, name: str, data: bytes) -> None:
        temporary_name = _temporary_name(name)
        directory = self._tenant_directory(tenant)
        final = directory / name
        temporary = directory / temporary_name
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final)
            _fsync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def open(self, tenant: UUID, name: str) -> BinaryIO:
        path = self._resolve(tenant, name)
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise StorageNotFound(str(exc)) from exc

    def read(self, tenant: UUID, name: str, limit: int) -> bytes:
        if limit < 0:
            raise ValueError("limit must not be negative")
        with self.open(tenant, name) as stream:
            return stream.read(limit)

    def delete(self, tenant: UUID, name: str) -> None:
        validate_name(name)
        directory = self._existing_directory(tenant)
        if directory is not None:
            (directory / name).unlink(missing_ok=True)

    def list_names(self, tenant: UUID) -> list[str]:
        directory = self._existing_directory(tenant)
        if directory is None:
            return []
        return sorted(path.name for path in directory.iterdir())

    # Multipart: pieces are positional writes into ``.<name>.part``, renamed on completion.
    # Messages carry the operation only, never a path or name.

    def start_upload(self, tenant: UUID, name: str, media_type: str) -> str | None:
        temporary_name = _temporary_name(name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            temporary = self._tenant_directory(tenant) / temporary_name
            try:
                descriptor = os.open(temporary, flags, 0o600)
            except FileExistsError:
                raise StorageError("object storage start failed: upload exists") from None
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _fsync_directory(temporary.parent)
        except StorageError:
            raise
        except OSError as exc:
            raise _os_failure("start", exc) from None
        return None

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
        if number < 1 or offset < 0:
            raise ValueError("piece number must be positive and offset not negative")
        if hashlib.sha256(data).digest() != sha256:
            raise PartMismatch([number])
        temporary = self._upload_path(tenant, name, "write")
        # No O_CREAT: a write that races a delete must not bring the upload back.
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            raise StorageNotFound("object storage write: upload not found") from None
        except OSError as exc:
            raise _os_failure("write", exc) from None
        try:
            view = memoryview(data)
            written = 0
            while written < len(view):
                written += os.pwrite(descriptor, view[written:], offset + written)
            os.fsync(descriptor)
        except OSError as exc:
            raise _os_failure("write", exc) from None
        finally:
            os.close(descriptor)
        return sha256.hex()

    def complete_upload(
        self,
        tenant: UUID,
        name: str,
        upload_id: str | None,
        parts: Sequence[Part],
        piece_size: int,
        total_bytes: int,
    ) -> None:
        missing = missing_numbers(parts, piece_size, total_bytes)
        temporary = self._upload_path(tenant, name, "complete")
        final = temporary.parent / name
        try:
            descriptor = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            if final.is_file() and not final.is_symlink():
                return  # published by an earlier call whose answer was lost
            raise StorageNotFound("object storage complete: upload not found") from None
        except OSError as exc:
            raise _os_failure("complete", exc) from None
        try:
            if missing:
                raise PartMismatch(missing)
            changed = [
                part.number
                for part in sorted(parts, key=lambda part: part.number)
                if _range_sha256(descriptor, *piece_range(part.number, piece_size, total_bytes))
                != part.sha256
            ]
            if changed:
                raise PartMismatch(changed)
            if os.fstat(descriptor).st_size != total_bytes:
                raise StorageError("object storage complete failed: size mismatch")
        except StorageError:
            raise
        except OSError as exc:
            raise _os_failure("complete", exc) from None
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, final)
            _fsync_directory(final.parent)
        except FileNotFoundError:
            if final.is_file() and not final.is_symlink():
                return  # a concurrent call published it first
            raise StorageNotFound("object storage complete: upload not found") from None
        except OSError as exc:
            raise _os_failure("complete", exc) from None

    def abort_upload(self, tenant: UUID, name: str, upload_id: str | None) -> None:
        temporary_name = _temporary_name(name)
        directory = self._existing_directory(tenant)
        if directory is not None:
            (directory / temporary_name).unlink(missing_ok=True)

    def list_uploads(self, tenant: UUID) -> list[OpenUpload]:
        # Local uploads have no id; the sweep finds their ``.part`` files by name.
        return []

    def media_source(self, tenant: UUID, name: str, ttl_seconds: int) -> str:
        path = self._published_path(tenant, name, "source")
        if not path.is_file():
            raise StorageNotFound("object storage source: object not found")
        return str(path)

    def presigned_playback(
        self, tenant: UUID, name: str, ttl_seconds: int, media_type: str, file_name: str
    ) -> str | None:
        # The gateway serves local files itself, with Range support.
        return None

    def open_range(self, tenant: UUID, name: str) -> BinaryIO:
        path = self._published_path(tenant, name, "open")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except FileNotFoundError:
            raise StorageNotFound("object storage open: object not found") from None
        return os.fdopen(descriptor, "rb")

    def _upload_path(self, tenant: UUID, name: str, operation: str) -> Path:
        temporary_name = _temporary_name(name)
        directory = self._existing_directory(tenant)
        if directory is None:
            raise StorageNotFound(f"object storage {operation}: upload not found")
        return directory / temporary_name

    def _published_path(self, tenant: UUID, name: str, operation: str) -> Path:
        validate_name(name)
        directory = self._existing_directory(tenant)
        if directory is None:
            raise StorageNotFound(f"object storage {operation}: object not found")
        return directory / name

    def _resolve(self, tenant: UUID, name: str) -> Path:
        # Rows written before the store existed may hold an absolute path inside the tenant
        # directory; resolve_upload_path keeps those readable and refuses anything outside.
        if not os.path.isabs(name):
            validate_name(name)
        try:
            return resolve_upload_path(self.root, tenant, name)
        except KbFileError as exc:
            raise StorageNotFound(str(exc)) from exc

    def _tenant_directory(self, tenant: UUID) -> Path:
        directory = self.root.resolve() / str(tenant)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._refuse_symlink(directory)
        return directory

    def _existing_directory(self, tenant: UUID) -> Path | None:
        directory = self.root.resolve() / str(tenant)
        if not directory.exists() and not directory.is_symlink():
            return None
        self._refuse_symlink(directory)
        return directory

    @staticmethod
    def _refuse_symlink(directory: Path) -> None:
        if directory.is_symlink() or directory.resolve() != directory:
            raise StorageError("tenant storage directory must not be a symlink")


def _temporary_name(name: str) -> str:
    validate_name(name)
    temporary_name = f".{name}.part"
    if len(temporary_name.encode()) > MAX_FILE_NAME_BYTES:
        raise InvalidObjectName(f"object name too long for local storage: {name[:80]!r}")
    return temporary_name


def _os_failure(operation: str, exc: OSError) -> StorageError:
    """The operation and the error's code only: an ``OSError``'s text carries the local path."""
    code = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
    return StorageError(f"object storage {operation} failed: {code}")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _range_sha256(descriptor: int, offset: int, length: int) -> bytes:
    digest = hashlib.sha256()
    position, end = offset, offset + length
    while position < end:
        block = os.pread(descriptor, min(HASH_READ_BYTES, end - position), position)
        if not block:
            break  # the file is shorter than the piece: the digest cannot match
        digest.update(block)
        position += len(block)
    return digest.digest()
