"""S3-only behaviour: key layout, encryption, pagination, health check, error mapping."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import boto3
import pytest
from botocore.stub import Stubber
from csa.storage.base import StorageError, StorageNotFound
from csa.storage.s3 import S3ObjectStore
from moto import mock_aws

BUCKET = "test-bucket"
FAKE_SECRET = "fake-secret-value"


def make_client() -> Any:
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key=FAKE_SECRET,
    )


@pytest.fixture
def client() -> Iterator[Any]:
    with mock_aws():
        s3 = make_client()
        s3.create_bucket(Bucket=BUCKET)
        yield s3


def test_key_layout_is_prefix_tenant_name(client: Any) -> None:
    tenant = uuid4()
    S3ObjectStore(BUCKET, "kb", client).put(tenant, "faq.csv", b"data")

    keys = [item["Key"] for item in client.list_objects_v2(Bucket=BUCKET)["Contents"]]

    assert keys == [f"kb/{tenant}/faq.csv"]


def test_put_requests_aes256_encryption(client: Any) -> None:
    tenant = uuid4()
    S3ObjectStore(BUCKET, "kb", client).put(tenant, "faq.csv", b"data")

    head = client.head_object(Bucket=BUCKET, Key=f"kb/{tenant}/faq.csv")

    assert head["ServerSideEncryption"] == "AES256"


def test_list_names_paginates(client: Any) -> None:
    tenant = uuid4()
    store = S3ObjectStore(BUCKET, "kb", client, list_page_size=2)
    for index in range(5):
        store.put(tenant, f"file-{index}.csv", b"x")

    assert sorted(store.list_names(tenant)) == [f"file-{index}.csv" for index in range(5)]


def test_read_of_an_empty_object_returns_empty_bytes(client: Any) -> None:
    tenant = uuid4()
    store = S3ObjectStore(BUCKET, "kb", client)
    store.put(tenant, "empty.csv", b"")

    assert store.read(tenant, "empty.csv", 1024) == b""


def test_read_with_zero_limit_does_not_call_s3() -> None:
    client = make_client()
    with Stubber(client) as stubber:
        assert S3ObjectStore(BUCKET, "kb", client).read(uuid4(), "faq.csv", 0) == b""
        stubber.assert_no_pending_responses()


def test_check_passes_for_a_usable_bucket(client: Any) -> None:
    S3ObjectStore(BUCKET, "kb", client).check()


def test_check_fails_for_a_missing_bucket(client: Any) -> None:
    with pytest.raises(StorageError) as raised:
        S3ObjectStore("no-such-bucket", "kb", client).check()

    assert not isinstance(raised.value, StorageNotFound)
    assert FAKE_SECRET not in str(raised.value)


def test_check_lists_under_its_own_prefix_only() -> None:
    client = make_client()
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_objects_v2",
            {"KeyCount": 0, "IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": "kb/", "MaxKeys": 1},
        )

        S3ObjectStore(BUCKET, "kb", client).check()

        stubber.assert_no_pending_responses()
    # head_bucket would have raised: the stubber only holds the one list_objects_v2 response.


def test_access_denied_is_a_storage_error_and_not_not_found() -> None:
    client = make_client()
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_object",
            service_error_code="AccessDenied",
            service_message="Access Denied",
            http_status_code=403,
        )

        with pytest.raises(StorageError) as raised:
            S3ObjectStore(BUCKET, "kb", client).read(uuid4(), "faq.csv", 10)

    assert not isinstance(raised.value, StorageNotFound)
    assert FAKE_SECRET not in str(raised.value)
