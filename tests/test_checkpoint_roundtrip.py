"""Checkpoint round-trip across a simulated restart (LG01 §7.4).

Runs the trivial 2-node graph to its interrupt, tears down the saver / pool /
Python objects to simulate a process restart, rebuilds a fresh saver on a NEW
pool against the SAME container, resumes with the same ``thread_id``, and
asserts ``node_b`` saw ``node_a``'s state and the graph completed.
"""  # LG01

from __future__ import annotations

import uuid
from typing import Any

import pytest
from csa.graph.saver import build_checkpoint_pool, build_saver
from csa.graph.trivial_graph import build_trivial_graph
from langgraph.types import Command

from tests.conftest import CSA_APP_PASSWORD, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]


def _app_lg_dsn(container: object) -> str:
    """A ``csa_app`` DSN; the checkpoint pool sets ``search_path=lg`` itself."""
    host = container.get_container_host_ip()  # type: ignore[attr-defined]
    port = container.get_exposed_port(5432)  # type: ignore[attr-defined]
    return f"postgresql://csa_app:{CSA_APP_PASSWORD}@{host}:{port}/csa"


async def test_roundtrip_resumes_across_restart(lg_setup_dsn: str, pg_container: object) -> None:
    dsn = _app_lg_dsn(pg_container)
    thread_id = str(uuid.uuid4())  # thread_id == trace_id
    config: Any = {"configurable": {"thread_id": thread_id}}

    # --- "process 1": run to the interrupt, then tear everything down. ---
    pool1 = build_checkpoint_pool(dsn)
    await pool1.open(wait=True)
    saver1 = build_saver(pool1)
    graph1 = build_trivial_graph(saver1)
    result1 = await graph1.ainvoke({"value": None, "review": None}, config)
    # The graph is paused at the interrupt (node_b has not run yet).
    assert "__interrupt__" in result1
    del graph1, saver1
    await pool1.close()
    del pool1

    # --- "process 2": fresh pool + saver against the SAME container. ---
    pool2 = build_checkpoint_pool(dsn)
    await pool2.open(wait=True)
    saver2 = build_saver(pool2)
    graph2 = build_trivial_graph(saver2)
    try:
        result2 = await graph2.ainvoke(Command(resume="approved"), config)
        # node_b read node_a's value => state survived the simulated restart.
        assert result2["value"] == "read-by-node-b:set-by-node-a"
        assert result2["review"] == "approved"
    finally:
        await pool2.close()
