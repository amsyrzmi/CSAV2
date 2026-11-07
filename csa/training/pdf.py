"""PDF checks and text extraction for the training library.

The library stores only PDFs. ``check_pdf`` runs on every upload before anything is
stored; ``extract_text`` feeds the summary. Both treat the file as hostile: they read
the catalog and the page tree under tighter pypdf page-tree and stream-decoding limits
(see ``limits``), never parse a content stream to answer a check, and turn every error a
damaged or crafted file can raise into a ``PdfRefused`` code. No database, network or
logging of names or text.
"""

from __future__ import annotations

import io
import struct
import unicodedata
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from pypdf import PageObject, PdfReader, apply_configuration
from pypdf.errors import DependencyError, LimitReachedError, PyPdfError
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    PdfObject,
    StreamObject,
)

from csa.training import limits
from csa.training.limits import MAX_PAGES

PDF_MAGIC = b"%PDF-"
MAX_NAME_CHARS = 255

# A real page tree has fewer nodes than twice its leaves; four times leaves headroom
# for odd producers while keeping a crafted tree's walk small. Depth stays far above
# any balanced tree of MAX_PAGES leaves.
_PAGE_TREE_ENTRIES = 4 * MAX_PAGES
_PAGE_TREE_DEPTH = 32
# Longest /Next chain followed from an /OpenAction before the file counts as damaged.
_MAX_CHAINED_ACTIONS = 64

_ACTIVE_ACTIONS = frozenset({"/JavaScript", "/Launch"})
# Bidirectional controls let a name display differently from what it is ("fdp.exe").
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
# Control characters, lone surrogates and line or paragraph separators.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})

# Everything pypdf (or the stdlib under it) raises on a damaged or crafted file.
_READ_ERRORS: tuple[type[BaseException], ...] = (
    PyPdfError,
    DependencyError,
    NotImplementedError,
    ValueError,
    RecursionError,
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    AssertionError,
    OverflowError,
    ZeroDivisionError,
    zlib.error,
    struct.error,
)


class PdfRefused(Exception):
    """The upload is refused; ``code`` is the API refusal code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PdfInfo:
    page_count: int
    landscape: bool


def check_pdf(data: bytes, file_name: str) -> PdfInfo:
    """Accept a PDF upload or raise ``PdfRefused``; checks run in the spec's order."""
    _check_name(file_name)
    if not file_name.lower().endswith(".pdf") or not data.startswith(PDF_MAGIC):
        raise PdfRefused("training_not_pdf")
    with _parse_limits():
        try:
            reader = _open(data)
            pages = _pages(reader)
            if _has_active_content(reader.root_object, pages):
                raise PdfRefused("training_pdf_active_content")
            landscape = _is_landscape(pages[0])
        except PdfRefused:
            raise
        except _READ_ERRORS as exc:
            raise PdfRefused("training_pdf_damaged") from exc
    return PdfInfo(page_count=len(pages), landscape=landscape)


