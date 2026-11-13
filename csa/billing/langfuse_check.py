"""Reconcile the usage ledger with Langfuse: token totals for one company over one cycle.

The ledger (``ai_usage_events``) is the authority. This module only reports whether Langfuse,
the best-effort mirror, saw the same tokens; it never changes a billed figure.

Langfuse side: the public Metrics API v2, ``GET /api/public/v2/metrics?query=<JSON>`` with Basic
auth (public key as username, secret key as password), on the ``observations`` view, summing the
``inputTokens`` and ``outputTokens`` measures, filtered by ``userId`` (the sink sets ``user.id`` to
the client's keyed pseudonym, ``csa.core.langfuse.pseudonym``, on every span; never the raw id,
owner 2026-11-13) between ``fromTimestamp`` and ``toTimestamp``. With no dimensions the
reply is ``{"data": [{"sum_inputTokens": n, "sum_outputTokens": n}]}``. Sources (read
2026-11-13): https://langfuse.com/docs/metrics/features/metrics-api and the public OpenAPI spec
(https://cloud.langfuse.com/generated/api/openapi.yml, ``/api/public/v2/metrics``).

Langfuse documents v2 metrics plus OTLP ingestion (with ``x-langfuse-ingestion-version: 4``, which
the sink sends) as its one real-time read path, but our exporter batches spans for a few seconds,
so the caller leaves the last ``SETTLE`` out of both sides. v2 needs Langfuse Cloud or a
self-hosted Langfuse v4 or later. On Langfuse Cloud the Metrics API is rate limited per
organisation (Hobby: 100 requests a day; Core: 100 an hour; Pro and up: 500 an hour; HTTP 429),
which is why the check is asked for one company at a time and never on an overview load.

``langfuse_tokens`` answers ``None`` (and logs an error) when the keys or the pseudonym key are
unset, the base URL is
malformed, Langfuse cannot be reached or does not answer within ``REQUEST_DEADLINE_S``, it answers
an error status, or the reply is not the documented shape: never a guess.
Logs carry only the event, the HTTP status or the exception type; never keys, URLs or bodies.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

import httpx
import structlog

from csa.billing.cycles import KL
from csa.core.langfuse import pseudonym, usable_pseudonym_key

__all__ = [
    "ABS_TOLERANCE",
    "DEFAULT_BASE_URL",
    "METRICS_PATH",
    "REQUEST_DEADLINE_S",
    "SETTLE",
    "TIMEOUT",
    "CheckResult",
    "LangfuseSource",
    "LangfuseTotals",
    "check_window",
    "drift",
    "langfuse_tokens",
]

logger = structlog.get_logger(__name__)

METRICS_PATH = "/api/public/v2/metrics"
# The sink's own default when LANGFUSE_BASE_URL is unset (apps/worker, apps/gateway).
DEFAULT_BASE_URL = "https://cloud.langfuse.com"
TIMEOUT = httpx.Timeout(10.0)
# The whole request, end to end: ``TIMEOUT`` bounds each phase, so a server that trickles its
# reply could otherwise hold the admin page far longer.
REQUEST_DEADLINE_S: float = 15.0
# Usage this recent may still sit in the exporter's batch queue: both sides leave it out.
SETTLE = timedelta(minutes=5)
# ``match`` within 1% of the ledger figure or this many tokens, whichever is larger, per direction.
ABS_TOLERANCE = 100
# Eighteen digits is far past any real token total and well inside what ``int`` will parse.
_DIGITS = re.compile(r"[0-9]{1,18}")
_MEASURES = ("inputTokens", "outputTokens")

CheckResult = Literal["match", "drift", "unavailable"]


@dataclass(frozen=True)
class LangfuseTotals:
    """Input and output tokens Langfuse holds for one company over one window."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LangfuseSource:
    """Where to read from: an HTTP client, the sink's credentials and its pseudonym key (``None``
    when unset)."""

    http: httpx.AsyncClient
    base_url: str
    public_key: str | None
    secret_key: str | None
    pseudonym_key: bytes | None


def _midnight(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=KL)


