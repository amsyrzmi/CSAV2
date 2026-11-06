"""Local-disk object store: ``<root>/<tenant>/<name>``, published atomically."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from csa.agents.kb.extract import KbFileError, resolve_upload_path
from csa.storage.base import StorageError, StorageNotFound, validate_name


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def check(self) -> None:
        # The root is created on demand, so a missing root is fine; a file in its place is not.
        if self.root.exists() and not self.root.is_dir():
            raise StorageError(f"object storage root is not a directory: {self.root}")

    def put(self, tenant: UUID, name: str, data: bytes) -> None:
        validate_name(name)
        directory = self._tenant_directory(tenant)
        final = directory / name
        temporary = directory / f".{name}.part"
        try:
            with temporary.open("xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final)
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
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
