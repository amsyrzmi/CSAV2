"""Header-valid PNG and JPEG bytes for tests, built at run time (no media file is ever committed).

The pictures carry a real structure (signature, size header, end marker, correct PNG CRCs) but no
real pixels: the picture check reads headers only, so a 5000 x 100 image costs a few dozen bytes.
"""

from __future__ import annotations

import struct
import zlib


def _chunk(name: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(name + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + name + body + struct.pack(">I", crc)


def png(width: int, height: int, *, trailing: bytes = b"") -> bytes:
    """A PNG: signature, IHDR (8-bit grayscale), one IDAT, IEND; `trailing` follows IEND."""
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + _chunk(b"IEND", b"")
        + trailing
    )


def jpeg(width: int, height: int, *, trailing: bytes = b"") -> bytes:
    """A JPEG: SOI, APP0, SOF0 with the size, a minimal SOS, EOI; `trailing` follows EOI."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    sos = b"\xff\xda" + struct.pack(">HB", 8, 1) + b"\x01\x00" + b"\x00\x3f\x00" + b"\x00\x00"
    return b"\xff\xd8" + app0 + sof0 + sos + b"\xff\xd9" + trailing
