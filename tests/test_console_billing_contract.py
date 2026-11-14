"""What a client can see of billing: prices only, never raw cost, tokens, markup or margin.

The schema half needs no database: every client response model, and the OpenAPI document the web
console is built from, is walked for a forbidden property name. The payload half seeds usage with
distinctive token counts and checks the real responses and the logs written while serving them.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, Literal

import pytest
import structlog
from csa.billing.memo import USAGE_MEMO
from csa.billing.service import cycle_usage
from csa.console.billing import (
    AgentUsageOut,
    CyclePickOut,
    DailyUsageOut,
    UsageOut,
    billing_now,
)
from csa.core.tenancy import tx
from pydantic import BaseModel
from scripts.export_console_openapi import contract

from tests.conftest import requires_docker
from tests.test_console_billing import (
    BASE,
    NOW,
    OCT,
    UNPRICED,
    admin_of,
    call,
    kl,
    plan,
)
from tests.test_console_sessions import SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

FORBIDDEN = re.compile(r"token|raw|cost|markup|margin|usd|aws", re.IGNORECASE)
CLIENT_MODELS: tuple[type[BaseModel], ...] = (
    UsageOut,
    AgentUsageOut,
    DailyUsageOut,
    CyclePickOut,
)
# Token counts no other figure in these tests can produce, so a match is a leak.
PROMPT, COMPLETION = 1_234_567, 76_543


def property_names(schema: Any) -> Iterator[str]:
    """Every key under any ``properties`` object, at any depth (``$defs`` included)."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                yield from value
            yield from property_names(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from property_names(item)


def json_keys(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from json_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from json_keys(item)


# --- schemas ---------------------------------------------------------------------------------


@pytest.mark.parametrize("model", CLIENT_MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_client_models_have_no_forbidden_property(
    model: type[BaseModel], mode: Literal["validation", "serialization"]
) -> None:
    names = list(property_names(model.model_json_schema(mode=mode)))
    assert names, f"{model.__name__} has no properties to check"
    assert [n for n in names if FORBIDDEN.search(n)] == []


def test_usage_out_fields_are_pinned() -> None:
    """A new client field is a deliberate contract change: list it here after the review."""
    assert list(UsageOut.model_fields) == [
        "cycle_start",
        "cycle_end",
        "billed_from",
        "billed_to",
        "executions",
        "ai_usage_price",
        "platform_fee",
        "total",
        "final",
        "status",
        "by_agent",
        "daily",
        "terminated_on",
    ]


@pytest.mark.parametrize("model", CLIENT_MODELS, ids=lambda m: m.__name__)
def test_client_models_forbid_extra_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid"


def test_the_published_billing_routes_carry_no_forbidden_property() -> None:
    document = contract()
    routes = {p: v for p, v in document["paths"].items() if p.startswith(BASE + "/usage")}
    # Owner decision 2026-11-13: no per-execution list for the client.
    assert set(routes) == {f"{BASE}/usage", f"{BASE}/usage/cycles"}
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref not in seen:
                seen.add(ref)
                target: Any = document
                for part in ref.removeprefix("#/").split("/"):
                    target = target[part]
                walk(target)
            for key, item in value.items():
                if key == "properties" and isinstance(item, dict):
                    assert [n for n in item if FORBIDDEN.search(n)] == [], item
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for route in routes.values():
        walk(route["get"]["responses"]["200"])
        for parameter in route["get"].get("parameters", []):
            assert not FORBIDDEN.search(parameter["name"])
    assert any(ref.endswith("/UsageOut") for ref in seen)


# --- payloads and logs -----------------------------------------------------------------------


@pytest.fixture
def pinned_clock(sessions: SessionWorld) -> Iterator[None]:
    sessions.app.dependency_overrides[billing_now] = lambda: NOW
    USAGE_MEMO.clear()
    yield
    USAGE_MEMO.clear()


@requires_docker
@pytest.mark.asyncio
async def test_payloads_and_logs_carry_prices_only(
    sessions: SessionWorld, pinned_clock: None, caplog: pytest.LogCaptureFixture
) -> None:
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, OCT.start)
    await call(w, tenant, kl(10, 6), prompt=PROMPT, completion=COMPLETION)
    await call(w, tenant, kl(10, 7), job="email_analysis", prompt=PROMPT, completion=COMPLETION)
    await call(
        w,
        tenant,
        kl(10, 8),
        job="hr_assistant",
        provider="synthetic",
        model=UNPRICED,
        prompt=PROMPT,
        completion=COMPLETION,
    )
    async with tx(w.pool, tenant) as conn:
        internal = await cycle_usage(conn, uuid.UUID(tenant), OCT, now=NOW)
    assert internal.markup is not None and internal.status == "pending_prices"
    secrets = {
        str(PROMPT),
        str(COMPLETION),
        str(internal.prompt_tokens),
        str(internal.completion_tokens),
        str(internal.raw_cost_rm),
        f"{internal.raw_cost_rm:.4f}",
        "openai/gpt-oss-120b",
        "groq",
        UNPRICED,
    }
    secrets |= {str(p.exact_price) for p in internal.priced}
    secrets |= {str(e.raw_rm) for e in internal.executions if e.raw_rm is not None}
    admin = await admin_of(w, tenant)
    caplog.set_level(logging.DEBUG)
    caplog.clear()  # only what serving the billing requests logs
    with structlog.testing.capture_logs() as structured:
        responses = [
            await admin.get(f"{BASE}/usage"),
            await admin.get(f"{BASE}/usage?cycle=2026-10-05"),
            await admin.get(f"{BASE}/usage/cycles"),
        ]
        gone = await admin.get(f"{BASE}/usage/executions")
    assert gone.status_code == 404, gone.text  # no per-execution list (owner decision)
    for secret in secrets:
        assert secret not in gone.text, secret
    for response in responses:
        assert response.status_code == 200, response.text
        payload = response.json()
        assert [k for k in json_keys(payload) if FORBIDDEN.search(k)] == []
        for secret in secrets:
            assert secret not in response.text, (secret, response.request.url)
    usage = responses[0].json()
    # Money is a fixed-point string: 2 places for the bill, 4 for allocated prices.
    for key in ("ai_usage_price", "platform_fee", "total"):
        assert re.fullmatch(r"\d+\.\d{2}", usage[key]), usage[key]
    for entry in usage["by_agent"] + usage["daily"]:
        assert re.fullmatch(r"\d+\.\d{4}", entry["price"]), entry
    assert usage["executions"] == 3
    agents = usage["by_agent"]
    assert sum(Decimal(a["price"]) for a in agents) == Decimal(usage["ai_usage_price"])
    assert sum(Decimal(d["price"]) for d in usage["daily"]) == Decimal(usage["ai_usage_price"])

    logged = [json.dumps(entry, default=str) for entry in structured]
    logged += [record.getMessage() for record in caplog.records]
    logged += [json.dumps(record.__dict__, default=str) for record in caplog.records]
    for line in logged:
        for secret in secrets:
            assert secret not in line, (secret, line)
        assert not re.search(r"prompt_tokens|completion_tokens|raw_cost|markup", line), line
