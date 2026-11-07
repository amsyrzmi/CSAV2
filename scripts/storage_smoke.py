"""Check the configured object store end to end, for the owner to run with the real app key.

Run it inside the gateway container so it uses the process's own settings, for example
``docker exec csa-prod-gateway-1 python -m scripts.storage_smoke``.

It writes only under a random tenant id, in the knowledge-base, HR and training stores, and
removes what it wrote. Exit codes: 0 every check passed, 1 a check failed, 2 the setup is
unusable. It prints one ``PASS`` or ``FAIL`` line per check and never prints keys or object
contents.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from uuid import UUID, uuid4

from csa.core.settings import Settings, get_settings
from csa.storage.base import ObjectStore, StorageError, StorageNotFound
from csa.storage.factory import build_stores
from pydantic import ValidationError

PAYLOAD = b"csa-storage-smoke-payload-0123456789"
STREAM_BYTES = 8


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


def _check_stores(label: str, store: ObjectStore, tenant: UUID) -> bool:
    """Run every check against one store; return True when all passed."""
    name = f"smoke-{uuid4().hex}.bin"
    never_written = f"smoke-{uuid4().hex}.missing"
    passed = True

    def check(title: str, action: Callable[[], str | None]) -> None:
        nonlocal passed
        try:
            detail = action()
        except Exception as exc:
            # StorageError text is code-only by design and AssertionError text is authored in
            # this script; any other error is named by class alone.
            authored = isinstance(exc, StorageError | AssertionError)
            reason = str(exc) if authored else type(exc).__name__
            print(f"FAIL {label}: {title} ({reason})", flush=True)
            passed = False
        else:
            print(f"PASS {label}: {title}" + (f" ({detail})" if detail else ""), flush=True)

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
    return passed


def run(settings: Settings) -> int:
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
