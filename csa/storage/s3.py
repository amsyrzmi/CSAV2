"""S3 object store: ``<prefix>/<tenant>/<name>`` keys over blocking boto3 calls."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any, BinaryIO, cast
from urllib.parse import quote
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from csa.storage.base import StorageError, StorageNotFound, validate_name
from csa.storage.multipart import OpenUpload, Part, PartMismatch, missing_numbers

NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound", "NoSuchUpload"}
INVALID_RANGE_CODE = "InvalidRange"
NO_SUCH_UPLOAD_CODE = "NoSuchUpload"
INVALID_PART_CODE = "InvalidPart"
BAD_DIGEST_CODE = "BadDigest"


class S3ObjectStore:
    def __init__(self, bucket: str, prefix: str, client: Any, list_page_size: int = 1000) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.client = client
        self.list_page_size = list_page_size

    @classmethod
    def create(
        cls,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        prefix: str,
    ) -> S3ObjectStore:
        # Virtual addressing makes presigned URLs use the regional host
        # (<bucket>.s3.<region>.amazonaws.com) rather than the global one, which redirects;
        # botocore still falls back to path style for a bucket name with dots.
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        )
        client = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=config,
        )
        return cls(bucket, prefix, client)

    def check(self) -> None:
        # head_bucket would be denied: the production key may only list under its prefixes.
        try:
            self.client.list_objects_v2(Bucket=self.bucket, Prefix=f"{self.prefix}/", MaxKeys=1)
        except (ClientError, BotoCoreError) as exc:
            raise self._error("check", exc) from exc

    def put(self, tenant: UUID, name: str, data: bytes) -> None:
        key = self._key(tenant, name)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ServerSideEncryption="AES256",
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._error("put", exc) from exc

    def open(self, tenant: UUID, name: str) -> BinaryIO:
        key = self._key(tenant, name)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise self._error("open", exc) from exc
        return cast(BinaryIO, response["Body"])

    def read(self, tenant: UUID, name: str, limit: int) -> bytes:
        if limit < 0:
            raise ValueError("limit must not be negative")
        key = self._key(tenant, name)
        if limit == 0:
            return b""
        try:
            byte_range = f"bytes=0-{limit - 1}"
            response = self.client.get_object(Bucket=self.bucket, Key=key, Range=byte_range)
        except ClientError as exc:
            if _error_code(exc) == INVALID_RANGE_CODE:
                # S3 refuses any Range on a 0-byte object; let ingest report "file is empty".
                return b""
            raise self._error("read", exc) from exc
        except BotoCoreError as exc:
            raise self._error("read", exc) from exc
        body = response["Body"]
        try:
            return cast(bytes, body.read())[:limit]
        except (OSError, BotoCoreError) as exc:
            raise self._error("read", exc) from exc
        finally:
            body.close()

    def delete(self, tenant: UUID, name: str) -> None:
        key = self._key(tenant, name)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise self._error("delete", exc) from exc

    def list_names(self, tenant: UUID) -> list[str]:
        # The trailing slash keeps tenant ids that share leading characters apart.
        tenant_prefix = f"{self.prefix}/{tenant}/"
        names: list[str] = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            pages = paginator.paginate(
                Bucket=self.bucket,
                Prefix=tenant_prefix,
                PaginationConfig={"PageSize": self.list_page_size},
            )
            for page in pages:
                names.extend(item["Key"][len(tenant_prefix) :] for item in page.get("Contents", []))
        except (ClientError, BotoCoreError) as exc:
            raise self._error("list", exc) from exc
        return sorted(name for name in names if name)

    # Multipart: each piece is an S3 part whose SHA-256 S3 checks on arrival and on completion.

    def start_upload(self, tenant: UUID, name: str, media_type: str) -> str | None:
        key = self._key(tenant, name)
        try:
            response = self.client.create_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                ContentType=media_type,
                ServerSideEncryption="AES256",
                ChecksumAlgorithm="SHA256",
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._error("start", exc) from exc
        return str(response["UploadId"])

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
        key = self._key(tenant, name)
        try:
            response = self.client.upload_part(
                Bucket=self.bucket,
                Key=key,
                UploadId=_require_upload_id(upload_id),
                PartNumber=number,
                Body=data,
                ChecksumSHA256=_b64(sha256),
            )
        except ClientError as exc:
            if _error_code(exc) == BAD_DIGEST_CODE:
                raise PartMismatch([number]) from exc
            raise self._error("write", exc) from exc
        except BotoCoreError as exc:
            raise self._error("write", exc) from exc
        return str(response["ETag"])

    def complete_upload(
        self,
        tenant: UUID,
        name: str,
        upload_id: str | None,
        parts: Sequence[Part],
        piece_size: int,
        total_bytes: int,
    ) -> None:
        key = self._key(tenant, name)
        upload = _require_upload_id(upload_id)
        missing = missing_numbers(parts, piece_size, total_bytes)
        if missing:
            raise PartMismatch(missing)
        ordered = sorted(parts, key=lambda part: part.number)
        try:
            self.client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload,
                MultipartUpload={
                    "Parts": [
                        {
                            "PartNumber": part.number,
                            "ETag": part.etag,
                            "ChecksumSHA256": _b64(part.sha256),
                        }
                        for part in ordered
                    ]
                },
            )
        except ClientError as exc:
            code = _error_code(exc)
            if code == INVALID_PART_CODE:
                changed = self._changed_parts(key, upload, ordered)
                if changed:
                    raise PartMismatch(changed) from exc
            elif code == NO_SUCH_UPLOAD_CODE and self._published(key):
                return  # completed by an earlier call whose answer was lost
            raise self._error("complete", exc) from exc
        except BotoCoreError as exc:
            raise self._error("complete", exc) from exc

    def abort_upload(self, tenant: UUID, name: str, upload_id: str | None) -> None:
        key = self._key(tenant, name)
        if upload_id is None:
            return
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            if _error_code(exc) == NO_SUCH_UPLOAD_CODE:
                return
            raise self._error("abort", exc) from exc
        except BotoCoreError as exc:
            raise self._error("abort", exc) from exc

    def list_uploads(self, tenant: UUID) -> list[OpenUpload]:
        tenant_prefix = f"{self.prefix}/{tenant}/"
        uploads: list[OpenUpload] = []
        try:
            paginator = self.client.get_paginator("list_multipart_uploads")
            pages = paginator.paginate(
                Bucket=self.bucket,
                Prefix=tenant_prefix,
                PaginationConfig={"PageSize": self.list_page_size},
            )
            for page in pages:
                for item in page.get("Uploads", []):
                    name = str(item["Key"])[len(tenant_prefix) :]
                    if name and "/" not in name:
                        uploads.append(OpenUpload(name, str(item["UploadId"]), item["Initiated"]))
        except (ClientError, BotoCoreError) as exc:
            raise self._error("list uploads", exc) from exc
        return sorted(uploads, key=lambda upload: (upload.name, upload.started_at))

    def media_source(self, tenant: UUID, name: str, ttl_seconds: int) -> str:
        return self._presign(self._key(tenant, name), ttl_seconds, {})

    def presigned_playback(
        self, tenant: UUID, name: str, ttl_seconds: int, media_type: str, file_name: str
    ) -> str | None:
        # S3 answers Range requests itself; these response overrides make it play inline and
        # keep shared caches from storing it. The URL is a bearer link: never log it.
        overrides = {
            "ResponseContentType": media_type,
            "ResponseContentDisposition": f"inline; filename*=utf-8''{quote(file_name, safe='')}",
            "ResponseCacheControl": "private, no-store",
        }
        return self._presign(self._key(tenant, name), ttl_seconds, overrides)

    def object_size(self, tenant: UUID, name: str) -> int:
        key = self._key(tenant, name)
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise self._error("head", exc) from exc
        return int(response["ContentLength"])

    def open_range(self, tenant: UUID, name: str) -> BinaryIO:
        raise StorageError("object storage open range: not supported on s3, use playback")

    def _presign(self, key: str, ttl_seconds: int, overrides: dict[str, str]) -> str:
        try:
            return str(
                self.client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key, **overrides},
                    ExpiresIn=ttl_seconds,
                )
            )
        except (ClientError, BotoCoreError) as exc:
            raise self._error("presign", exc) from exc

    def _changed_parts(self, key: str, upload_id: str, parts: Sequence[Part]) -> list[int]:
        stored: dict[int, tuple[str | None, str]] = {}
        try:
            paginator = self.client.get_paginator("list_parts")
            for page in paginator.paginate(Bucket=self.bucket, Key=key, UploadId=upload_id):
                for item in page.get("Parts", []):
                    stored[int(item["PartNumber"])] = (
                        item.get("ChecksumSHA256"),
                        str(item.get("ETag", "")).strip('"'),
                    )
        except (ClientError, BotoCoreError) as exc:
            raise self._error("list parts", exc) from exc
        return [
            part.number
            for part in parts
            if stored.get(part.number) != (_b64(part.sha256), part.etag.strip('"'))
        ]

    def _published(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in NOT_FOUND_CODES:
                return False
            raise self._error("complete", exc) from exc
        except BotoCoreError as exc:
            raise self._error("complete", exc) from exc
        return True

    def _key(self, tenant: UUID, name: str) -> str:
        return f"{self.prefix}/{tenant}/{validate_name(name)}"

    @staticmethod
    def _error(operation: str, exc: Exception) -> StorageError:
        # Describe the failure by its code only; never echo the exception text or any credentials.
        if isinstance(exc, ClientError):
            code = _error_code(exc)
            if code in NOT_FOUND_CODES:
                return StorageNotFound(f"object storage {operation}: object not found")
            return StorageError(f"object storage {operation} failed: {code}")
        return StorageError(f"object storage {operation} failed: {type(exc).__name__}")


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "Unknown"))


def _b64(digest: bytes) -> str:
    return base64.b64encode(digest).decode("ascii")


def _require_upload_id(upload_id: str | None) -> str:
    if upload_id is None:
        raise ValueError("an S3 upload needs its upload id")
    return upload_id
