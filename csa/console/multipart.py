"""Bound multipart parsing before buffering; close spools on every failure path."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from email.message import Message

from fastapi import HTTPException, Request
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import FormData
from starlette.formparsers import MultiPartException, MultiPartParser

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_BODY_BYTES = 3 * MAX_FILE_BYTES + 1024 * 1024


class BoundedParser(MultiPartParser):
    def __init__(self, request: Request) -> None:
        async def stream() -> AsyncGenerator[bytes, None]:
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    raise HTTPException(413, "upload request exceeds 31 MB")
                yield chunk

        super().__init__(request.headers, stream(), max_files=3, max_fields=0, max_part_size=1024)
        self.complete = False
        self.part_bytes = 0
        self.header_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self.part_bytes = 0
        self.header_bytes = 0

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.part_bytes += end - start
        if self.part_bytes > MAX_FILE_BYTES:
            raise HTTPException(413, "file exceeds 10 MB")
        super().on_part_data(data, start, end)

    def _header_bound(self, size: int) -> None:
        self.header_bytes += size
        if self.header_bytes > 8192:
            raise HTTPException(413, "multipart headers exceed 8 KB")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_bound(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_bound(end - start)
        super().on_header_value(data, start, end)

    def on_end(self) -> None:
        self.complete = True
        super().on_end()

    def on_headers_finished(self) -> None:
        disposition = self._current_part.content_disposition
        if disposition is None:
            raise HTTPException(422, "Content-Disposition is required")
        header = Message()
        header["Content-Disposition"] = disposition.decode("latin-1")
        original = header.get_filename() or ""
        # python-multipart normalizes old Windows full paths; reject them instead.
        if "/" in original or "\\" in original:
            raise HTTPException(422, "invalid original filename")
        super().on_headers_finished()


@asynccontextmanager
async def upload_form(request: Request) -> AsyncIterator[FormData]:
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise HTTPException(415, "multipart/form-data required")
    parser = BoundedParser(request)
    try:
        try:
            form = await parser.parse()
        except MultiPartException as exc:
            raise HTTPException(422, exc.message) from exc
        except MultipartParseError as exc:
            raise HTTPException(422, "malformed multipart body") from exc
        if not parser.complete:
            raise HTTPException(422, "incomplete multipart body")
        yield form
    finally:
        # Starlette closes these only on MultiPartException, not cancellation,
        # disconnect, parser errors or HTTP size rejection.
        for spool in parser._files_to_close_on_error:
            spool.close()