def check_window(start: date, end: date, until: datetime | None) -> tuple[datetime, datetime]:
    """``[start 00:00, end 00:00)`` in Kuala Lumpur, cut at ``until`` (aware) when it is earlier.

    ``until`` is truncated to the millisecond (the precision Langfuse is queried at), so the
    ledger and Langfuse are asked about the same instants. The window may be empty.
    """
    begin, finish = _midnight(start), _midnight(end)
    if until is not None:
        if until.tzinfo is None or until.utcoffset() is None:
            raise ValueError("until must carry a timezone")
        cut = until.replace(microsecond=until.microsecond // 1000 * 1000)
        finish = min(finish, cut)
    return begin, finish


def _iso_ms(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def _count(value: Any) -> int | None:
    """A token total from the reply: a whole number (a null sum is zero), else ``None``."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value >= 0 else None
    if isinstance(value, str) and _DIGITS.fullmatch(value):
        return int(value)
    return None


def _totals(body: Any) -> LangfuseTotals | None:
    """The documented reply shape, or ``None``: no rows means nothing matched (zero)."""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return None
    rows = body["data"]
    if not rows:
        return LangfuseTotals(0, 0)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    keys = [f"sum_{m}" for m in _MEASURES]
    if any(k not in row for k in keys):
        return None
    tokens_in, tokens_out = (_count(row[k]) for k in keys)
    if tokens_in is None or tokens_out is None:
        return None
    return LangfuseTotals(tokens_in, tokens_out)


def _query(user_id: str, begin: datetime, finish: datetime) -> str:
    return json.dumps(
        {
            "view": "observations",
            "dimensions": [],
            "metrics": [{"measure": m, "aggregation": "sum"} for m in _MEASURES],
            "filters": [{"column": "userId", "operator": "=", "value": user_id, "type": "string"}],
            "fromTimestamp": _iso_ms(begin),
            "toTimestamp": _iso_ms(finish),
        },
        separators=(",", ":"),
    )


async def langfuse_tokens(
    http: httpx.AsyncClient,
    *,
    base_url: str,
    public_key: str | None,
    secret_key: str | None,
    pseudonym_key: bytes | None,
    client_id: UUID,
    start: date,
    end: date,
    until: datetime | None = None,
) -> LangfuseTotals | None:
    """Langfuse's input and output token totals for ``client_id`` over ``check_window``.

    ``start`` and ``end`` are Kuala Lumpur dates (``end`` exclusive), queried as UTC instants.
    Langfuse is asked about the client's pseudonym, the same one the sink sends. An empty window
    is zero without a request. ``None`` (with an error log) when either key is unset or blank,
    the pseudonym key is missing or unusable, the base URL is malformed, Langfuse is unreachable
    or too slow, answers an error status, or replies off-shape.
    """
    if not (public_key and public_key.strip() and secret_key and secret_key.strip()):
        logger.error("langfuse_check_unconfigured")
        return None
    if pseudonym_key is None or not usable_pseudonym_key(
        pseudonym_key, forbidden=(secret_key.encode(), public_key.encode())
    ):
        logger.error("langfuse_check_unconfigured")
        return None
    begin, finish = check_window(start, end, until)
    if finish <= begin:
        return LangfuseTotals(0, 0)
    try:
        async with asyncio.timeout(REQUEST_DEADLINE_S):
            response = await http.get(
                f"{base_url.rstrip('/')}{METRICS_PATH}",
                params={
                    "query": _query(pseudonym("client", client_id, pseudonym_key), begin, finish)
                },
                auth=httpx.BasicAuth(public_key, secret_key),
                timeout=TIMEOUT,
            )
    # A malformed LANGFUSE_BASE_URL raises InvalidURL (not an HTTPError) on httpx 0.28; a missing
    # scheme is UnsupportedProtocol, an HTTPError.
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError) as exc:
        logger.error("langfuse_check_unreachable", error_type=type(exc).__name__)
        return None
    if response.status_code != 200:
        logger.error("langfuse_check_failed", status=response.status_code)
        return None
    try:
        body = response.json()
    except ValueError:
        body = None
    totals = _totals(body)
    if totals is None:
        logger.error("langfuse_check_bad_reply")
    return totals


def _within(ledger: int, other: int) -> bool:
    gap = abs(ledger - other)
    return gap <= ABS_TOLERANCE or gap * 100 <= ledger


def drift(ledger: tuple[int, int], langfuse: LangfuseTotals | None) -> CheckResult:
    """``match`` when input and output each agree within 1% of the ledger or 100 tokens
    (whichever is larger: export is batched and best-effort), ``drift`` otherwise, and
    ``unavailable`` when there is no Langfuse figure. Integer arithmetic only.
    """
    if langfuse is None:
        return "unavailable"
    tokens_in, tokens_out = ledger
    if _within(tokens_in, langfuse.input_tokens) and _within(tokens_out, langfuse.output_tokens):
        return "match"
    return "drift"
