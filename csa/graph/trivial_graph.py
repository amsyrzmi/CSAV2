"""The trivial 2-node interrupt graph (LG01 §7.4).

This is the ONLY langgraph usage outside the saver/retention wiring in
``csa/graph``. It exists purely to prove the checkpoint round-trip: ``node_a``
sets a value, an ``interrupt()`` pauses between the nodes, and ``node_b`` reads
``node_a``'s value after the human resumes. No business logic.
"""  # LG01

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt


def _keep_last(_old: str | None, new: str | None) -> str | None:
    """Reducer: last writer wins (keeps the state shape trivially mergeable)."""
    return new


class TrivialState(TypedDict):
    """State carried across the interrupt: ``node_a``'s value and the review."""

    value: Annotated[str | None, _keep_last]
    review: Annotated[str | None, _keep_last]


def node_a(state: TrivialState) -> dict[str, Any]:
    """First node: set a value, then interrupt for a human review."""
    del state  # unused; node_a seeds the value
    return {"value": "set-by-node-a"}


def node_review(state: TrivialState) -> dict[str, Any]:
    """Interrupt point: pause and wait for a resume payload."""
    del state  # unused; the resume payload comes from interrupt()
    decision = interrupt({"question": "resume?"})
    return {"review": str(decision)}


def node_b(state: TrivialState) -> dict[str, Any]:
    """Second node: read ``node_a``'s value (proves state survived the restart)."""
    seen = state["value"]
    return {"value": f"read-by-node-b:{seen}"}


def build_trivial_graph(checkpointer: BaseCheckpointSaver[Any]) -> CompiledStateGraph:
    """Compile the 2-node interrupt graph against ``checkpointer``."""
    builder = StateGraph(TrivialState)
    builder.add_node("node_a", node_a)
    builder.add_node("node_review", node_review)
    builder.add_node("node_b", node_b)
    builder.add_edge(START, "node_a")
    builder.add_edge("node_a", "node_review")
    builder.add_edge("node_review", "node_b")
    builder.add_edge("node_b", END)
    return builder.compile(checkpointer=checkpointer)
