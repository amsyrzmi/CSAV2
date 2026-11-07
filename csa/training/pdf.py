"""PDF checks and text extraction for the training library.

The library stores only PDFs. ``check_pdf`` runs on every upload before anything is
stored; ``extract_text`` feeds the summary. Both treat the file as hostile: they read
the catalog and the page tree under tighter pypdf page-tree limits, never parse a
content stream to answer a check, and turn every error a damaged or crafted file can
raise into a ``PdfRefused`` code. No database, network or logging of names or text.
"""

from __future__ import annotations

import io
import struct
import unicodedata
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pypdf import PageObject, PdfReader, apply_configuration
from pypdf.errors import DependencyError, PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, PdfObject

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
_BIDI_CONTROLS = frozenset("؜‎‏‪‫‬‭‮⁦⁧⁨⁩")
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
    with _page_tree_limits():
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
    scan) gives ``("", False)``.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if not data.startswith(PDF_MAGIC):
        raise PdfRefused("training_pdf_damaged")
    with _page_tree_limits():
        try:
            pages = _pages(_open(data))
        except PdfRefused:
            raise
        except _READ_ERRORS as exc:
            raise PdfRefused("training_pdf_damaged") from exc
        parts: list[str] = []
        gathered = 0
        for index, page in enumerate(pages):
            try:
                text = (page.extract_text() or "").strip()
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
def _page_tree_limits() -> Iterator[None]:
    """pypdf walks the page tree with these bounds (scoped to this context, not global)."""
    with apply_configuration(
        page_tree_maximum_entries=_PAGE_TREE_ENTRIES,
        page_tree_maximum_depth=_PAGE_TREE_DEPTH,
    ):
        yield


def _open(data: bytes) -> PdfReader:
    reader = PdfReader(io.BytesIO(data), strict=False)
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
