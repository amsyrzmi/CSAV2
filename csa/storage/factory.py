"""Build the KB and HR object stores from settings and check them at startup."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from csa.core.settings import Settings
from csa.storage.base import ObjectStore
from csa.storage.local import LocalObjectStore

if TYPE_CHECKING:
    from csa.storage.s3 import S3ObjectStore


@dataclass(frozen=True)
class Stores:
    """The stores a process uses; ``None`` means that store is not configured."""

    kb: ObjectStore | None
    hr: ObjectStore | None


def build_stores(settings: Settings) -> Stores:
    """Return the stores for ``settings.STORAGE_BACKEND``."""
    if settings.STORAGE_BACKEND == "s3":
        bucket, region = settings.S3_BUCKET, settings.S3_REGION
        key_id, secret = settings.S3_ACCESS_KEY_ID, settings.S3_SECRET_ACCESS_KEY
        if bucket is None or region is None or key_id is None or secret is None:
            raise ValueError("STORAGE_BACKEND=s3 requires bucket, region and both keys")
        # Imported here so processes on the local backend never load boto3.
        from csa.storage.s3 import S3ObjectStore

        def s3(prefix: str) -> S3ObjectStore:
            return S3ObjectStore.create(
                bucket, region, key_id.get_secret_value(), secret.get_secret_value(), prefix
            )

        return Stores(kb=s3("kb"), hr=s3("hr"))
    return Stores(
        kb=LocalObjectStore(settings.KB_UPLOAD_ROOT) if settings.KB_UPLOAD_ROOT else None,
        hr=LocalObjectStore(settings.HR_UPLOAD_ROOT) if settings.HR_UPLOAD_ROOT else None,
    )


def kb_only(stores: Stores) -> Stores:
    """Keep the KB store only, for processes (the worker) that never touch HR documents."""
    return Stores(kb=stores.kb, hr=None)


async def verify_stores(stores: Stores) -> None:
    """Run every configured store's check; a ``StorageError`` stops the process at startup."""
    for store in (stores.kb, stores.hr):
        if store is not None:
            await asyncio.to_thread(store.check)
