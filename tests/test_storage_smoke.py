"""The owner-run storage smoke script: exit codes, one line per check, no secrets in output."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from uuid import UUID

import boto3
import httpx
import pytest
import requests
from csa.core.settings import Settings
from csa.storage.factory import Stores
from csa.storage.local import LocalObjectStore
from csa.storage.s3 import S3ObjectStore
from moto import mock_aws
from scripts import storage_smoke

# Settings looks for /run/secrets even when told not to; the folder is absent off Docker.
pytestmark = pytest.mark.filterwarnings("ignore:directory .* does not exist:UserWarning")

BUCKET = "smoke-bucket"
REGION = "us-east-1"
FAKE_KEY_ID = "fake-key-id"
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
        S3_ACCESS_KEY_ID=FAKE_KEY_ID,
        S3_SECRET_ACCESS_KEY=FAKE_SECRET,
    )


def result_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(("PASS ", "FAIL "))]


def test_smoke_passes_against_moto_and_prints_no_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name=REGION,
            aws_access_key_id=FAKE_KEY_ID,
            aws_secret_access_key=FAKE_SECRET,
        )
        client.create_bucket(Bucket=BUCKET)

        with moto_http() as http:
            code = storage_smoke.run(s3_settings(), http=http)

        leftovers = client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    output = capsys.readouterr().out
    lines = result_lines(output)

    assert code == 0
    assert leftovers == []
    assert not [line for line in lines if line.startswith("FAIL ")]
    for store in ("kb", "hr", "training"):
        for check in (
            "startup check",
            "put then read",
            "list contains name",
            "open streams and closes",
            "delete then read missing",
            "delete missing is not an error",
            "read missing raises",
        ):
            assert any(line.startswith(f"PASS {store}: {check}") for line in lines), (store, check)
    assert any("StorageNotFound" in line or "StorageError" in line for line in lines)
    for secret in (FAKE_KEY_ID, FAKE_SECRET, storage_smoke.PAYLOAD.decode("ascii")):
        assert secret not in output


def test_smoke_checks_a_local_training_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "training"
    root.mkdir()

    code = storage_smoke.run(make_settings(TRAINING_UPLOAD_ROOT=str(root)))

    lines = result_lines(capsys.readouterr().out)
    assert code == 0
    assert any(line.startswith("PASS training: put then read") for line in lines)
    assert not [line for line in lines if line.startswith(("PASS kb", "PASS hr"))]
    for check in MULTIPART_CHECKS:
        assert any(line.startswith(f"PASS training: {check}") for line in lines), check
    # The gateway serves local files itself: there is no presigned link to try.
    assert not [line for line in lines if "presigned" in line]
    assert not [path for path in root.rglob("*") if path.is_file()]


class CorruptingStore(LocalObjectStore):
    """A store whose reads return the wrong bytes, so the round-trip check must fail."""

    def read(self, tenant: UUID, name: str, limit: int) -> bytes:
        return b"not what was written"


def test_smoke_reports_failed_check_with_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    kb_root, hr_root = tmp_path / "kb", tmp_path / "hr"
    kb_root.mkdir()
    hr_root.mkdir()
    stores = Stores(kb=CorruptingStore(kb_root), hr=LocalObjectStore(hr_root))
    monkeypatch.setattr(storage_smoke, "build_stores", lambda settings: stores)

    code = storage_smoke.run(make_settings())

    output = capsys.readouterr().out
    lines = result_lines(output)
    assert code == 1
    assert "FAIL kb: put then read (read returned different bytes)" in output
    assert any(line.startswith("PASS hr: put then read") for line in lines)
    assert not [line for line in lines if line.startswith("FAIL hr:")]
    assert not [path for path in kb_root.rglob("*") if path.is_file()]
    assert not [path for path in hr_root.rglob("*") if path.is_file()]


def test_smoke_exits_2_without_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No store configured at all.
    assert storage_smoke.run(make_settings()) == 2
    assert "no object store is configured" in capsys.readouterr().err

    # Settings that do not load (incomplete s3 setup).
    def broken() -> Settings:
        return make_settings(STORAGE_BACKEND="s3")

    monkeypatch.setattr(storage_smoke, "get_settings", broken)
    assert storage_smoke.main() == 2
    assert "settings are invalid" in capsys.readouterr().err


class LeakyFailingStore(S3ObjectStore):
    """An S3 store that reads wrong bytes and fails deletes with an error full of secrets."""

    def read(self, tenant: UUID, name: str, limit: int) -> bytes:
        return storage_smoke.PAYLOAD[:-1]

    def delete(self, tenant: UUID, name: str) -> None:
        raise RuntimeError(f"{FAKE_KEY_ID} {FAKE_SECRET} {storage_smoke.PAYLOAD.decode('ascii')}")


def test_failed_checks_print_no_secrets_or_payload(capsys: pytest.CaptureFixture[str]) -> None:
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name=REGION,
            aws_access_key_id=FAKE_KEY_ID,
            aws_secret_access_key=FAKE_SECRET,
        )
        client.create_bucket(Bucket=BUCKET)
        store = LeakyFailingStore(BUCKET, "kb", client)

        code = storage_smoke._check_stores("kb", store, UUID(int=1))

    captured = capsys.readouterr()
    assert code is False
    # The script's own assertion text is shown, other errors are named by class alone.
    assert "FAIL kb: put then read (read returned different bytes)" in captured.out
    assert "FAIL kb: delete then read missing (RuntimeError)" in captured.out
    for secret in (FAKE_KEY_ID, FAKE_SECRET, storage_smoke.PAYLOAD.decode("ascii")):
        assert secret not in captured.out
        assert secret not in captured.err


MULTIPART_CHECKS = (
    "multipart: start, three pieces, complete",
    "multipart: read back",
    "multipart: abort an upload",
    "multipart: cleanup leaves nothing behind",
)


def moto_http() -> httpx.Client:
    """An httpx client whose requests reach moto: moto answers ``requests`` calls, not httpx."""

    def handler(request: httpx.Request) -> httpx.Response:
        answer = requests.request(
            request.method, str(request.url), headers=dict(request.headers), timeout=10
        )
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_smoke_runs_a_multipart_round_trip_and_a_presigned_range_on_moto(
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[httpx.Request] = []
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name=REGION,
            aws_access_key_id=FAKE_KEY_ID,
            aws_secret_access_key=FAKE_SECRET,
        )
        client.create_bucket(Bucket=BUCKET)
        with moto_http() as http:
            http.event_hooks["request"].append(seen.append)
            code = storage_smoke.run(s3_settings(), http=http)

        leftovers = client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
        uploads = client.list_multipart_uploads(Bucket=BUCKET).get("Uploads", [])
    output = capsys.readouterr().out
    lines = result_lines(output)

    assert code == 0, output
    assert leftovers == [] and uploads == []
    assert not [line for line in lines if line.startswith("FAIL ")]
    for check in (*MULTIPART_CHECKS, "presigned range answers 206"):
        assert any(line.startswith(f"PASS training: {check}") for line in lines), check
    # Multipart is the training store's alone.
    for store in ("kb", "hr"):
        assert not [line for line in lines if line.startswith(f"PASS {store}: multipart")]
    # One ranged GET of the training object, through the presigned link.
    assert len(seen) == 1
    assert seen[0].headers["Range"] == "bytes=0-15"
    assert "/training/" in seen[0].url.path
    # Content-free: no key, link, signature, bucket or object name.
    for secret in (FAKE_KEY_ID, FAKE_SECRET, BUCKET, "http", "Signature", "X-Amz", ".bin"):
        assert secret not in output, secret


class WrongRangeStore(S3ObjectStore):
    """Hands out a presigned link to another object: the range check must fail."""

    def presigned_playback(
        self, tenant: UUID, name: str, ttl_seconds: int, media_type: str, file_name: str
    ) -> str | None:
        return super().presigned_playback(tenant, "missing.bin", ttl_seconds, media_type, file_name)


def test_smoke_fails_a_presigned_range_that_is_not_206(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name=REGION,
            aws_access_key_id=FAKE_KEY_ID,
            aws_secret_access_key=FAKE_SECRET,
        )
        client.create_bucket(Bucket=BUCKET)
        store = WrongRangeStore(BUCKET, "training", client)
        with moto_http() as http:
            passed = storage_smoke._check_multipart("training", store, UUID(int=2), http)
        leftovers = client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    output = capsys.readouterr().out
    assert passed is False
    # moto answers a link to a missing object 403 (S3 itself answers 403 or 404).
    assert re.search(
        r"^FAIL training: presigned range answers 206 \(answered 40[34], expected 206\)$",
        output,
        flags=re.MULTILINE,
    )
    assert leftovers == []
    assert "http" not in output and BUCKET not in output
