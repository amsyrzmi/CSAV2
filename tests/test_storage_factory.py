"""Store factory: backend choice, per-store prefixes and the startup check."""

from __future__ import annotations

import base64
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from csa.core.settings import Settings
from csa.storage.base import StorageError
from csa.storage.factory import Stores, build_stores, verify_stores
from csa.storage.local import LocalObjectStore
from csa.storage.s3 import S3ObjectStore
from moto import mock_aws

# Settings looks for /run/secrets even when told not to; the folder is absent off Docker.
pytestmark = pytest.mark.filterwarnings("ignore:directory .* does not exist:UserWarning")

BUCKET = "factory-bucket"
REGION = "us-east-1"
FAKE_SECRET = "fake-secret-value"


def make_settings(**overrides: str) -> Settings:
    env = {
        "ENV": "ci",
        "DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "LG_DATABASE_URL": "postgresql://csa_app:pw@localhost:5433/csa",
        "GROQ_API_KEY": "synthetic",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": "base64:" + base64.b64encode(b"\x01" * 32).decode("ascii"),
    }
    env.update(overrides)
    return Settings(_env_file=None, _secrets_dir=None, **env)  # type: ignore[call-arg]


def s3_settings() -> Settings:
    return make_settings(
        STORAGE_BACKEND="s3",
        S3_BUCKET=BUCKET,
        S3_REGION=REGION,
        S3_ACCESS_KEY_ID="testing",
        S3_SECRET_ACCESS_KEY=FAKE_SECRET,
    )


def test_build_stores_local_returns_none_for_unset_roots() -> None:
    assert build_stores(make_settings()) == Stores(kb=None, hr=None)


def test_build_stores_local_uses_the_configured_roots(tmp_path: Path) -> None:
    kb_root, hr_root = tmp_path / "kb", tmp_path / "hr"
    stores = build_stores(make_settings(KB_UPLOAD_ROOT=str(kb_root), HR_UPLOAD_ROOT=str(hr_root)))

    assert isinstance(stores.kb, LocalObjectStore) and stores.kb.root == kb_root
    assert isinstance(stores.hr, LocalObjectStore) and stores.hr.root == hr_root
    only_hr = build_stores(make_settings(HR_UPLOAD_ROOT=str(hr_root)))
    assert only_hr.kb is None and only_hr.hr is not None


def test_build_stores_s3_gives_kb_and_hr_separate_prefixes() -> None:
    tenant = uuid4()
    with mock_aws():
        client = boto3.client(
            "s3", region_name=REGION, aws_access_key_id="testing", aws_secret_access_key=FAKE_SECRET
        )
        client.create_bucket(Bucket=BUCKET)
        stores = build_stores(s3_settings())
        assert isinstance(stores.kb, S3ObjectStore) and isinstance(stores.hr, S3ObjectStore)

        stores.kb.put(tenant, "faq.csv", b"kb")
        stores.hr.put(tenant, "contract.pdf", b"hr")

        keys = sorted(item["Key"] for item in client.list_objects_v2(Bucket=BUCKET)["Contents"])
    assert keys == [f"hr/{tenant}/contract.pdf", f"kb/{tenant}/faq.csv"]


async def test_verify_stores_passes_for_a_reachable_bucket() -> None:
    with mock_aws():
        boto3.client(
            "s3", region_name=REGION, aws_access_key_id="testing", aws_secret_access_key=FAKE_SECRET
        ).create_bucket(Bucket=BUCKET)
        await verify_stores(build_stores(s3_settings()))


async def test_verify_stores_skips_unset_local_stores() -> None:
    await verify_stores(Stores(kb=None, hr=None))


async def test_verify_stores_raises_for_a_missing_bucket() -> None:
    with mock_aws():
        with pytest.raises(StorageError, match="check failed: NoSuchBucket"):
            await verify_stores(build_stores(s3_settings()))