def extract_text(data: bytes, max_chars: int) -> tuple[str, bool]:
    """The PDF's text, page by page, up to ``max_chars``; ``True`` when it was cut.

    Pages are read in order and reading stops as soon as ``max_chars`` characters are
    gathered. A page whose text cannot be extracted is skipped. A PDF with no text (a
    scan) gives ``("", False)``. Once a decoding limit is hit (one stream too large, or
    the file's content budget spent) no later page is decoded: the text gathered so far
    is returned with ``True``, as if the cap had been reached.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if not data.startswith(PDF_MAGIC):
        raise PdfRefused("training_pdf_damaged")
    with _parse_limits():
        try:
            pages = _pages(_open(data))
        except PdfRefused:
            raise
        except _READ_ERRORS as exc:
            raise PdfRefused("training_pdf_damaged") from exc
        parts: list[str] = []
        gathered = 0
        content_spent = 0
        for index, page in enumerate(pages):
            try:
                content_spent = _charge_content(page, content_spent)
                text = (page.extract_text() or "").strip()
            except LimitReachedError:
                return "".join(parts)[:max_chars], True
            except _READ_ERRORS:
                continue
            if not text:
                continue
            if parts:
                text = "\n\n" + text
            parts.append(text)
            gathered += len(text)
            if gathered > max_chars or (gathered == max_chars and index + 1 < len(pages)):
                return "".join(parts)[:max_chars], True
    return "".join(parts), False


def _check_name(file_name: str) -> None:
    """One display-only segment: no separators, controls or bidi tricks, at most 255 characters."""
    if (
        not file_name.strip()
        or len(file_name) > MAX_NAME_CHARS
        or file_name in {".", ".."}
        or "/" in file_name
        or "\\" in file_name
        or any(
            ch in _BIDI_CONTROLS or unicodedata.category(ch) in _UNSAFE_CATEGORIES
            for ch in file_name
        )
    ):
        raise PdfRefused("training_name_invalid")


@contextmanager
def _parse_limits() -> Iterator[None]:
    """pypdf's page-tree and stream-decoding bounds, scoped to this context (not global)."""
    stream_max = limits.PDF_STREAM_MAX_DECODED_BYTES
    with apply_configuration(
        page_tree_maximum_entries=_PAGE_TREE_ENTRIES,
        page_tree_maximum_depth=_PAGE_TREE_DEPTH,
        zlib_maximum_output_length=stream_max,
        lzw_maximum_output_length=stream_max,
        run_length_maximum_output_length=stream_max,
        brotli_maximum_output_length=stream_max,
        array_based_stream_maximum_output_length=stream_max,
        stream_decoding_work_maximum_length=limits.PDF_STREAM_MAX_DECODING_WORK,
        zlib_maximum_recovery_input_length=limits.PDF_FLATE_MAX_RECOVERY_INPUT,
        # The raw bytes of a stream: bounded by the file itself, so the file cap is enough.
        maximum_declared_stream_length=limits.MAX_FILE_BYTES,
    ):
        yield


