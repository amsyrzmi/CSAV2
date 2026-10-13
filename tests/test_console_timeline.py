from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from csa.console import timeline
from csa.console.router import build_router
from csa.graph.coordinator import run_turn
from csa.graph.events import PostgresEventSink, summarize
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_coordinator import FakeDependencies, make_envelope
from tests.test_order_agent import World, world  # noqa: F401


class MemorySink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def record(self, **event: Any) -> None:
        self.events.append(event)


class BrokenSink:
    async def record(self, **event: Any) -> None:
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_graph_records_one_redacted_event_per_step() -> None:
    sink = MemorySink()
    from csa.graph.coordinator import build_graph

    graph = build_graph(FakeDependencies(), events=sink)
    await run_turn(make_envelope("my secret order text"), FakeDependencies(), graph=graph)
    nodes = [e["node"] for e in sink.events]
    assert nodes[0] == "load_ctx" and nodes[-1] == "finalize"
    assert {"route", "gate", "emit"} <= set(nodes)
    gate = next(e for e in sink.events if e["node"] == "gate")
    assert gate["summary"]["decision"] == "send"
    assert all(e["status"] == "ok" and e["duration_ms"] >= 0 for e in sink.events)
    assert "secret" not in repr(sink.events)


@pytest.mark.asyncio
async def test_sink_failure_never_breaks_the_turn() -> None:
    from csa.graph.coordinator import build_graph

    deps = FakeDependencies()
    graph = build_graph(deps, events=BrokenSink())
    await run_turn(make_envelope(), deps, graph=graph)
    assert len(deps.emitted) == 1


def test_summarize_reports_failures_without_text() -> None:
    agent, summary = summarize("compose", {"node_error": "compose:RuntimeError"})
    assert agent is None and summary == {"error": "compose:RuntimeError"}


def test_summarize_does_not_publish_free_text_checker_issues() -> None:
    _, summary = summarize(
        "verify", {"verified": False, "verify_issues": ["Customer private text was unsupported"]}
    )
    assert summary == {"verified": False, "verify_issue_count": 1}


def test_router_rejects_missing_or_wrong_token() -> None:
    app = FastAPI()
    app.state.pool = None
    app.include_router(build_router("x" * 32))
    client = TestClient(app)
    headers = {"X-Client-Id": str(uuid.uuid4())}
    assert client.get("/console/v1/traces", headers=headers).status_code == 401
    bad = {**headers, "Authorization": "Bearer nope"}
    assert client.get("/console/v1/traces", headers=bad).status_code == 401


async def test_timeline_is_persisted_scoped_to_the_tenant_and_pollable(world: World) -> None:  # noqa: F811
    sink = PostgresEventSink(world.pool)
    trace = uuid.uuid4()
    for node, agent in (("route", None), ("run_spoke", "order_agent"), ("gate", None)):
        await sink.record(
            client_id=world.client_id,
            trace_id=trace,
            conversation_id=world.conversation_id,
            node=node,
            agent=agent,
            status="ok",
            duration_ms=5,
            summary={"route": "plan_args"} if node == "route" else {},
        )
    other = uuid.uuid4()
    with psycopg.connect(world.owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s,%s,'Other','t')",
            (other, f"o-{other}"),
        )
    assert await timeline.list_events(world.pool, other) == []

    events = await timeline.list_events(world.pool, world.client_id, trace_id=trace)
    assert [e["node"] for e in events] == ["route", "run_spoke", "gate"]
    newer = await timeline.list_events(world.pool, world.client_id, after_id=events[0]["id"])
    assert [e["node"] for e in newer] == ["run_spoke", "gate"]
    [row] = await timeline.list_traces(world.pool, world.client_id)
    assert row["steps"] == 3 and row["agents"] == ["order_agent"] and row["route"] == "plan_args"
