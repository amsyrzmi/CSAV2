"""Multipart pieces, checked completion and presigned playback on both object store backends."""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import boto3
import pytest
from botocore.stub import Stubber
from csa.storage.base import StorageError, StorageNotFound
from csa.storage.local import LocalObjectStore
from csa.storage.multipart import MultipartStore, Part, PartMismatch
from csa.storage.s3 import S3ObjectStore
from csa.training import limits
from moto import mock_aws

BUCKET = "test-bucket"
REGION = "ap-southeast-1"
FAKE_SECRET = "fake-secret-value"
MIB = 1024 * 1024
# moto refuses parts under 5 MiB except the last, as S3 does.
S3_PIECE = 5 * MIB


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


def video_name() -> str:
    return f"{uuid4()}.mp4"


def source_bytes(size: int) -> bytes:
    # Synthetic, non-repeating at piece boundaries, so a misplaced piece changes the result.
    block = hashlib.sha256(b"synthetic").digest() * 2048
    return (block * (size // len(block) + 1))[:size]


def pieces(data: bytes, piece_size: int) -> list[tuple[int, int, bytes]]:
    """Return ``(number, offset, bytes)`` for each piece of ``data``."""
    return [
        (index + 1, offset, data[offset : offset + piece_size])
        for index, offset in enumerate(range(0, len(data), piece_size))
    ]


def b64(digest: bytes) -> str:
    return base64.b64encode(digest).decode()


def write_all(
    store: Any,
    tenant: UUID,
    name: str,
    upload_id: str | None,
    data: bytes,
    piece_size: int,
    order: list[int],
) -> list[Part]:
    by_number = {number: (offset, chunk) for number, offset, chunk in pieces(data, piece_size)}
    parts: dict[int, Part] = {}
    for number in order:
        offset, chunk = by_number[number]
        digest = hashlib.sha256(chunk).digest()
        etag = store.write_part(tenant, name, upload_id, number, offset, chunk, digest)
        parts[number] = Part(number, etag, digest)
    return [parts[number] for number in sorted(parts)]


# Local backend


def test_local_pieces_in_any_order_assemble_byte_identical(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    tenant, name = uuid4(), video_name()
    data = source_bytes(2 * 1024 + 500)
    directory = tmp_path / "uploads" / str(tenant)

    assert isinstance(store, MultipartStore)
    assert store.start_upload(tenant, name, "video/mp4") is None
    part_file = directory / f".{name}.part"
    assert oct(part_file.stat().st_mode & 0o777) == oct(0o600)
    parts = write_all(store, tenant, name, None, data, 1024, [3, 1, 2])
    assert [part.etag for part in parts] == [part.sha256.hex() for part in parts]
    store.complete_upload(tenant, name, None, parts, 1024, len(data))

    assert (directory / name).read_bytes() == data
    assert not part_file.exists()
    assert store.list_uploads(tenant) == []
    assert store.media_source(tenant, name, 900) == str((directory / name).resolve())
    assert store.presigned_playback(tenant, name, 900, "video/mp4", "a.mp4") is None
    with store.open_range(tenant, name) as stream:
        stream.seek(1024)
        assert stream.read(10) == data[1024:1034]


def test_local_rewrite_of_a_piece_overwrites_the_same_range(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    tenant, name = uuid4(), video_name()
    data = source_bytes(2 * 1024 + 500)
    part_file = tmp_path / "uploads" / str(tenant) / f".{name}.part"

    store.start_upload(tenant, name, "video/mp4")
    parts = write_all(store, tenant, name, None, data, 1024, [1, 2, 3])
    size_before = part_file.stat().st_size
    write_all(store, tenant, name, None, data, 1024, [2])

    assert part_file.stat().st_size == size_before == len(data)
    store.complete_upload(tenant, name, None, parts, 1024, len(data))
    assert (tmp_path / "uploads" / str(tenant) / name).read_bytes() == data


def test_local_complete_reports_a_changed_piece(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    tenant, name = uuid4(), video_name()
    data = source_bytes(2 * 1024 + 500)
    directory = tmp_path / "uploads" / str(tenant)
    part_file = directory / f".{name}.part"

    store.start_upload(tenant, name, "video/mp4")
    parts = write_all(store, tenant, name, None, data, 1024, [1, 2, 3])
    # Change piece 2 behind the store's back: same size, different bytes.
    with part_file.open("r+b") as stream:
        stream.seek(1024 + 7)
        stream.write(b"\x00\xff")

    with pytest.raises(PartMismatch) as raised:
        store.complete_upload(tenant, name, None, parts, 1024, len(data))

    assert raised.value.numbers == [2]
    assert part_file.exists()
    assert not (directory / name).exists()
    assert name not in str(raised.value) and str(tenant) not in str(raised.value)


def test_local_write_after_abort_raises_not_found_and_creates_nothing(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    tenant, name = uuid4(), video_name()
    data = source_bytes(2 * 1024)
    directory = tmp_path / "uploads" / str(tenant)

    store.start_upload(tenant, name, "video/mp4")
    write_all(store, tenant, name, None, data, 1024, [1])
    store.abort_upload(tenant, name, None)
    store.abort_upload(tenant, name, None)  # missing is not an error

    with pytest.raises(StorageNotFound) as raised:
        write_all(store, tenant, name, None, data, 1024, [2])

    assert list(directory.iterdir()) == []
    assert name not in str(raised.value) and str(tenant) not in str(raised.value)
    # A company that never started an upload: not even its directory is created.
    other = uuid4()
    with pytest.raises(StorageNotFound):
        write_all(store, other, name, None, data, 1024, [1])
    assert not (tmp_path / "uploads" / str(other)).exists()


def test_local_complete_twice_is_done(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "uploads")
    tenant, name = uuid4(), video_name()
    data = source_bytes(2 * 1024 + 500)

    store.start_upload(tenant, name, "video/mp4")
    parts = write_all(store, tenant, name, None, data, 1024, [1, 2, 3])
    store.complete_upload(tenant, name, None, parts, 1024, len(data))
    store.complete_upload(tenant, name, None, parts, 1024, len(data))

    assert (tmp_path / "uploads" / str(tenant) / name).read_bytes() == data
    # Never started and never published: not done.
    with pytest.raises(StorageNotFound):
        store.complete_upload(tenant, video_name(), None, parts, 1024, len(data))


# S3 backend


def test_s3_multipart_round_trip_under_the_training_prefix(client: Any) -> None:
    store = S3ObjectStore(BUCKET, "training", client)
    tenant, name = uuid4(), video_name()
    data = source_bytes(2 * S3_PIECE + 1000)

    assert isinstance(store, MultipartStore)
    upload_id = store.start_upload(tenant, name, "video/mp4")
    assert upload_id is not None
    assert [upload.name for upload in store.list_uploads(tenant)] == [name]
    parts = write_all(store, tenant, name, upload_id, data, S3_PIECE, [3, 1, 2])
    store.complete_upload(tenant, name, upload_id, parts, S3_PIECE, len(data))

    key = f"training/{tenant}/{name}"
    assert [item["Key"] for item in client.list_objects_v2(Bucket=BUCKET)["Contents"]] == [key]
    response = client.get_object(Bucket=BUCKET, Key=key)
    assert response["Body"].read() == data
    assert response["ServerSideEncryption"] == "AES256"
    assert store.list_uploads(tenant) == []


def test_s3_calls_carry_encryption_and_checksums() -> None:
    client = make_client()
    store = S3ObjectStore(BUCKET, "training", client)
    tenant, name = uuid4(), video_name()
    key = f"training/{tenant}/{name}"
    data = source_bytes(10)
    digest = hashlib.sha256(data).digest()
    with Stubber(client) as stubber:
        stubber.add_response(
            "create_multipart_upload",
            {"Bucket": BUCKET, "Key": key, "UploadId": "upload-1"},
            {
                "Bucket": BUCKET,
                "Key": key,
                "ContentType": "video/mp4",
                "ServerSideEncryption": "AES256",
                "ChecksumAlgorithm": "SHA256",
            },
        )
        stubber.add_response(
            "upload_part",
            {"ETag": '"etag-1"', "ChecksumSHA256": b64(digest)},
            {
                "Bucket": BUCKET,
                "Key": key,
                "UploadId": "upload-1",
                "PartNumber": 1,
                "Body": data,
                "ChecksumSHA256": b64(digest),
            },
        )
        stubber.add_response(
            "complete_multipart_upload",
            {"Bucket": BUCKET, "Key": key},
            {
                "Bucket": BUCKET,
                "Key": key,
                "UploadId": "upload-1",
                "MultipartUpload": {
                    "Parts": [{"PartNumber": 1, "ETag": '"etag-1"', "ChecksumSHA256": b64(digest)}]
                },
            },
        )

        upload_id = store.start_upload(tenant, name, "video/mp4")
        etag = store.write_part(tenant, name, upload_id, 1, 0, data, digest)
        store.complete_upload(tenant, name, upload_id, [Part(1, etag, digest)], 8 * MIB, len(data))

        stubber.assert_no_pending_responses()
    assert etag == '"etag-1"'


def test_s3_invalid_part_names_the_mismatched_numbers() -> None:
    client = make_client()
    store = S3ObjectStore(BUCKET, "training", client)
    tenant, name = uuid4(), video_name()
    key = f"training/{tenant}/{name}"
    data = source_bytes(10)
    parts = [
        Part(number, f'"etag-{number}"', hashlib.sha256(chunk).digest())
        for number, _offset, chunk in pieces(data, 4)
    ]
    listed = [
        {"PartNumber": part.number, "ETag": part.etag, "ChecksumSHA256": b64(part.sha256)}
        for part in parts
    ]
    listed[2]["ChecksumSHA256"] = b64(hashlib.sha256(b"something else").digest())
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "complete_multipart_upload",
            service_error_code="InvalidPart",
            service_message=f"One or more of the specified parts could not be found: {key}",
            http_status_code=400,
        )
        stubber.add_response(
            "list_parts",
            {"Parts": listed, "IsTruncated": False},
            {"Bucket": BUCKET, "Key": key, "UploadId": "upload-1"},
        )

        with pytest.raises(PartMismatch) as raised:
            store.complete_upload(tenant, name, "upload-1", parts, 4, len(data))

        stubber.assert_no_pending_responses()
    assert raised.value.numbers == [3]
    assert key not in str(raised.value) and name not in str(raised.value)


def test_s3_complete_after_a_lost_answer_is_done() -> None:
    client = make_client()
    store = S3ObjectStore(BUCKET, "training", client)
    tenant, name = uuid4(), video_name()
    key = f"training/{tenant}/{name}"
    data = source_bytes(10)
    parts = [Part(1, '"etag-1"', hashlib.sha256(data).digest())]
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "complete_multipart_upload", service_error_code="NoSuchUpload", http_status_code=404
        )
        stubber.add_response("head_object", {"ContentLength": 10}, {"Bucket": BUCKET, "Key": key})

        store.complete_upload(tenant, name, "upload-1", parts, 8 * MIB, len(data))

        stubber.assert_no_pending_responses()

    # The upload is gone and nothing was published: that is not done.
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "complete_multipart_upload", service_error_code="NoSuchUpload", http_status_code=404
        )
        stubber.add_client_error("head_object", service_error_code="404", http_status_code=404)

        with pytest.raises(StorageNotFound):
            store.complete_upload(tenant, name, "upload-1", parts, 8 * MIB, len(data))


def test_s3_list_uploads_is_per_company(client: Any) -> None:
    store = S3ObjectStore(BUCKET, "training", client, list_page_size=1)
    first, second = uuid4(), uuid4()
    first_names = sorted([video_name(), video_name()])
    second_name = video_name()
    first_ids = {name: store.start_upload(first, name, "video/mp4") for name in first_names}
    second_id = store.start_upload(second, second_name, "video/webm")
    # Another prefix in the same bucket is never listed.
    S3ObjectStore(BUCKET, "kb", client).start_upload(first, video_name(), "video/mp4")

    first_uploads = store.list_uploads(first)
    second_uploads = store.list_uploads(second)

    assert sorted((u.name, u.upload_id) for u in first_uploads) == sorted(first_ids.items())
    assert [(u.name, u.upload_id) for u in second_uploads] == [(second_name, second_id)]
    assert all(upload.started_at.tzinfo is not None for upload in first_uploads)
    store.abort_upload(first, first_names[0], first_ids[first_names[0]])
    store.abort_upload(first, first_names[0], first_ids[first_names[0]])  # missing is fine
    assert [upload.name for upload in store.list_uploads(first)] == [first_names[1]]


def test_s3_presigned_playback_is_inline_no_store_for_15_minutes() -> None:
    store = S3ObjectStore.create(BUCKET, REGION, "testing", FAKE_SECRET, "training")
    tenant, name = uuid4(), video_name()

    url = store.presigned_playback(
        tenant, name, limits.PLAYBACK_URL_SECONDS, "video/mp4", "Induction day 1.mp4"
    )

    assert url is not None
    parts = urlsplit(url)
    query = parts.query
    assert "X-Amz-Expires=900" in query
    assert "response-content-type=video%2Fmp4" in query
    assert "response-cache-control=private%2C%20no-store" in query
    disposition = parse_qs(query)["response-content-disposition"][0]
    assert disposition.startswith("inline")
    assert disposition == "inline; filename*=utf-8''Induction%20day%201.mp4"
    host = parts.hostname or ""
    key_path = f"/training/{tenant}/{name}"
    assert (host.endswith(f".s3.{REGION}.amazonaws.com") and parts.path == key_path) or (
        parts.path == f"/{BUCKET}{key_path}"
    )
    assert FAKE_SECRET not in url
    # The worker reads the same object through a plain presigned GET.
    source = urlsplit(store.media_source(tenant, name, limits.PLAYBACK_URL_SECONDS))
    assert source.path in {key_path, f"/{BUCKET}{key_path}"}
    with pytest.raises(StorageError):
        store.open_range(tenant, name)


def test_s3_errors_never_echo_the_key_or_url() -> None:
    client = make_client()
    store = S3ObjectStore(BUCKET, "training", client)
    tenant, name = uuid4(), video_name()
    key = f"training/{tenant}/{name}"
    data = source_bytes(10)
    digest = hashlib.sha256(data).digest()
    url = f"https://{BUCKET}.s3.amazonaws.com/{key}?X-Amz-Signature=abc"
    calls = [
        ("create_multipart_upload", lambda: store.start_upload(tenant, name, "video/mp4")),
        ("upload_part", lambda: store.write_part(tenant, name, "u-1", 1, 0, data, digest)),
        (
            "complete_multipart_upload",
            lambda: store.complete_upload(
                tenant, name, "u-1", [Part(1, '"e"', digest)], 8 * MIB, len(data)
            ),
        ),
        ("abort_multipart_upload", lambda: store.abort_upload(tenant, name, "u-1")),
        ("list_multipart_uploads", lambda: store.list_uploads(tenant)),
    ]
    for operation, call in calls:
        with Stubber(client) as stubber:
            stubber.add_client_error(
                operation,
                service_error_code="AccessDenied",
                service_message=f"Access Denied for {key} at {url} with {FAKE_SECRET}",
                http_status_code=403,
            )
            with pytest.raises(StorageError) as raised:
                call()

        message = str(raised.value)
        assert not isinstance(raised.value, StorageNotFound), operation
        assert "AccessDenied" in message, operation
        for secret in (key, name, str(tenant), url, "training/", FAKE_SECRET, BUCKET):
            assert secret not in message, (operation, secret)


def test_video_limits_match_the_design() -> None:
    assert limits.MAX_VIDEO_BYTES == 500 * MIB
    assert limits.VIDEO_PIECE_BYTES == 8 * MIB
    assert limits.MAX_VIDEO_TOTAL_BYTES == 5 * 1024 * MIB
    assert (limits.MAX_VIDEO_SECONDS, limits.MAX_VIDEO_ITEMS) == (3600, 50)
    assert (limits.MAX_UPLOADS_IN_PROGRESS, limits.PIECES_IN_FLIGHT) == (2, 4)
    assert (limits.PLAYBACK_URL_SECONDS, limits.UPLOAD_ABANDON_SECONDS) == (900, 86400)
    assert limits.VIDEO_EXTENSIONS[".mov"] == "mp4"
    assert limits.PLAYBACK_MEDIA_TYPE[limits.VIDEO_EXTENSIONS[".mov"]] == "video/mp4"
    assert limits.PLAYBACK_MEDIA_TYPE[limits.VIDEO_EXTENSIONS[".webm"]] == "video/webm"
    assert sorted(limits.VIDEO_EXTENSIONS) == [".m4v", ".mov", ".mp4", ".webm"]
    assert os.path.splitext("a.m4v")[1] in limits.VIDEO_EXTENSIONS
