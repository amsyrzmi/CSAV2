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
