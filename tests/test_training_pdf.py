"""PDF checks and text extraction for the training library (csa.training.pdf).

Every PDF here is synthetic: built with the pypdf writer, or hand-written bytes for
the hostile cases. ``make_pdf``, ``raw_pdf`` and ``bad_files`` are public so the
upload route tests can reuse them.
"""

from __future__ import annotations

import io
import random
import struct
import zlib
from collections.abc import Callable
from typing import Any

import pypdf
import pytest
from csa.training import limits
from csa.training import pdf as pdf_module
from csa.training.limits import MAX_PAGES
from csa.training.pdf import PdfInfo, PdfRefused, check_pdf, extract_text
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

PORTRAIT = (612, 792)
WIDESCREEN = (960, 540)


def _text_content(writer: PdfWriter, page: PageObject, text: str) -> None:
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 10 Tf 36 400 Td (" + text.encode("ascii") + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)


def make_pdf(
    pages: int = 1,
    *,
    size: tuple[int, int] = PORTRAIT,
    texts: list[str] | None = None,
    mutate: Callable[[PdfWriter], None] | None = None,
) -> bytes:
    """A synthetic PDF: ``pages`` blank pages of ``size``, with ``texts[i]`` drawn on page i."""
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(*size)
        if texts is not None:
            _text_content(writer, page, texts[index])
    if mutate is not None:
        mutate(writer)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def raw_pdf(objects: list[bytes]) -> bytes:
    """A hand-built PDF: object n is ``objects[n - 1]``, object 1 the catalog, exact xref."""
    out = bytearray(b"%PDF-1.7\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


UNLISTED = 999_999
ORPHAN = 888_888


def objstm_pdf(
    leaves: int,
    *,
    pad: int = 0,
    extra_objects: int = 0,
    unlisted_refs: int = 0,
    xref_index: bytes | None = None,
    shared_stream: bool = False,
    orphan: bytes | None = None,
) -> bytes:
    """A PDF 1.5 file whose leaves each sit in their own flate object stream.

    ``pad`` spaces follow each leaf inside its stream (flate packs them about 1000:1);
    ``extra_objects`` tiny integer objects share the first leaf's stream. A cross-reference
    stream indexes it all. ``unlisted_refs`` adds that many /Kids references to object
    ``UNLISTED``, which the xref maps into the first stream but the stream does not list.
    ``xref_index`` sets the xref stream's /Index array. ``shared_stream`` puts every leaf
    in one stream. ``orphan`` is the body of object ``ORPHAN``, listed first in the first
    stream but absent from the xref, so pypdf never caches it.
    """
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    compressed: dict[int, tuple[int, int]] = {}
    refs = [b"%d 0 R" % (3 + i) for i in range(leaves)] + [b"%d 0 R" % UNLISTED] * unlisted_refs
    kids = b" ".join(refs)
    for number, body in (
        (1, CATALOG),
        (2, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, leaves)),
    ):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    groups = [list(range(leaves))] if shared_stream else [[i] for i in range(leaves)]
    next_number = 3 + leaves
    for group_index, group in enumerate(groups):
        members = [(3 + i, LEAF) for i in group]
        stream_number = next_number
        next_number += 1
        if group_index == 0:
            members += [(next_number + n, b"0") for n in range(extra_objects)]
            next_number += extra_objects
            if orphan is not None:
                members.insert(0, (ORPHAN, orphan))
        header, body = bytearray(), bytearray()
        for position, (number, obj) in enumerate(members):
            header += b"%d %d " % (number, len(body))
            body += obj + b" "
            if number != ORPHAN:
                compressed[number] = (stream_number, position)
        packed = zlib.compress(bytes(header + body) + b" " * pad, 9)
        offsets[stream_number] = len(out)
        shape = b"/Type /ObjStm /N %d /First %d" % (len(members), len(header))
        out += b"%d 0 obj\n<< %s /Filter /FlateDecode /Length %d >>\nstream\n" % (
            stream_number,
            shape,
            len(packed),
        )
        out += packed + b"\nendstream\nendobj\n"
    xref_number = next_number
    if unlisted_refs:
        compressed[UNLISTED] = (3 + leaves, 0)
    numbers = list(range(xref_number + 1))
    index = b"[0 %d]" % (xref_number + 1)
    if unlisted_refs:
        numbers.append(UNLISTED)
        index = b"[0 %d %d 1]" % (xref_number + 1, UNLISTED)
    rows = bytearray()
    for number in numbers:
        if number == xref_number:
            rows += struct.pack(">BII", 1, len(out), 0)
        elif number in offsets:
            rows += struct.pack(">BII", 1, offsets[number], 0)
        elif number in compressed:
            rows += struct.pack(">BII", 2, *compressed[number])
        else:
            rows += struct.pack(">BII", 0, 0, 65535)
    xref_offset = len(out)
    shape = b"/Type /XRef /Size %d /Index %s /W [1 4 4]" % (
        max(numbers) + 1,
        xref_index if xref_index is not None else index,
    )
    out += b"%d 0 obj\n<< %s /Root 1 0 R /Length %d >>\nstream\n" % (
        xref_number,
        shape,
        len(rows),
    )
    out += rows + b"\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % xref_offset
    return bytes(out)


def _heavy_page(page_index: int, size: int) -> Callable[[PdfWriter], None]:
    """Replace one page's content with ``size`` bytes of no-op operators, flate-packed."""

    def mutate(writer: PdfWriter) -> None:
        stream = DecodedStreamObject()
        stream.set_data(b"q Q\n" * (size // 4 + 1))
        writer.pages[page_index][NameObject("/Contents")] = writer._add_object(
            stream.flate_encode()
        )

    return mutate


CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
LEAF = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"


def _kids(count: int, ref: bytes = b"3 0 R") -> bytes:
    return b"[" + b" ".join([ref] * count) + b"]"


def _action(kind: str, **extra: Any) -> DictionaryObject:
    action = DictionaryObject({NameObject("/S"): NameObject(kind)})
    for key, value in extra.items():
        action[NameObject("/" + key)] = value
    return action


def _javascript() -> DictionaryObject:
    return _action("/JavaScript", JS=TextStringObject("app.alert('synthetic');"))


def _launch() -> DictionaryObject:
    return _action("/Launch", F=TextStringObject("calc.exe"))


def _set_root(key: str, value: Any) -> Callable[[PdfWriter], None]:
    def mutate(writer: PdfWriter) -> None:
        writer.root_object[NameObject(key)] = value

    return mutate


def _encrypt(user_password: str, algorithm: str) -> Callable[[PdfWriter], None]:
    def mutate(writer: PdfWriter) -> None:
        writer.encrypt(
            user_password=user_password, owner_password="synthetic-owner", algorithm=algorithm
        )

    return mutate


def _page_aa(writer: PdfWriter) -> None:
    writer.pages[1][NameObject("/AA")] = DictionaryObject({NameObject("/O"): _javascript()})


def _add_js(writer: PdfWriter) -> None:
    """Document-level JavaScript: a catalog /Names /JavaScript name tree with one script."""
    tree = DictionaryObject(
        {
            NameObject("/Names"): ArrayObject(
                [TextStringObject("synthetic"), writer._add_object(_javascript())]
            )
        }
    )
    writer.root_object[NameObject("/Names")] = DictionaryObject({NameObject("/JavaScript"): tree})


def bad_files() -> list[tuple[str, str, bytes, str]]:
    """(label, file name, bytes, refusal code): one hostile upload per check_pdf refusal."""
    valid = make_pdf()
    return [
        ("wrong extension", "slides.pptx", valid, "training_not_pdf"),
        (
            "exe named pdf",
            "x.pdf",
            b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 120,
            "training_not_pdf",
        ),
        ("truncated", "cut.pdf", valid[: len(valid) // 2], "training_pdf_damaged"),
        (
            "encrypted",
            "locked.pdf",
            make_pdf(mutate=_encrypt("", "RC4-128")),
            "training_pdf_protected",
        ),
        ("301 pages", "long.pdf", make_pdf(MAX_PAGES + 1), "training_pdf_too_many_pages"),
        ("javascript", "script.pdf", make_pdf(mutate=_add_js), "training_pdf_active_content"),
        ("bad name", "a/b.pdf", valid, "training_name_invalid"),
    ]


def _refusal(data: bytes, file_name: str = "deck.pdf") -> str:
    with pytest.raises(PdfRefused) as caught:
        check_pdf(data, file_name)
    return caught.value.code


# --- check_pdf: the good cases -------------------------------------------------------


def test_valid_pdf_reports_pages_and_landscape() -> None:
    assert check_pdf(make_pdf(3, size=WIDESCREEN), "Induction deck.pdf") == PdfInfo(
        page_count=3, landscape=True
    )


def test_portrait_is_not_landscape() -> None:
    assert check_pdf(make_pdf(2, size=PORTRAIT), "handbook.pdf") == PdfInfo(
        page_count=2, landscape=False
    )


def test_rotated_portrait_page_counts_as_landscape() -> None:
    def rotate(writer: PdfWriter) -> None:
        writer.pages[0][NameObject("/Rotate")] = NumberObject(90)

    assert check_pdf(make_pdf(1, size=PORTRAIT, mutate=rotate), "turned.pdf").landscape is True


def test_upper_case_extension_and_exactly_300_pages_accepted() -> None:
    assert check_pdf(make_pdf(MAX_PAGES), "MANUAL.PDF").page_count == MAX_PAGES


def test_open_action_goto_is_accepted() -> None:
    def open_first_page(writer: PdfWriter) -> None:
        writer.open_destination = writer.pages[0]

    assert check_pdf(make_pdf(2, mutate=open_first_page), "deck.pdf").page_count == 2
    goto = _action("/GoTo", D=ArrayObject([NumberObject(0), NameObject("/Fit")]))
    assert check_pdf(make_pdf(1, mutate=_set_root("/OpenAction", goto)), "deck.pdf").page_count == 1


# --- check_pdf: names and content type ----------------------------------------------


@pytest.mark.parametrize("file_name", ["slides.pptx", "deck", "deck.pdf.exe", "deck.pdfx", "pdf"])
def test_wrong_extension_refused(file_name: str) -> None:
    assert _refusal(make_pdf(), file_name) == "training_not_pdf"


@pytest.mark.parametrize(
    "data",
    [
        b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 120,
        b"",
        b"PK\x03\x04 zip",
        b" %PDF-1.7\n",
        b"%PDF",
    ],
    ids=["exe", "empty", "zip", "leading-space", "short-header"],
)
def test_pdf_extension_with_exe_bytes_refused(data: bytes) -> None:
    assert _refusal(data, "x.pdf") == "training_not_pdf"


@pytest.mark.parametrize(
    "file_name",
    [
        "a/b.pdf",
        "a\\b.pdf",
        "a\x00b.pdf",
        "a\nb.pdf",
        "a\tb.pdf",
        "a\x1fb.pdf",
        "a\x7fb.pdf",
        "a\x85b.pdf",
        "invoice\u202efdp.pdf",
        "line\u2028break.pdf",
        "x" * 252 + ".pdf",
        "",
        "   ",
        ".",
        "..",
    ],
    ids=[
        "slash",
        "backslash",
        "nul",
        "newline",
        "tab",
        "unit-separator",
        "delete",
        "c1-control",
        "bidi-override",
        "line-separator",
        "256-chars",
        "empty",
        "blank",
        "dot",
        "dotdot",
    ],
)
def test_name_with_slash_backslash_nul_control_or_over_255_refused(file_name: str) -> None:
    assert _refusal(make_pdf(), file_name) == "training_name_invalid"


@pytest.mark.parametrize(
    "file_name",
    [
        "Latihan keselamatan 2026 (v2).pdf",
        "安全培训 第一部分.pdf",
        "பயிற்சி கையேடு.pdf",
        "Café menu – March.pdf",
        "x" * 251 + ".pdf",
    ],
    ids=["malay", "chinese", "tamil", "accents-dash", "255-chars"],
)
def test_unicode_name_with_spaces_accepted(file_name: str) -> None:
    assert check_pdf(make_pdf(), file_name).page_count == 1


# --- check_pdf: damaged, protected, too long -----------------------------------------


def test_truncated_pdf_is_damaged() -> None:
    valid = make_pdf(3)
    for cut in (len(valid) // 4, len(valid) // 2, len(valid) - 12):
        assert _refusal(valid[:cut]) == "training_pdf_damaged", cut


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-",
        b"%PDF-1.7\n" + bytes(range(256)) * 8,
        raw_pdf([b"<< /Type /Catalog >>"]),
        raw_pdf([CATALOG, b"(not a dictionary)"]),
        raw_pdf([CATALOG, b"<< /Type /Pages /Kids [] /Count 0 >>"]),
        raw_pdf(
            [
                CATALOG,
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Pages /Kids [2 0 R] /Count 1 >>",
            ]
        ),
        raw_pdf([CATALOG, b"<< /Type /Pages /Kids [2 0 R] /Count 1 >>"]),
        raw_pdf([CATALOG, b"<< /Type /Pages /Kids " + _kids(5000) + b" /Count 1 >>", LEAF]),
        raw_pdf(
            [
                CATALOG,
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R >>",
            ]
        ),
    ],
    ids=[
        "header-only",
        "garbage",
        "no-pages",
        "pages-not-dict",
        "zero-pages",
        "cyclic-tree",
        "self-cycle",
        "huge-tree-lying-count",
        "no-mediabox",
    ],
)
def test_garbage_and_hostile_trees_are_damaged(data: bytes) -> None:
    assert _refusal(data) == "training_pdf_damaged"


def test_deep_page_tree_is_damaged() -> None:
    depth = 200
    objects = [CATALOG]
    for level in range(depth):
        objects.append(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % (level + 3))
    objects.append(b"<< /Type /Page /MediaBox [0 0 612 792] >>")
    assert _refusal(raw_pdf(objects)) == "training_pdf_damaged"


@pytest.mark.parametrize(
    ("user_password", "algorithm"),
    [("", "RC4-128"), ("", "AES-256"), ("synthetic-secret", "AES-128")],
    ids=["empty-password-rc4", "empty-password-aes256", "password-aes128"],
)
def test_encrypted_pdf_is_protected(user_password: str, algorithm: str) -> None:
    assert (
        _refusal(make_pdf(2, mutate=_encrypt(user_password, algorithm))) == "training_pdf_protected"
    )


def test_301_pages_refused() -> None:
    assert _refusal(make_pdf(MAX_PAGES + 1)) == "training_pdf_too_many_pages"
    # A /Count of 1 does not hide 301 real leaves; a huge honest /Count is refused before the walk.
    lying = raw_pdf(
        [CATALOG, b"<< /Type /Pages /Kids " + _kids(MAX_PAGES + 1) + b" /Count 1 >>", LEAF]
    )
    assert _refusal(lying) == "training_pdf_too_many_pages"
    honest = raw_pdf([CATALOG, b"<< /Type /Pages /Kids " + _kids(5000) + b" /Count 5000 >>", LEAF])
    assert _refusal(honest) == "training_pdf_too_many_pages"


# --- check_pdf: active content -------------------------------------------------------


def test_javascript_in_names_tree_refused() -> None:
    assert _refusal(make_pdf(mutate=_add_js)) == "training_pdf_active_content"


@pytest.mark.parametrize(
    "action",
    [
        _javascript(),
        _launch(),
        _action("/GoTo", D=ArrayObject([NumberObject(0), NameObject("/Fit")]), Next=_javascript()),
        _action(
            "/GoTo",
            D=ArrayObject([NumberObject(0), NameObject("/Fit")]),
            Next=ArrayObject([_launch()]),
        ),
        _action("/Rendition", JS=TextStringObject("app.alert(1);")),
    ],
    ids=["javascript", "launch", "next-javascript", "next-array-launch", "rendition-js"],
)
def test_open_action_javascript_refused(action: DictionaryObject) -> None:
    assert (
        _refusal(make_pdf(mutate=_set_root("/OpenAction", action))) == "training_pdf_active_content"
    )


def test_page_additional_actions_refused() -> None:
    assert _refusal(make_pdf(3, mutate=_page_aa)) == "training_pdf_active_content"


def test_catalog_additional_actions_refused() -> None:
    aa = DictionaryObject({NameObject("/WC"): _javascript()})
    assert _refusal(make_pdf(mutate=_set_root("/AA", aa))) == "training_pdf_active_content"


def test_bad_files_each_give_their_code() -> None:
    for label, file_name, data, code in bad_files():
        assert _refusal(data, file_name) == code, label


def test_mutated_pdfs_raise_only_refusals() -> None:
    valid = make_pdf(3, size=WIDESCREEN, texts=["one", "two", "three"])
    rng = random.Random(20261106)
    for _ in range(300):
        data = bytearray(valid)
        for _ in range(rng.randint(1, 8)):
            data[rng.randrange(5, len(data))] = rng.randrange(256)
        try:
            check_pdf(bytes(data), "fuzz.pdf")
        except PdfRefused:
            pass


# --- extract_text ----------------------------------------------------------------------


def _page_texts(count: int) -> list[str]:
    return [f"Page {number:03d} " + "safety briefing words " * 4 for number in range(1, count + 1)]


def test_extract_text_stops_at_cap_and_reports_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_pdf(300, texts=_page_texts(300))
    original = PageObject.extract_text
    calls: list[int] = []

    def spy(self: PageObject, *args: Any, **kwargs: Any) -> str:
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", spy)
    text, truncated = extract_text(data, 1000)

    assert truncated is True
    assert len(text) == 1000
    assert text.startswith("Page 001 safety briefing")
    # About 100 characters a page: the cap is reached on page 11, and no later page is read.
    assert 1 <= len(calls) <= 11
    assert "Page 012" not in text


def test_extract_text_whole_file_is_not_truncated() -> None:
    text, truncated = extract_text(make_pdf(3, texts=_page_texts(3)), 24_000)
    assert truncated is False
    assert "Page 001" in text and "Page 002" in text and "Page 003" in text


def test_extract_text_exact_fit_on_last_page_is_not_truncated() -> None:
    data = make_pdf(2, texts=_page_texts(2))
    whole, _ = extract_text(data, 24_000)
    assert extract_text(data, len(whole)) == (whole, False)
    assert extract_text(data, len(whole) - 1) == (whole[:-1], True)


def test_extract_text_scan_returns_empty_string() -> None:
    assert extract_text(make_pdf(4), 24_000) == ("", False)


def test_extract_text_skips_a_page_that_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    data = make_pdf(3, texts=_page_texts(3))
    original = PageObject.extract_text
    calls: list[int] = []

    def flaky(self: PageObject, *args: Any, **kwargs: Any) -> str:
        calls.append(1)
        if len(calls) == 2:
            raise KeyError("/Font")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", flaky)
    text, truncated = extract_text(data, 24_000)
    assert "Page 001" in text and "Page 003" in text and "Page 002" not in text
    assert truncated is False


def test_extract_text_refuses_damaged_and_protected() -> None:
    with pytest.raises(PdfRefused) as damaged:
        extract_text(b"%PDF-1.7\n" + bytes(range(256)) * 8, 24_000)
    assert damaged.value.code == "training_pdf_damaged"
    with pytest.raises(PdfRefused) as protected:
        extract_text(make_pdf(1, texts=["hidden"], mutate=_encrypt("", "AES-256")), 24_000)
    assert protected.value.code == "training_pdf_protected"


# --- decoding limits ---------------------------------------------------------------------


def _damage_cause(data: bytes) -> str:
    with pytest.raises(PdfRefused) as caught:
        check_pdf(data, "deck.pdf")
    assert caught.value.code == "training_pdf_damaged"
    return str(caught.value.__cause__)


def test_leaves_in_object_streams_are_accepted() -> None:
    assert check_pdf(objstm_pdf(3), "deck.pdf") == PdfInfo(page_count=3, landscape=False)


def test_object_stream_padded_past_the_stream_cap_is_damaged() -> None:
    # About 2 KB on disk, more than PDF_STREAM_MAX_DECODED_BYTES once inflated.
    data = objstm_pdf(1, pad=limits.PDF_STREAM_MAX_DECODED_BYTES + 1)
    assert len(data) < 16 * 1024
    assert "Limit reached while decompressing" in _damage_cause(data)


def test_object_streams_under_the_cap_add_up_to_a_file_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(limits, "PDF_OBJECT_STREAMS_MAX_BYTES", 64 * 1024)
    assert check_pdf(objstm_pdf(3, pad=20 * 1024), "deck.pdf").page_count == 3
    assert "stream budget spent" in _damage_cause(objstm_pdf(3, pad=30 * 1024))


def test_objects_parsed_out_of_object_streams_are_budgeted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "PDF_STREAM_OBJECTS_MAX", 20)
    assert check_pdf(objstm_pdf(1, extra_objects=10), "deck.pdf").page_count == 1
    assert "object stream entry budget spent" in _damage_cause(objstm_pdf(1, extra_objects=30))


def test_an_unlisted_object_stream_id_is_charged_on_every_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pypdf never caches an id its stream does not list, so each of the 40 references makes
    # it re-parse the stream's 11 objects. Charged once, 40 x 11 reads would pass a budget
    # of 50.
    monkeypatch.setattr(limits, "PDF_STREAM_OBJECTS_MAX", 50)
    assert check_pdf(objstm_pdf(1, extra_objects=10, unlisted_refs=1), "deck.pdf").page_count == 1
    hostile = objstm_pdf(1, extra_objects=10, unlisted_refs=40)
    assert "object stream entry budget spent" in _damage_cause(hostile)


def test_a_large_uncached_object_is_charged_by_bytes_on_every_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The stream lists a 20 KB array whose number is not in the xref, so pypdf never caches
    # it and re-parses it on every fetch of the unlisted id. /N is only 2, so the object
    # count alone would let all 20 fetches through; the bytes are charged on every read.
    monkeypatch.setattr(limits, "PDF_OBJECT_STREAMS_MAX_BYTES", 64 * 1024)
    orphan = b"[" + b"1 " * 10_000 + b"]"
    assert check_pdf(objstm_pdf(1, orphan=orphan, unlisted_refs=1), "deck.pdf").page_count == 1
    hostile = objstm_pdf(1, orphan=orphan, unlisted_refs=20)
    assert "object and cross-reference stream budget spent" in _damage_cause(hostile)


def test_a_shared_object_stream_is_charged_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # 200 leaves and 2,000 small objects in one stream: the first fetch parses and caches
    # them all, so every later leaf is a cache hit and the budget hook runs once.
    reads: list[int] = []
    original = pdf_module._BudgetedReader._charge_object_stream

    def spy(self: Any, number: int) -> None:
        reads.append(number)
        original(self, number)

    monkeypatch.setattr(pdf_module._BudgetedReader, "_charge_object_stream", spy)
    data = objstm_pdf(200, shared_stream=True, extra_objects=2_000)
    assert check_pdf(data, "deck.pdf").page_count == 200
    assert len(reads) == 1


def test_negative_xref_index_count_is_damaged() -> None:
    # pypdf's clamp and a signed sum both see 0 rows, then pypdf loops a million past the data.
    hostile = objstm_pdf(1, xref_index=b"[0 -1000000 0 1000000]")
    assert "negative cross-reference subsection count" in _damage_cause(hostile)


def test_cross_reference_entries_are_budgeted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "PDF_XREF_MAX_ENTRIES", 5)
    assert "cross-reference entry budget spent" in _damage_cause(objstm_pdf(3))


def test_extract_text_stops_for_the_file_at_a_stream_over_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = _heavy_page(1, limits.PDF_STREAM_MAX_DECODED_BYTES + 1)
    data = make_pdf(3, texts=_page_texts(3), mutate=oversized)
    original = PageObject.extract_text
    calls: list[int] = []

    def spy(self: PageObject, *args: Any, **kwargs: Any) -> str:
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", spy)
    text, truncated = extract_text(data, 24_000)
    # Page 1 is kept; page 2 hits the limit, and page 3 is never decoded or read.
    assert truncated is True
    assert text.startswith("Page 001") and "Page 003" not in text
    assert len(calls) == 1


def test_extract_text_stops_when_the_content_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = make_pdf(3, texts=_page_texts(3))
    whole, _ = extract_text(data, 24_000)
    monkeypatch.setattr(limits, "PDF_TEXT_CONTENT_MAX_BYTES", 150)
    text, truncated = extract_text(data, 24_000)
    assert truncated is True
    assert text and whole.startswith(text) and "Page 003" not in text


def _form_uses(uses: int) -> Callable[[PdfWriter], None]:
    """Page 1 invokes one form XObject (which shows the word "Form") ``uses`` times."""

    def mutate(writer: PdfWriter) -> None:
        page = writer.pages[0]
        resources = page["/Resources"].get_object()
        assert isinstance(resources, DictionaryObject)
        form = DecodedStreamObject()
        form.set_data(b"BT /F1 10 Tf 36 300 Td (Form) Tj ET")
        form[NameObject("/Type")] = NameObject("/XObject")
        form[NameObject("/Subtype")] = NameObject("/Form")
        form[NameObject("/BBox")] = ArrayObject([NumberObject(0), NumberObject(0)] * 2)
        form[NameObject("/Resources")] = resources
        resources[NameObject("/XObject")] = DictionaryObject(
            {NameObject("/Fm0"): writer._add_object(form)}
        )
        content = DecodedStreamObject()
        content.set_data(b"/Fm0 Do\n" * uses)
        page[NameObject("/Contents")] = writer._add_object(content)

    return mutate


def test_extract_text_interprets_a_bounded_number_of_form_uses() -> None:
    cap = limits.PDF_FORM_XOBJECT_USES_PER_PAGE
    text, _ = extract_text(make_pdf(1, texts=["seed"], mutate=_form_uses(cap + 100)), 24_000)
    assert text.count("Form") == cap


def test_pypdf_hooks_still_exist() -> None:
    # _BudgetedReader overrides these private PdfReader methods; an upgrade that renames one
    # would silently drop its budget, so the exact pin and the names are checked together.
    assert pypdf.__version__ == "6.20.1"
    for hook in ("_read_xref_subsections", "_read_pdf15_xref_stream", "_get_object_from_stream"):
        assert hasattr(PdfReader, hook), hook
