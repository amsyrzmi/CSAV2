"""Ledger-versus-Langfuse reconciliation: ``drift``, ``langfuse_tokens`` and the admin check route.

Langfuse is never called for real: ``httpx.MockTransport`` stands in for its Metrics API v2
(``GET /api/public/v2/metrics``), with synthetic totals. The ledger stays the authority: the check
only reports, so every route test also proves the overview's billed figures do not move.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from csa.billing import langfuse_check as langfuse_check_module
from csa.billing.cycles import KL
from csa.billing.langfuse_check import (
    METRICS_PATH,
    SETTLE,
    LangfuseSource,
    LangfuseTotals,
    drift,
    langfuse_tokens,
)
from csa.billing.memo import USAGE_MEMO
from csa.console.billing import billing_now
from csa.console.billing_admin import langfuse_source
from csa.core.langfuse import pseudonym
from structlog.testing import capture_logs
from structlog.typing import EventDict

from tests.conftest import requires_docker
from tests.test_console_billing import BASE, NOW, OCT, SEP, call, kl, plan
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

CLIENT = uuid.UUID("00000000-0000-4000-8000-00000000c0de")
PUBLIC = "pk-lf-synthetic"
SECRET = "sk-lf-synthetic-secret"
LF_BASE = "https://langfuse.example"
PSEUDONYM_KEY = bytes(range(32))
ADMIN = f"{BASE}/admin"
OVERVIEW = f"{ADMIN}/overview"


# --- drift -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ledger", "langfuse", "expected"),
    [
        ((0, 0), LangfuseTotals(0, 0), "match"),
        ((12_345, 678), LangfuseTotals(12_345, 678), "match"),
        # Small totals: 100 tokens either way is the floor.
        ((1_000, 500), LangfuseTotals(1_100, 400), "match"),
        ((1_000, 500), LangfuseTotals(1_101, 500), "drift"),
        ((1_000, 500), LangfuseTotals(1_000, 399), "drift"),
        ((0, 0), LangfuseTotals(100, 0), "match"),
        ((0, 0), LangfuseTotals(101, 0), "drift"),
        # Large totals: 1% of the ledger figure is the tolerance, Langfuse ahead or behind.
        ((1_000_000, 200_000), LangfuseTotals(990_000, 202_000), "match"),
        ((1_000_000, 200_000), LangfuseTotals(1_010_000, 198_000), "match"),
        ((1_000_000, 200_000), LangfuseTotals(989_999, 200_000), "drift"),
        ((1_000_000, 200_000), LangfuseTotals(1_000_000, 202_001), "drift"),
        # Each direction on its own: equal sums with input and output swapped is drift.
        ((10_000, 2_000), LangfuseTotals(2_000, 10_000), "drift"),
    ],
)
def test_drift_boundaries(ledger: tuple[int, int], langfuse: LangfuseTotals, expected: str) -> None:
    assert drift(ledger, langfuse) == expected


def test_no_langfuse_figure_is_unavailable_never_a_match() -> None:
    assert drift((0, 0), None) == "unavailable"
    assert drift((5, 5), None) == "unavailable"


# --- langfuse_tokens -------------------------------------------------------------------------


def metrics_reply(data: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": data})

    return handler


def recording(
    handler: Callable[[httpx.Request], Any], seen: list[httpx.Request]
) -> httpx.MockTransport:
    def wrapped(request: httpx.Request) -> Any:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped)


async def read(
    handler: Callable[[httpx.Request], Any],
    *,
    base_url: str = LF_BASE + "/",
    public_key: str | None = PUBLIC,
    secret_key: str | None = SECRET,
    pseudonym_key: bytes | None = PSEUDONYM_KEY,
    start: date = OCT.start,
    end: date = OCT.end,
    until: datetime | None = None,
) -> tuple[LangfuseTotals | None, list[httpx.Request], list[EventDict]]:
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=recording(handler, seen)) as http:
        with capture_logs() as logs:
            totals = await langfuse_tokens(
                http,
                base_url=base_url,
                public_key=public_key,
                secret_key=secret_key,
                pseudonym_key=pseudonym_key,
                client_id=CLIENT,
                start=start,
                end=end,
                until=until,
            )
    return totals, seen, logs


def query_of(request: httpx.Request) -> dict[str, Any]:
    [raw] = parse_qs(urlsplit(str(request.url)).query)["query"]
    return json.loads(raw)


@pytest.mark.asyncio
async def test_reads_the_metrics_v2_api_with_basic_auth_client_filter_and_window() -> None:
    totals, seen, logs = await read(
        metrics_reply([{"sum_inputTokens": 1_234_567, "sum_outputTokens": 7_654}])
    )
    assert totals == LangfuseTotals(input_tokens=1_234_567, output_tokens=7_654)
    [request] = seen
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "langfuse.example"
    assert request.url.path == METRICS_PATH == "/api/public/v2/metrics"
    expected_auth = base64.b64encode(f"{PUBLIC}:{SECRET}".encode()).decode()
    assert request.headers["Authorization"] == f"Basic {expected_auth}"
    query = query_of(request)
    assert query["view"] == "observations"
    assert query["metrics"] == [
        {"measure": "inputTokens", "aggregation": "sum"},
        {"measure": "outputTokens", "aggregation": "sum"},
    ]
    # The sink's pseudonym of the client id, never the raw id (owner 2026-11-13).
    assert query["filters"] == [
        {
            "column": "userId",
            "operator": "=",
            "value": pseudonym("client", CLIENT, PSEUDONYM_KEY),
            "type": "string",
        }
    ]
    for spelling in (str(CLIENT), CLIENT.hex):
        assert spelling not in str(request.url).lower()
    assert query.get("dimensions", []) == []
    assert "timeDimension" not in query
    # 5 Oct 00:00 and 5 Nov 00:00 in Kuala Lumpur (UTC+8), as UTC.
    assert query["fromTimestamp"] == "2026-10-04T16:00:00.000Z"
    assert query["toTimestamp"] == "2026-11-04T16:00:00.000Z"
    assert logs == []
    assert SECRET not in str(request.url)


@pytest.mark.asyncio
async def test_the_window_stops_at_until_when_the_cycle_is_still_open() -> None:
    until = datetime(2026, 10, 20, 11, 55, 0, 123456, tzinfo=KL)
    _, [request], _ = await read(
        metrics_reply([{"sum_inputTokens": 0, "sum_outputTokens": 0}]), until=until
    )
    query = query_of(request)
    assert query["fromTimestamp"] == "2026-10-04T16:00:00.000Z"
    assert query["toTimestamp"] == "2026-10-20T03:55:00.123Z"
    # A closed cycle is never stretched past its end.
    _, [later], _ = await read(
        metrics_reply([{"sum_inputTokens": 0, "sum_outputTokens": 0}]),
        start=SEP.start,
        end=SEP.end,
        until=until,
    )
    assert query_of(later)["toTimestamp"] == "2026-10-04T16:00:00.000Z"


@pytest.mark.asyncio
async def test_an_empty_window_is_zero_without_a_request() -> None:
    totals, seen, _ = await read(metrics_reply([]), until=datetime(2026, 10, 5, 0, 0, tzinfo=KL))
    assert (totals, seen) == (LangfuseTotals(0, 0), [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        [],  # nothing matched the filter
        [{"sum_inputTokens": None, "sum_outputTokens": None}],
        [{"sum_inputTokens": 0, "sum_outputTokens": 0}],
    ],
)
async def test_no_usage_in_langfuse_reads_as_zero(data: list[dict[str, Any]]) -> None:
    totals, _, logs = await read(metrics_reply(data))
    assert totals == LangfuseTotals(0, 0)
    assert logs == []


@pytest.mark.asyncio
async def test_totals_sent_as_digit_strings_or_whole_floats_are_read_exactly() -> None:
    totals, _, _ = await read(
        metrics_reply([{"sum_inputTokens": "9007199254740993", "sum_outputTokens": 12.0}])
    )
    assert totals == LangfuseTotals(9_007_199_254_740_993, 12)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_an_error_status_is_none_and_logs_only_the_status(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "synthetic failure"})

    totals, seen, logs = await read(handler)
    assert totals is None
    assert len(seen) == 1
    assert logs == [{"event": "langfuse_check_failed", "log_level": "error", "status": status}]


@pytest.mark.asyncio
async def test_an_unreachable_langfuse_is_none_and_logs_only_the_error_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic: connection refused", request=request)

    totals, _, logs = await read(handler)
    assert totals is None
    assert logs == [
        {"event": "langfuse_check_unreachable", "log_level": "error", "error_type": "ConnectError"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"rows": []}',
        b'{"data": {"sum_inputTokens": 1}}',
        b'{"data": [{"sum_inputTokens": -1, "sum_outputTokens": 0}]}',
        b'{"data": [{"sum_inputTokens": 1.5, "sum_outputTokens": 0}]}',
        b'{"data": [{"sum_inputTokens": "12a", "sum_outputTokens": 0}]}',
        b'{"data": [{"sum_inputTokens": true, "sum_outputTokens": 0}]}',
        b'{"data": [{"sum_outputTokens": 0}]}',
        b'{"data": [{"sum_inputTokens": 1, "sum_outputTokens": 0}, {"sum_inputTokens": 1}]}',
    ],
)
async def test_an_unexpected_reply_is_none_never_a_guess(body: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    totals, _, logs = await read(handler)
    assert totals is None
    assert logs == [{"event": "langfuse_check_bad_reply", "log_level": "error"}]


@pytest.mark.asyncio
async def test_an_over_long_digit_string_is_a_bad_reply_not_an_error() -> None:
    # Python refuses int() of a string over 4,300 digits; the reply is off-shape, never a 500.
    totals, _, logs = await read(
        metrics_reply([{"sum_inputTokens": "9" * 5_000, "sum_outputTokens": 0}])
    )
    assert totals is None
    assert logs == [{"event": "langfuse_check_bad_reply", "log_level": "error"}]
    # Nineteen digits is past any real token total, and also refused.
    totals, _, _ = await read(metrics_reply([{"sum_inputTokens": "1" * 19, "sum_outputTokens": 0}]))
    assert totals is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "error_type"),
    [("https://[::1", "InvalidURL"), ("https://lang\x00fuse.example", "InvalidURL")],
)
async def test_a_malformed_base_url_is_none_and_logs_only_the_error_type(
    base_url: str, error_type: str
) -> None:
    totals, seen, logs = await read(
        metrics_reply([{"sum_inputTokens": 1, "sum_outputTokens": 1}]),
        base_url=base_url,
    )
    assert (totals, seen) == (None, [])
    assert logs == [
        {"event": "langfuse_check_unreachable", "log_level": "error", "error_type": error_type}
    ]


@pytest.mark.asyncio
async def test_a_stalled_langfuse_is_cut_off_and_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(langfuse_check_module, "REQUEST_DEADLINE_S", 0.01)

    async def stall(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        return httpx.Response(200, json={"data": []})

    async with asyncio.timeout(5):  # the test fails rather than hangs if unbounded
        totals, seen, logs = await read(stall)
    assert totals is None
    assert len(seen) == 1
    assert logs == [
        {"event": "langfuse_check_unreachable", "log_level": "error", "error_type": "TimeoutError"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("public_key", "secret_key"), [(None, SECRET), (PUBLIC, None), ("", SECRET), (PUBLIC, " ")]
)
async def test_unconfigured_keys_are_none_without_a_request(
    public_key: str | None, secret_key: str | None
) -> None:
    totals, seen, logs = await read(
        metrics_reply([{"sum_inputTokens": 1, "sum_outputTokens": 1}]),
        public_key=public_key,
        secret_key=secret_key,
    )
    assert (totals, seen) == (None, [])
    assert logs == [{"event": "langfuse_check_unconfigured", "log_level": "error"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("pseudonym_key", [None, b"", b"short-key"])
async def test_a_missing_pseudonym_key_is_none_without_a_request(
    pseudonym_key: bytes | None,
) -> None:
    totals, seen, logs = await read(
        metrics_reply([{"sum_inputTokens": 1, "sum_outputTokens": 1}]),
        pseudonym_key=pseudonym_key,
    )
    assert (totals, seen) == (None, [])
    assert logs == [{"event": "langfuse_check_unconfigured", "log_level": "error"}]


# --- the admin route -------------------------------------------------------------------------


def check_path(client_id: str) -> str:
    return f"{ADMIN}/clients/{client_id}/langfuse-check"


class FakeLangfuse:
    """A Langfuse stand-in for the route: answers with ``totals``, or ``status`` when set."""

    def __init__(self) -> None:
        self.totals: tuple[int, int] = (0, 0)
        self.status: int | None = None
        self.requests: list[httpx.Request] = []
        self.public_key: str | None = PUBLIC
        self.pseudonym_key: bytes | None = PSEUDONYM_KEY
        self.base_url: str = LF_BASE

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx.Response(self.status)
        tokens_in, tokens_out = self.totals
        return httpx.Response(
            200, json={"data": [{"sum_inputTokens": tokens_in, "sum_outputTokens": tokens_out}]}
        )


@pytest.fixture
def fake_langfuse(sessions: SessionWorld) -> Iterator[FakeLangfuse]:
    fake = FakeLangfuse()

    async def source() -> AsyncIterator[LangfuseSource]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http:
            yield LangfuseSource(
                http=http,
                base_url=fake.base_url,
                public_key=fake.public_key,
                secret_key=SECRET,
                pseudonym_key=fake.pseudonym_key,
            )

    sessions.app.dependency_overrides[billing_now] = lambda: NOW
    sessions.app.dependency_overrides[langfuse_source] = source
    USAGE_MEMO.clear()
    yield fake
    sessions.app.dependency_overrides.pop(langfuse_source, None)
    USAGE_MEMO.clear()


async def ledger_world(w: SessionWorld) -> str:
    """A company with billed usage, usage after its termination, and usage outside the cycle."""
    tenant = w.tenants[0]
    await plan(w, tenant, SEP.start, "pilot", "individual")
    await call(w, tenant, kl(9, 20), prompt=999, completion=999)  # previous cycle
    await call(w, tenant, kl(10, 6, 9), prompt=1_234_567, completion=7_654)
    await call(w, tenant, kl(10, 8), job="email_reply", prompt=2_000_000, completion=1)
    return tenant


@requires_docker
@pytest.mark.asyncio
async def test_the_check_matches_when_langfuse_agrees_with_the_whole_cycle_ledger(
    sessions: SessionWorld, fake_langfuse: FakeLangfuse
) -> None:
    w = sessions
    tenant = await ledger_world(w)
    # Usage after a termination is not billed, but it was spent and exported: it is compared.
    await plan(w, tenant, date(2026, 10, 10), "terminated", "individual")
    await call(w, tenant, kl(10, 12), prompt=10, completion=20)
    # Too recent to have reached Langfuse: left out of both sides.
    await call(w, tenant, NOW - SETTLE / 2, prompt=50_000, completion=50_000)
    fake_langfuse.totals = (1_234_567 + 2_000_000 + 10, 7_654 + 1 + 20)
    response = await w.admin.get(check_path(tenant))
    assert response.status_code == 200, response.text
    assert response.json() == {
        "client_id": tenant,
        "cycle_start": "2026-10-05",
        "cycle_end": "2026-11-05",
        "checked_until": (NOW - SETTLE).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "result": "match",
        "ledger_input_tokens": 1_234_567 + 2_000_000 + 10,
        "ledger_output_tokens": 7_654 + 1 + 20,
        "langfuse_input_tokens": 1_234_567 + 2_000_000 + 10,
        "langfuse_output_tokens": 7_654 + 1 + 20,
        "warnings": [],
    }
    [request] = fake_langfuse.requests
    query = query_of(request)
    assert query["filters"][0]["value"] == pseudonym("client", uuid.UUID(tenant), PSEUDONYM_KEY)
    assert tenant not in str(request.url)
    assert query["toTimestamp"] == "2026-10-20T03:55:00.000Z"


@requires_docker
@pytest.mark.asyncio
async def test_drift_adds_the_langfuse_drift_warning_and_a_past_cycle_can_be_checked(
    sessions: SessionWorld, fake_langfuse: FakeLangfuse
) -> None:
    w = sessions
    tenant = await ledger_world(w)
    fake_langfuse.totals = (1_234_567, 7_654)  # the email reply never arrived
    body = (await w.admin.get(check_path(tenant))).json()
    assert (body["result"], body["warnings"]) == ("drift", ["langfuse_drift"])
    assert (body["ledger_input_tokens"], body["langfuse_input_tokens"]) == (3_234_567, 1_234_567)
    fake_langfuse.totals = (999, 999)
    past = (await w.admin.get(f"{check_path(tenant)}?cycle=2026-09-05")).json()
    assert (past["cycle_start"], past["result"], past["ledger_input_tokens"]) == (
        "2026-09-05",
        "match",
        999,
    )
    assert past["checked_until"] == "2026-10-04T16:00:00Z"


@requires_docker
@pytest.mark.asyncio
@pytest.mark.parametrize("down", ["status", "unconfigured", "no_pseudonym_key", "malformed_url"])
async def test_langfuse_down_is_unavailable_and_the_billed_figures_do_not_move(
    sessions: SessionWorld, fake_langfuse: FakeLangfuse, down: str
) -> None:
    w = sessions
    tenant = await ledger_world(w)
    before = await w.admin.get(OVERVIEW)
    assert before.status_code == 200
    if down == "status":
        fake_langfuse.status = 500
    elif down == "malformed_url":
        fake_langfuse.base_url = "https://[::1"
    elif down == "no_pseudonym_key":
        fake_langfuse.pseudonym_key = None
    else:
        fake_langfuse.public_key = None
    response = await w.admin.get(check_path(tenant))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "unavailable"
    assert body["warnings"] == []
    assert (body["langfuse_input_tokens"], body["langfuse_output_tokens"]) == (None, None)
    assert (body["ledger_input_tokens"], body["ledger_output_tokens"]) == (3_234_567, 7_655)
    assert len(fake_langfuse.requests) == (1 if down == "status" else 0)
    after = await w.admin.get(OVERVIEW)
    assert after.json() == before.json()
    [row] = [c for c in after.json()["companies"] if c["client_id"] == tenant]
    assert "langfuse_drift" not in row["warnings"]


@requires_docker
@pytest.mark.asyncio
async def test_the_overview_never_calls_langfuse(
    sessions: SessionWorld, fake_langfuse: FakeLangfuse
) -> None:
    await ledger_world(sessions)
    assert (await sessions.admin.get(OVERVIEW)).status_code == 200
    assert fake_langfuse.requests == []


@requires_docker
@pytest.mark.asyncio
async def test_the_check_is_for_platform_admins_only(
    sessions: SessionWorld, fake_langfuse: FakeLangfuse
) -> None:
    w = sessions
    tenant = w.tenants[0]
    for role in ("tenant_admin", "tenant_staff", "read_only"):
        member, _ = await w.member(role=role, tenant=tenant)
        refused = await member.get(check_path(tenant))
        assert refused.status_code == 403, (role, refused.text)
        assert refused.json()["detail"] == "platform_admin_only"
    anonymous = await w.browser()
    assert (await anonymous.get(check_path(tenant))).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url=ORIGIN
    ) as machine:
        headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": tenant}
        assert (await machine.get(check_path(tenant), headers=headers)).status_code == 403
    assert fake_langfuse.requests == []


@requires_docker
@pytest.mark.asyncio
async def test_the_check_refuses_an_unknown_company_and_a_bad_cycle(
    sessions: SessionWorld, fake_langfuse: FakeLangfuse
) -> None:
    w = sessions
    missing = await w.admin.get(check_path(str(uuid.uuid4())))
    assert (missing.status_code, missing.json()["detail"]) == (404, "tenant not found")
    tenant = w.tenants[0]
    off = await w.admin.get(f"{check_path(tenant)}?cycle=2026-10-06")
    assert (off.status_code, off.json()["detail"]) == (422, "cycle_must_start_on_a_fifth")
    future = await w.admin.get(f"{check_path(tenant)}?cycle=2026-12-05")
    assert (future.status_code, future.json()["detail"]) == (404, "cycle_not_found")
    assert fake_langfuse.requests == []
