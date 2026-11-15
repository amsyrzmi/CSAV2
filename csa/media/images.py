"""The picture check: JPEG or PNG only, read from the headers, no image library.

A picture is kept only when its extension agrees with its content (as `csa.hr.documents.sniff`
does for employee documents), it is at most 2 MiB, each side is 64-4096 px and the longer side is
at most 10 times the shorter. The size comes from the PNG IHDR chunk or the first JPEG SOF segment.
The file must also end at its end marker (PNG IEND, JPEG EOI): bytes appended after it are
refused, which stops a picture with an archive or a web page tacked on the end. Content hidden
inside the image (a comment segment, a text chunk) is not detected here: the read route contains
it with `nosniff`, a `Content-Security-Policy` of `default-src 'none'; sandbox` and a stored image
type. Pixels are never decoded, so a hostile picture costs one pass over its bytes at most.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = [
    "MAX_PICTURES",
    "MAX_PICTURE_BYTES",
    "MAX_SIDE",
    "MIN_SIDE",
    "ImageInfo",
    "PictureRefused",
    "check_image",
    "read_dimensions",
]

MAX_PICTURE_BYTES = 2097152
MIN_SIDE = 64
MAX_SIDE = 4096
MAX_RATIO = 10
MAX_PICTURES = 300  # uploads per company; generated pictures do not count

MediaType = Literal["image/jpeg", "image/png"]

_EXTENSIONS: dict[str, MediaType] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_MAGIC: dict[MediaType, bytes] = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
_PNG_IHDR = b"\x00\x00\x00\x0dIHDR"
_PNG_IEND = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"
_JPEG_EOI = b"\xff\xd9"
# SOF0-3, SOF5-7, SOF9-11, SOF13-15 carry the frame size; C4, C8 and CC are not frame headers.
_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_SOS = 0xDA
_STANDALONE = frozenset({0x01, 0xD8, *range(0xD0, 0xD8)})  # markers with no length field


class PictureRefused(Exception):
    """A picture the store will not keep; `code` is the plain reason the console shows."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ImageInfo:
    media_type: MediaType
    width: int
    height: int
    byte_size: int
    sha256: str


def _sniff(data: bytes, file_name: str | None) -> MediaType:
    """The picture's type; with a file name, only when its extension agrees with the content."""
    if file_name is not None:
        wanted = _EXTENSIONS.get(Path(file_name).suffix.lower())
        if wanted is None or not data.startswith(_MAGIC[wanted]):
            raise PictureRefused("picture_type_unsupported")
        return wanted
    for media_type, magic in _MAGIC.items():
        if data.startswith(magic):
            return media_type
    raise PictureRefused("picture_type_unsupported")


def _png_size(data: bytes) -> tuple[int, int]:
    # Signature (8), then the first chunk, which must be IHDR: length 13, name, width, height.
    if len(data) < 33 or data[8:16] != _PNG_IHDR or not data.endswith(_PNG_IEND):
        raise PictureRefused("picture_unreadable")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int]:
    if not data.endswith(_JPEG_EOI):
        raise PictureRefused("picture_unreadable")
    end = len(data)
    pos = 2  # after SOI
    while pos < end:
        if data[pos] != 0xFF:
            raise PictureRefused("picture_unreadable")
        while pos < end and data[pos] == 0xFF:  # fill bytes before a marker
            pos += 1
        if pos >= end:
            break
        marker = data[pos]
        pos += 1
        if marker in _STANDALONE or marker == 0x00:
            continue
        if marker == _SOS:
            break  # the scan starts: no frame header came first
        if pos + 2 > end:
            break
        (length,) = struct.unpack(">H", data[pos : pos + 2])
        if length < 2 or pos + length > end:
            break
        if marker in _SOF:
            if length < 8:
                break
            height, width = struct.unpack(">HH", data[pos + 3 : pos + 7])
            return width, height
        pos += length
    raise PictureRefused("picture_unreadable")


def read_dimensions(data: bytes, media_type: str) -> tuple[int, int]:
    """(width, height) from a JPEG or PNG header; `picture_unreadable` when it is malformed."""
    if media_type == "image/png":
        return _png_size(data)
    if media_type == "image/jpeg":
        return _jpeg_size(data)
    raise PictureRefused("picture_type_unsupported")


def check_image(data: bytes, file_name: str | None) -> ImageInfo:
    """What the store needs to keep `data`, or a `PictureRefused` with a plain code.

    `file_name=None` (a picture the system made itself) skips only the extension rule: the content
    must still be a JPEG or a PNG with sane dimensions.
    """
    if not data:
        raise PictureRefused("picture_empty")
    if len(data) > MAX_PICTURE_BYTES:
        raise PictureRefused("picture_too_large")
    media_type = _sniff(data, file_name)
    width, height = read_dimensions(data, media_type)
    short, long = sorted((width, height))
    if short < MIN_SIDE or long > MAX_SIDE or long > MAX_RATIO * short:
        raise PictureRefused("picture_dimensions")
    return ImageInfo(media_type, width, height, len(data), hashlib.sha256(data).hexdigest())
