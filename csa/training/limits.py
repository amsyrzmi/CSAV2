"""Size and count limits for the training library."""

from __future__ import annotations

MAX_FILE_BYTES = 25 * 1024 * 1024
"""Largest PDF a single upload may carry."""

MAX_PAGES = 300
"""Most pages one PDF may have."""

MAX_ITEMS = 300
"""Most items one company's library may hold."""

MAX_TOTAL_BYTES = 500 * 1024 * 1024
"""Most bytes of PDFs one company's library may hold."""

SUMMARY_MAX_CHARS = 24_000
"""Most characters of PDF text sent to the model for a summary."""

# Parsing bounds for a hostile PDF. Flate packs about 1000:1, so the file cap says nothing
# about decoded size, and pypdf's own defaults (75 MB a stream) let a small file tie up a
# worker for minutes. pypdf parses decoded bytes in Python at roughly 0.2-4 µs a byte, so
# these budgets are what bound the CPU a file can cost. Images are never decoded here.

PDF_STREAM_MAX_DECODED_BYTES = 2 * 1024 * 1024
"""Most bytes any one stream may decode to (each filter's output, per pypdf filter)."""

PDF_STREAM_MAX_DECODING_WORK = 2 * PDF_STREAM_MAX_DECODED_BYTES
"""Most bytes pypdf may feed through one stream's filter chain in total."""

PDF_FLATE_MAX_RECOVERY_INPUT = 256 * 1024
"""Most bytes of a broken flate stream pypdf may retry byte by byte before giving up."""

PDF_OBJECT_STREAMS_MAX_BYTES = 4 * 1024 * 1024
"""Most bytes of object and cross-reference streams one file may make the reader decode."""

PDF_STREAM_OBJECTS_MAX = 100_000
"""Most objects the reader may parse out of object streams in one file."""

PDF_XREF_MAX_ENTRIES = 250_000
"""Most cross-reference stream entries one file may declare (real decks have a few thousand)."""

PDF_FORM_XOBJECT_USES_PER_PAGE = 32
"""Most form XObject invocations extract_text interprets on one page (pypdf's default is
5,000, and it re-interprets the form's whole stream each time); later ones are skipped."""

PDF_TEXT_CONTENT_MAX_BYTES = 2 * 1024 * 1024
"""Most bytes of page content streams extract_text decodes from one file (text-show
operators cost about 4 µs a byte to interpret; 24,000 characters rarely need 1 MB)."""

# The hard bound. The budgets above cannot cover every path through pypdf (it re-parses on
# cache misses, re-scans the file for each undefined reference, re-runs form XObjects and
# ToUnicode CMaps), so every parse runs in a short-lived child process under these limits.

PDF_CHILD_CPU_SECONDS = 10
"""CPU seconds one parse may use; the child is killed by the kernel past it (RLIMIT_CPU)."""

PDF_CHILD_WALL_SECONDS = 20
"""Wall-clock seconds one parse may take; the child's whole process group is killed past it."""

PDF_CHILD_MEMORY_BYTES = 1024 * 1024 * 1024
"""Address space the child may map (RLIMIT_AS); the worker needs about 64 MiB to run."""

PDF_CHILD_MAX_CONCURRENT = 2
"""Most parse children one process runs at once; further callers wait for a slot."""
