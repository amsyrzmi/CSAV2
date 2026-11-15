"""The console upload and remove steps every owner's picture route shares.

An owner's route (a QR payment method, a plan, a product) first authorises with its own write rule,
so a refused role never has its body read, then calls `upload_picture` or `drop_picture` with a
`find_subject` that raises the owner's own 404 (or 422) inside the same tenant transaction as the
write. Refusals of the picture check are `{"detail": "<code>"}`: 422, except `picture_too_large`
(413) and `picture_limit` (409). An oversize body is refused by the parser with its own 413.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from starlette.datastructures import UploadFile

from csa.console.contracts import ConsoleActor
from csa.console.multipart import upload_form
from csa.core.tenancy import tx
from csa.media.contracts import PictureKind, PictureOut
from csa.media.images import MAX_PICTURE_BYTES, PictureRefused, check_image
from csa.media.service import remove_picture, set_picture

__all__ = [
    "PICTURE_UPLOAD_BODY",
    "drop_picture",
    "read_picture_upload",
    "refusal",
    "upload_picture",
]

FindSubject = Callable[[AsyncConnection[Any]], Awaitable[None]]

# The request body the upload routes take (they read it themselves, after authorising).
PICTURE_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}

_STATUS = {"picture_too_large": 413, "picture_limit": 409}


def refusal(exc: PictureRefused) -> HTTPException:
    """The HTTP answer for a refused picture: its code, as 422, 413 or 409."""
    return HTTPException(_STATUS.get(exc.code, 422), exc.code)


def _checked_name(item: UploadFile) -> str:
    """The rule of `csa.hr.documents._checked_name`: a plain, printable base name."""
    name = item.filename or ""
    if (
        not name.strip()
        or len(name) > 255
        or name != Path(name).name
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise HTTPException(422, "picture_name_invalid")
    return name


async def read_picture_upload(request: Request) -> tuple[str, bytes]:
    """The one uploaded file's name and bytes (field `file`), read within the 2 MiB bound."""
    async with upload_form(
        request,
        max_files=1,
        max_body=MAX_PICTURE_BYTES + 65536,
        max_file=MAX_PICTURE_BYTES,
        field="file",
    ) as form:
        items = form.multi_items()
        if len(items) != 1 or not isinstance(items[0][1], UploadFile):
            raise HTTPException(422, "upload one file")
        item = items[0][1]
        name = _checked_name(item)
        data = await item.read(MAX_PICTURE_BYTES + 1)
    return name, data


async def upload_picture(
    request: Request,
    client_id: UUID,
    actor: ConsoleActor,
    kind: PictureKind,
    subject_key: str,
    find_subject: FindSubject,
) -> PictureOut:
    """Read, check and keep the uploaded picture; the caller has already authorised `actor`."""
    name, data = await read_picture_upload(request)
    try:
        # The header check is pure Python (a fraction of a second for a hostile file): keep it
        # off the event loop.
        info = await asyncio.to_thread(check_image, data, name)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await find_subject(conn)
            return await set_picture(conn, client_id, actor, kind, subject_key, data, info)
    except PictureRefused as exc:
        raise refusal(exc) from None


async def drop_picture(
    request: Request,
    client_id: UUID,
    actor: ConsoleActor,
    kind: PictureKind,
    subject_key: str,
    find_subject: FindSubject,
) -> None:
    """Remove the subject's picture (nothing to remove is fine); the caller has authorised."""
    async with tx(request.app.state.pool, str(client_id)) as conn:
        await find_subject(conn)
        await remove_picture(conn, client_id, actor, kind, subject_key)
