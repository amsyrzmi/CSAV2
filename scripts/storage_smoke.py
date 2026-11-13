"""Check the configured object store end to end, for the owner to run with the real app key.

Run it inside the gateway container so it uses the process's own settings, for example
``docker exec csa-prod-gateway-1 python -m scripts.storage_smoke``.

It writes only under a random tenant id, in the knowledge-base, HR and training stores, and
removes what it wrote. The training store also gets a piece-by-piece (multipart) upload of three
pieces, read back whole, a second upload aborted after one piece, and, on S3, a GET of the first
16 bytes through a presigned playback link that must answer 206 (as a browser video element
does). It needs the application key: run it in the gateway, not the training worker.
Exit codes: 0 every check passed, 1 a check failed, 2 the setup is unusable. It prints one
``PASS`` or ``FAIL`` line per check and never prints keys, links, object names or contents.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable
from typing import Protocol
from uuid import UUID, uuid4

import httpx
from csa.core.settings import Settings, get_settings
from csa.storage.base import ObjectStore, StorageError, StorageNotFound
from csa.storage.factory import build_stores
from csa.storage.multipart import MultipartStore, Part
from pydantic import ValidationError

PAYLOAD = b"csa-storage-smoke-payload-0123456789"
STREAM_BYTES = 8
PIECE_BYTES = 5 * 1024 * 1024
"""Every piece but the last: S3's smallest part."""
LAST_PIECE_BYTES = 1024
RANGE_BYTES = 16
LINK_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 15.0


def _read_missing(store: ObjectStore, tenant: UUID, name: str) -> str:
    """Return the error class a read of ``name`` raises; fail if the read succeeds."""
    try:
        store.read(tenant, name, 16)
    except StorageNotFound:
        return "StorageNotFound"
    except StorageError:
        # A prefix-conditioned IAM policy may answer 403 for a missing key.
        return "StorageError"
    raise AssertionError("read of a missing name returned data")


class _TrainingStore(ObjectStore, MultipartStore, Protocol):
    """The training store: whole files and piece-by-piece uploads."""


def _reason(exc: Exception) -> str:
    """A failure's printable reason.

    StorageError text is code-only by design and AssertionError text is authored in this script;
    any other error is named by class alone.
    """
    return str(exc) if isinstance(exc, StorageError | AssertionError) else type(exc).__name__