class _BudgetedReader(PdfReader):
    """A PdfReader that charges every object and cross-reference stream it decodes to one
    budget per file, so many small-but-legal streams cannot add up to minutes of parsing.

    The per-stream caps in ``_parse_limits`` bound one stream; this bounds their sum, the
    cross-reference rows and the objects parsed out of object streams. It overrides three
    private pypdf 6.20 hooks (pypdf is pinned exactly; recheck them on any upgrade).
    """

    def __init__(self, data: bytes) -> None:
        self._decoded_bytes = 0
        self._xref_entries = 0
        self._stream_objects = 0
        self._charged_streams: set[int] = set()
        super().__init__(io.BytesIO(data), strict=False)

    def _charge(self, amount: int) -> None:
        self._decoded_bytes += amount
        if self._decoded_bytes > limits.PDF_OBJECT_STREAMS_MAX_BYTES:
            raise LimitReachedError("object and cross-reference stream budget spent")

    def _read_xref_subsections(
        self, idx_pairs: list[int], get_entry: Any, used_before: Any
    ) -> None:
        # Each cross-reference stream row costs about 10 µs of Python: count them first.
        self._xref_entries += sum(int(count) for count in idx_pairs[1::2])
        if self._xref_entries > limits.PDF_XREF_MAX_ENTRIES:
            raise LimitReachedError("cross-reference entry budget spent")
        super()._read_xref_subsections(idx_pairs, get_entry, used_before)

    def _read_pdf15_xref_stream(self, stream: Any) -> StreamObject:
        xref_stream = super()._read_pdf15_xref_stream(stream)
        self._charge(len(xref_stream.get_data()))  # decoded already, cached on the stream
        return xref_stream

    def _get_object_from_stream(self, indirect_reference: IndirectObject) -> PdfObject:
        entry = self.xref_objStm.get(indirect_reference.idnum)
        if entry is not None and entry[0] not in self._charged_streams:
            number = int(entry[0])
            self._charged_streams.add(number)
            container = IndirectObject(number, 0, self).get_object()
            if isinstance(container, StreamObject):
                # Charge the raw bytes first (a broken stream costs its retries), then the
                # decoded bytes before pypdf parses them; get_data caches the decoding.
                self._charge(len(container._data))
                decoded = len(container.get_data())
                self._charge(decoded)
                # pypdf parses every object in the stream (about 30 µs each), and caps
                # /N at a third of the decoded length, as this does.
                declared = _resolve(container.get("/N"))
                count = declared if isinstance(declared, int) else 0
                self._stream_objects += max(0, min(count, decoded // 3))
                if self._stream_objects > limits.PDF_STREAM_OBJECTS_MAX:
                    raise LimitReachedError("object stream entry budget spent")
        return super()._get_object_from_stream(indirect_reference)


def _charge_content(page: PageObject, spent: int) -> int:
    """Decode the page's content streams (cached for extract_text) and add them to ``spent``."""
    contents = _resolve(page.get("/Contents"))
    streams = list(contents) if isinstance(contents, ArrayObject) else [contents]
    for item in streams:
        stream = _resolve(item)
        if isinstance(stream, StreamObject):
            spent += len(stream.get_data())
            if spent > limits.PDF_TEXT_CONTENT_MAX_BYTES:
                raise LimitReachedError("page content budget spent")
    return spent


def _open(data: bytes) -> PdfReader:
    reader = _BudgetedReader(data)
    # Any encryption counts as protected, even a file that opens with an empty password.
    if reader.is_encrypted:
        raise PdfRefused("training_pdf_protected")
    return reader


def _pages(reader: PdfReader) -> list[PageObject]:
    """The pages: at least one, at most MAX_PAGES. A huge /Count is refused before the walk."""
    tree = _resolve(reader.root_object.get("/Pages"))
    if isinstance(tree, DictionaryObject):
        declared = _resolve(tree.get("/Count"))
        if isinstance(declared, int) and declared > MAX_PAGES:
            raise PdfRefused("training_pdf_too_many_pages")
    pages = list(reader.pages)
    if len(pages) > MAX_PAGES:
        raise PdfRefused("training_pdf_too_many_pages")
    if not pages:
        raise PdfRefused("training_pdf_damaged")
    return pages


def _has_active_content(root: DictionaryObject, pages: list[PageObject]) -> bool:
    """Script or launch actions: /Names/JavaScript, /OpenAction, catalog /AA, page /AA."""
    names = _resolve(root.get("/Names"))
    if isinstance(names, DictionaryObject) and "/JavaScript" in names:
        return True
    if _is_active_action(root.get("/OpenAction")):
        return True
    if _has_entries(root.get("/AA")):
        return True
    return any(_has_entries(page.get("/AA")) for page in pages)


def _is_active_action(value: object) -> bool:
    """An action (or its /Next chain) that runs script or launches something.

    A destination array (the usual "open at page 1") is not an action and passes.
    """
    pending = [value]
    followed = 0
    while pending:
        action = _resolve(pending.pop())
        if not isinstance(action, DictionaryObject):
            continue
        followed += 1
        if followed > _MAX_CHAINED_ACTIONS:
            raise PdfRefused("training_pdf_damaged")
        if _resolve(action.get("/S")) in _ACTIVE_ACTIONS or "/JS" in action:
            return True
        following = _resolve(action.get("/Next"))
        if isinstance(following, ArrayObject):
            pending.extend(following)
        elif following is not None:
            pending.append(following)
    return False


def _has_entries(value: object) -> bool:
    resolved = _resolve(value)
    return isinstance(resolved, DictionaryObject) and len(resolved) > 0


def _is_landscape(page: PageObject) -> bool:
    """Wider than tall as shown: the crop box, turned by a quarter-turn /Rotate."""
    box = page.cropbox
    width, height = abs(float(box.width)), abs(float(box.height))
    rotation = _resolve(page.get("/Rotate"))
    if isinstance(rotation, int) and rotation % 180 == 90:
        width, height = height, width
    return width > height


def _resolve(value: object) -> object:
    return value.get_object() if isinstance(value, PdfObject) else value
