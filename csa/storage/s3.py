"""S3 object store: ``<prefix>/<tenant>/<name>`` keys over blocking boto3 calls."""

from __future__ import annotations

from typing import Any, BinaryIO, cast
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from csa.storage.base import StorageError, StorageNotFound, validate_name

NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}
INVALID_RANGE_CODE = "InvalidRange"


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
        config = Config(
            signature_version="s3v4",
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
