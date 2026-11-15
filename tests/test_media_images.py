"""The picture check: type, size and dimensions from the headers only.

Bytes appended after the end marker (a picture with an archive or page tacked on) are refused.
Content hidden inside the image is not detected here; the read route's headers contain it.
"""

from __future__ import annotations

import hashlib

import pytest
from csa.media.images import (
    MAX_PICTURE_BYTES,
    ImageInfo,
    PictureRefused,
    check_image,
    read_dimensions,
)

from tests.media_images import jpeg, png


def refusal(data: bytes, name: str | None) -> str:
    with pytest.raises(PictureRefused) as caught:
        check_image(data, name)
    return caught.value.code


def test_check_image_accepts_jpeg_and_png() -> None:
    data = png(800, 800)
    assert check_image(data, "qr.png") == ImageInfo(
        "image/png", 800, 800, len(data), hashlib.sha256(data).hexdigest()
    )
    photo = jpeg(640, 480)
    for name in ("qr.jpg", "qr.jpeg", "QR.JPG"):
        info = check_image(photo, name)
        assert (info.media_type, info.width, info.height) == ("image/jpeg", 640, 480)
        assert info.byte_size == len(photo)
        assert info.sha256 == hashlib.sha256(photo).hexdigest()


def test_the_edges_are_accepted() -> None:
    assert check_image(png(64, 64), "a.png").width == 64
    assert check_image(png(4096, 4096), "a.png").height == 4096
    assert check_image(png(640, 64), "a.png").width == 640  # exactly 10:1
    assert check_image(png(64, 640), "a.png").height == 640


@pytest.mark.parametrize(
    ("data", "name", "code"),
    [
        (png(800, 800), "qr.jpg", "picture_type_unsupported"),
        (jpeg(800, 800), "qr.png", "picture_type_unsupported"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "qr.svg", "picture_type_unsupported"),
        (png(800, 800), "qr", "picture_type_unsupported"),
        (b"", "qr.png", "picture_empty"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024), "qr.png", "picture_too_large"),
        (png(800, 800, trailing=b"\x00" * MAX_PICTURE_BYTES), "qr.png", "picture_too_large"),
        (png(5000, 100), "qr.png", "picture_dimensions"),
        (png(63, 800), "qr.png", "picture_dimensions"),
        (png(4000, 300), "qr.png", "picture_dimensions"),
        (png(4097, 4097), "qr.png", "picture_dimensions"),
        (png(641, 64), "qr.png", "picture_dimensions"),
        (jpeg(300, 4000), "qr.jpg", "picture_dimensions"),
        (png(800, 800, trailing=b"PK\x03\x04 hidden archive"), "qr.png", "picture_unreadable"),
        (jpeg(800, 800, trailing=b"<html>"), "qr.jpg", "picture_unreadable"),
        (png(800, 800)[:20], "qr.png", "picture_unreadable"),
        (png(800, 800)[:-1], "qr.png", "picture_unreadable"),
        (jpeg(800, 800)[:-2], "qr.jpg", "picture_unreadable"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_check_image_refusals(data: bytes, name: str, code: str) -> None:
    assert refusal(data, name) == code


def test_a_png_whose_first_chunk_is_not_ihdr_is_unreadable() -> None:
    good = png(800, 800)
    assert refusal(good[:12] + b"IDAT" + good[16:], "qr.png") == "picture_unreadable"


def test_a_jpeg_with_no_frame_header_before_the_scan_is_unreadable() -> None:
    good = jpeg(800, 800)
    start = good.index(b"\xff\xc0")
    without_sof = good[:start] + good[start + 13 :]
    assert refusal(without_sof, "qr.jpg") == "picture_unreadable"
    # A segment whose length runs past the end of the file.
    assert refusal(good[:2] + b"\xff\xe0\xff\xff" + b"\x00" * 8 + b"\xff\xd9", "qr.jpg") == (
        "picture_unreadable"
    )


def test_a_jpeg_frame_header_may_follow_other_segments_and_fill_bytes() -> None:
    good = jpeg(300, 200)
    start = good.index(b"\xff\xc0")
    padded = good[:start] + b"\xff\xff" + good[start:]
    assert check_image(padded, "a.jpg").width == 300
    progressive = good[:start] + b"\xff\xc2" + good[start + 2 :]
    assert read_dimensions(progressive, "image/jpeg") == (300, 200)


def test_generated_pictures_skip_only_the_extension_rule() -> None:
    assert check_image(jpeg(320, 180), None).media_type == "image/jpeg"
    assert check_image(png(320, 180), None).media_type == "image/png"
    assert refusal(b"GIF89a" + b"\x00" * 64, None) == "picture_type_unsupported"
    assert refusal(jpeg(320, 20), None) == "picture_dimensions"
    assert refusal(b"", None) == "picture_empty"


def test_read_dimensions_gives_width_then_height() -> None:
    assert read_dimensions(png(300, 200), "image/png") == (300, 200)
    assert read_dimensions(jpeg(300, 200), "image/jpeg") == (300, 200)