class _Checks:
    """Prints one PASS or FAIL line per check and remembers whether all passed."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.passed = True

    def __call__(self, title: str, action: Callable[[], str | None]) -> None:
        try:
            detail = action()
        except Exception as exc:
            print(f"FAIL {self.label}: {title} ({_reason(exc)})", flush=True)
            self.passed = False
        else:
            print(f"PASS {self.label}: {title}" + (f" ({detail})" if detail else ""), flush=True)


def _check_stores(label: str, store: ObjectStore, tenant: UUID) -> bool:
    """Run every check against one store; return True when all passed."""
    name = f"smoke-{uuid4().hex}.bin"
    never_written = f"smoke-{uuid4().hex}.missing"
    check = _Checks(label)

    def put_then_read() -> None:
        store.put(tenant, name, PAYLOAD)
        if store.read(tenant, name, len(PAYLOAD) + 10) != PAYLOAD:
            raise AssertionError("read returned different bytes")

    def list_contains_name() -> None:
        if name not in store.list_names(tenant):
            raise AssertionError("name missing from the listing")

    def open_streams_and_closes() -> None:
        stream = store.open(tenant, name)
        try:
            if stream.read(STREAM_BYTES) != PAYLOAD[:STREAM_BYTES]:
                raise AssertionError("stream returned different bytes")
        finally:
            stream.close()

    def delete_then_read_missing() -> str:
        store.delete(tenant, name)
        return _read_missing(store, tenant, name)

    def read_missing_raises() -> str:
        return _read_missing(store, tenant, never_written)

    def cleanup() -> None:
        store.delete(tenant, name)
        if store.list_names(tenant):
            raise AssertionError("objects remain under the smoke tenant")

    check("startup check", lambda: store.check())
    check("put then read", put_then_read)
    check("list contains name", list_contains_name)
    check("open streams and closes", open_streams_and_closes)
    check("delete then read missing", delete_then_read_missing)
    check("delete missing is not an error", lambda: store.delete(tenant, name))
    check("read missing raises", read_missing_raises)
    check("cleanup leaves nothing behind", cleanup)
    return check.passed


def _check_multipart(label: str, store: _TrainingStore, tenant: UUID, http: httpx.Client) -> bool:
    """Upload three pieces, read them back, abort a second upload and, on S3, GET a range
    through a presigned link; remove everything. Return True when all passed."""
    name = f"smoke-{uuid4().hex}.bin"
    aborted = f"smoke-{uuid4().hex}.bin"
    # Random bytes: a store that returns zeros or another object cannot pass the read back.
    data = os.urandom(2 * PIECE_BYTES + LAST_PIECE_BYTES)
    check = _Checks(label)
    open_uploads: dict[str, str | None] = {}

    def write(target: str, upload_id: str | None, number: int) -> Part:
        offset = (number - 1) * PIECE_BYTES
        piece = data[offset : offset + PIECE_BYTES]
        digest = hashlib.sha256(piece).digest()
        etag = store.write_part(tenant, target, upload_id, number, offset, piece, digest)
        return Part(number, etag, digest)

    def round_trip() -> None:
        upload_id = store.start_upload(tenant, name, "video/mp4")
        open_uploads[name] = upload_id
        parts = [write(name, upload_id, number) for number in (1, 2, 3)]
        store.complete_upload(tenant, name, upload_id, parts, PIECE_BYTES, len(data))
        del open_uploads[name]

    def read_back() -> None:
        digest = hashlib.sha256()
        size = 0
        stream = store.open(tenant, name)
        try:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        finally:
            stream.close()
        if size != len(data) or digest.digest() != hashlib.sha256(data).digest():
            raise AssertionError("read back returned different bytes")

    def abort_an_upload() -> None:
        # What a removed or abandoned video does; on S3 it needs s3:AbortMultipartUpload.
        upload_id = store.start_upload(tenant, aborted, "video/mp4")
        open_uploads[aborted] = upload_id
        write(aborted, upload_id, 1)
        store.abort_upload(tenant, aborted, upload_id)
        del open_uploads[aborted]

    def presigned_range() -> None:
        link = store.presigned_playback(tenant, name, LINK_SECONDS, "video/mp4", "smoke.mp4")
        if link is None:
            raise AssertionError("no presigned link")
        # The link is a credential while it lasts: never printed, logged or put in an error.
        response = http.get(link, headers={"Range": f"bytes=0-{RANGE_BYTES - 1}"})
        if response.status_code != 206:
            raise AssertionError(f"answered {response.status_code}, expected 206")
        if response.content != data[:RANGE_BYTES]:
            raise AssertionError("range returned different bytes")

    def cleanup() -> None:
        # Every removal is tried even when an earlier one fails, so a denied abort never leaves
        # the 10 MiB object behind; what could not be removed is named (by kind, never by name).
        left: list[str] = []
        try:
            for target, upload_id in list(open_uploads.items()):
                try:
                    store.abort_upload(tenant, target, upload_id)
                except Exception as exc:
                    left.append(f"an open upload ({_reason(exc)})")
                else:
                    del open_uploads[target]
        finally:
            try:
                store.delete(tenant, name)
            except Exception as exc:
                left.append(f"the object ({_reason(exc)})")
        try:
            if store.list_names(tenant):
                left.append("objects under the smoke tenant")
        except Exception as exc:
            left.append(f"an unchecked listing ({_reason(exc)})")
        if left:
            raise AssertionError(f"left behind: {'; '.join(left)}")

    check("multipart: start, three pieces, complete", round_trip)
    check("multipart: read back", read_back)
    check("multipart: abort an upload", abort_an_upload)
    # Local files have no link (the gateway serves them itself), so there is nothing to try.
    try:
        linked = store.presigned_playback(tenant, name, 1, "video/mp4", "smoke.mp4") is not None
    except StorageError:
        linked = True  # the check below reports the failure
    if linked:
        check("presigned range answers 206", presigned_range)
    check("multipart: cleanup leaves nothing behind", cleanup)
    return check.passed


def run(settings: Settings, *, http: httpx.Client | None = None) -> int:
    """Run the checks for every configured store and return the process exit code."""
    try:
        stores = build_stores(settings)
    except ValueError as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2
    candidates = (("kb", stores.kb), ("hr", stores.hr), ("training", stores.training))
    configured = [(label, store) for label, store in candidates if store is not None]
    if not configured:
        print(
            "setup error: no object store is configured; set STORAGE_BACKEND=s3 with the S3 "
            "settings, or KB_UPLOAD_ROOT / HR_UPLOAD_ROOT / TRAINING_UPLOAD_ROOT for the local "
            "backend",
            file=sys.stderr,
        )
        return 2
    tenant = uuid4()
    results = [_check_stores(label, store, tenant) for label, store in configured]
    if isinstance(stores.training, MultipartStore):
        client = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        try:
            results.append(_check_multipart("training", stores.training, tenant, client))
        finally:
            if http is None:
                client.close()
    return 0 if all(results) else 1


def main() -> int:
    """Load the process settings and run the checks; settings errors exit 2."""
    try:
        settings = get_settings()
    except ValidationError as exc:
        # Name the fields only: the error's input values may be credentials.
        fields = ", ".join(sorted({".".join(str(part) for part in e["loc"]) for e in exc.errors()}))
        print(f"setup error: settings are invalid ({fields or 'model'})", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"setup error: settings are invalid ({type(exc).__name__})", file=sys.stderr)
        return 2
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
