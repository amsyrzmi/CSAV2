"""Identifier helpers (LG01 skeleton).

A trace/thread id is a canonical lowercase UUID string. Fleshed out as later
phases need deterministic id derivation.
"""  # LG01

from __future__ import annotations

import uuid


def new_trace_id() -> str:
    """Return a fresh trace id as a canonical lowercase UUID string."""
    return str(uuid.uuid4())
