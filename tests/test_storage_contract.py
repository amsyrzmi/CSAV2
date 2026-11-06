"""Behaviour every ObjectStore backend must share."""

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import boto3
import pytest
from csa.storage.base import InvalidObjectName, ObjectStore, StorageNotFound, durable_put
from csa.storage.local import LocalObjectStore
from csa.storage.s3 import S3ObjectStore
from moto import mock_aws

TENANT = uuid4()
INVALID_NAMES = [
    "",
    ".",
    "..",
    "a/b",
    "../x",
    "a\\b",
    "a\x00b",
    "x" * 256,
]


@pytest.fixture(params=["local", "s3"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ObjectStore]:
    if request.param == "local":
        yield LocalObjectStore(tmp_path / "uploads")
    elif request.param == "s3":
        with mock_aws():
            client = boto3.client(
                "s3",
                region_name="us-east-1",
                aws_access_key_id="testing",
                aws_secret_access_key="testing",  # noqa: S106
            )
            client.create_bucket(Bucket="test-bucket")
            yield S3ObjectStore("test-bucket", "kb", client)
    else:
        raise AssertionError(request.param)


def test_put_then_read_returns_same_bytes(store: ObjectStore) -> None:
    store.put(TENANT, "faq.csv", b"hello world")

    assert store.read(TENANT, "faq.csv", 1024) == b"hello world"


def test_put_leaves_only_the_final_name(store: ObjectStore) -> None:
    store.put(TENANT, "faq.csv", b"data")

    assert store.list_names(TENANT) == ["faq.csv"]


def test_put_overwrites_an_existing_object(store: ObjectStore) -> None:
    store.put(TENANT, "faq.csv", b"first")
    store.put(TENANT, "faq.csv", b"second")

    assert store.read(TENANT, "faq.csv", 1024) == b"second"


def test_open_streams_in_blocks(store: ObjectStore) -> None:
    store.put(TENANT, "faq.csv", b"abcdefgh")

    with store.open(TENANT, "faq.csv") as stream:
        assert stream.read(4) == b"abcd"
        assert stream.read(4) == b"efgh"


def test_missing_object_raises_storage_not_found(store: ObjectStore) -> None:
    with pytest.raises(StorageNotFound):
        store.open(TENANT, "missing.csv")
    with pytest.raises(StorageNotFound):
        store.read(TENANT, "missing.csv", 10)


def test_delete_missing_is_not_an_error(store: ObjectStore) -> None:
    store.delete(TENANT, "missing.csv")


def test_delete_then_read_raises_not_found(store: ObjectStore) -> None:
    store.put(TENANT, "faq.csv", b"data")

    store.delete(TENANT, "faq.csv")

    with pytest.raises(StorageNotFound):
        store.read(TENANT, "faq.csv", 10)
    assert store.list_names(TENANT) == []


def test_list_names_is_per_tenant(store: ObjectStore) -> None:
    first = UUID("11111111-1111-1111-1111-111111111111")
    second = UUID("11111111-1111-1111-1111-111111111112")
    store.put(first, "a.csv", b"1")
    store.put(second, "b.csv", b"2")

    assert store.list_names(first) == ["a.csv"]
    assert store.list_names(second) == ["b.csv"]
    assert store.list_names(uuid4()) == []
    with pytest.raises(StorageNotFound):
        store.read(first, "b.csv", 10)


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_invalid_names_are_refused(store: ObjectStore, name: str) -> None:
    with pytest.raises(InvalidObjectName):
        store.put(TENANT, name, b"data")
    with pytest.raises(InvalidObjectName):
        store.open(TENANT, name)
    with pytest.raises(InvalidObjectName):
        store.read(TENANT, name, 10)
    with pytest.raises(InvalidObjectName):
        store.delete(TENANT, name)


def test_a_long_valid_name_is_accepted(store: ObjectStore) -> None:
    # Well under 255: the local backend's ".name.part" temporary file needs six more characters.
    name = "x" * 200
    store.put(TENANT, name, b"data")

    assert store.read(TENANT, name, 10) == b"data"


def test_read_returns_at_most_limit_bytes(store: ObjectStore) -> None:
    store.put(TENANT, "faq.csv", b"0123456789")

    assert store.read(TENANT, "faq.csv", 4) == b"0123"
    assert store.read(TENANT, "faq.csv", 100) == b"0123456789"


async def test_durable_put_completes_when_cancelled(store: ObjectStore) -> None:
    started = threading.Event()
    release = threading.Event()

    class Slow:
        def put(self, tenant: UUID, name: str, data: bytes) -> None:
            started.set()
            assert release.wait(timeout=10)
            store.put(tenant, name, data)

    task = asyncio.create_task(durable_put(Slow(), TENANT, "faq.csv", b"data"))  # type: ignore[arg-type]
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.read(TENANT, "faq.csv", 10) == b"data"
