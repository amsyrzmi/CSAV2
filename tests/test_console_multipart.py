"""Streaming parser bounds do not rely on Content-Length or a completed form."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from csa.console import multipart
from fastapi import FastAPI, Request
from starlette.datastructures import UploadFile
from starlette.types import Message

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "kind", ["aggregate", "file", "header", "incomplete", "malformed", "field"]
)
async def test_stream_limits_and_failure_spool_cleanup(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[multipart.BoundedParser] = []
    parser_type = multipart.BoundedParser

    class Tracking(parser_type):
        def __init__(self, request: Request) -> None:
            super().__init__(request)
            captured.append(self)

    monkeypatch.setattr(multipart, "BoundedParser", Tracking)
    monkeypatch.setattr(multipart, "MAX_FILE_BYTES", 128)
    monkeypatch.setattr(multipart, "MAX_BODY_BYTES", 10000)
    app = FastAPI()

    @app.post("/")
    async def accept(request: Request) -> dict[str, Any]:
        async with multipart.upload_form(request) as form:
            return {"files": len([item for item in form.values() if isinstance(item, UploadFile)])}

    prefix = b'--x\r\nContent-Disposition: form-data; name="files"; filename="f.csv"\r\n\r\n'
    body = prefix + b"Question,Answer\nQ,A\n"
    expected = 413
    if kind == "aggregate":
        # Padding after a complete part still counts against the entire request.
        body += b"\r\n--x--\r\n" + b" " * 11000
    elif kind == "file":
        body += b"x" * 129 + b"\r\n--x--\r\n"
    elif kind == "header":
        body = b'--x\r\nContent-Disposition: form-data; name="files"; filename="' + b"a" * 9000
    elif kind == "malformed":
        body = b"broken body\r\n--x--\r\n"
        expected = 422
    elif kind == "field":
        body = b'--x\r\nContent-Disposition: form-data; name="text"\r\n\r\nnot a file\r\n--x--\r\n'
        expected = 422
    else:
        expected = 422

    async def chunks() -> AsyncIterator[bytes]:
        for offset in range(0, len(body), 32):
            yield body[offset : offset + 32]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        result = await client.post(
            "/", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=x"}
        )
    assert result.status_code == expected, result.text
    assert captured
    assert all(spool.closed for parser in captured for spool in parser._files_to_close_on_error)


@pytest.mark.parametrize("extra", [0, 1])
async def test_exact_31_mib_aggregate_boundary(extra: int) -> None:
    app = FastAPI()

    @app.post("/")
    async def accept(request: Request) -> dict[str, int]:
        async with multipart.upload_form(request) as form:
            return {"files": len(form)}

    body = (
        b'--x\r\nContent-Disposition: form-data; name="files"; filename="f.csv"\r\n\r\n'
        b"Question,Answer\nQ,A\n\r\n--x--\r\n"
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield body
        yield b" " * (multipart.MAX_BODY_BYTES - len(body) + extra)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        response = await client.post(
            "/", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=x"}
        )
    assert response.status_code == (200 if extra == 0 else 413), response.text


@pytest.mark.parametrize("disconnect", [False, True])
async def test_download_descriptor_survives_unlink_and_always_closes(
    tmp_path, disconnect: bool
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from csa.console.kb_uploads import FileRecord, SourceDownload

    path = tmp_path / "source.csv"
    data = b"Question,Answer\nSynthetic,Fixture\n"
    path.write_bytes(data)
    stream = path.open("rb")
    response = SourceDownload(
        stream,
        FileRecord(
            id=uuid4(),
            name="source.csv",
            media_type="text/csv",
            size_bytes=len(data),
            document_id=None,
            created_at=datetime.now(UTC),
            storage_name=f"{uuid4()}.csv",
            content_hash="synthetic",
            deleted_at=None,
        ),
    )
    path.unlink()
    received: list[bytes] = []

    async def send(message: Message) -> None:
        if disconnect:
            raise OSError("synthetic client disconnected")
        if message["type"] == "http.response.body":
            received.append(message["body"])

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    if disconnect:
        from starlette.requests import ClientDisconnect

        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    else:
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        assert b"".join(received) == data
    assert stream.closed
