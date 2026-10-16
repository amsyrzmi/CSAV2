"""Tests for Knowledge Base agent: embeddings, chunking, retrieval, and gaps (LG13)."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from csa.agents.base import AgentContext
from csa.agents.kb import DefaultKbEmbedder, KbAgent, KbInput
from csa.agents.kb.agent import _chunk_text
from csa.agents.kb.embedder import _deterministic_embedding, vector_to_sql
from csa.agents.kb.store import StoredDocument


def test_deterministic_embedding_properties() -> None:
    """Embedding should be 384 dimensions, normalized to unit norm."""
    vec1 = _deterministic_embedding("How do I return my parcel?")
    assert len(vec1) == 384
    norm1 = math.sqrt(sum(x * x for x in vec1))
    assert abs(norm1 - 1.0) < 1e-4

    # Identical text produces identical vector
    vec2 = _deterministic_embedding("How do I return my parcel?")
    assert vec1 == vec2

    # Semantic similarity: parcel return vs return parcel item
    vec3 = _deterministic_embedding("Return parcel process and refund item")
    cos_sim_related = sum(a * b for a, b in zip(vec1, vec3, strict=True))

    # Completely different text
    vec_diff = _deterministic_embedding("Quantum physics semiconductor cryogenic cooling")
    cos_sim_diff = sum(a * b for a, b in zip(vec1, vec_diff, strict=True))

    assert cos_sim_related > cos_sim_diff


def test_vector_to_sql_formatting() -> None:
    vec = [0.1234567, -0.654321, 0.0]
    sql_str = vector_to_sql(vec)
    assert sql_str.startswith("[")
    assert sql_str.endswith("]")
    assert "0.123457" in sql_str
    assert "-0.654321" in sql_str


def test_chunking_text() -> None:
    short_text = "Paragraph 1.\n\nParagraph 2."
    # Small adjacent paragraphs are merged up to the limit.
    assert _chunk_text(short_text, max_chunk_chars=50) == ["Paragraph 1.\n\nParagraph 2."]
    assert _chunk_text(short_text, max_chunk_chars=15) == ["Paragraph 1.", "Paragraph 2."]

    long_para = "Sentence one. " * 30
    long_chunks = _chunk_text(long_para, max_chunk_chars=100)
    assert len(long_chunks) > 1
    for c in long_chunks:
        assert len(c) <= 100

    # A paragraph with no sentence breaks (e.g. a PDF table) is still bounded.
    table = " ".join(f"cell{i}" for i in range(200))
    assert all(len(c) <= 80 for c in _chunk_text(table, max_chunk_chars=80))


def test_kb_input_validation() -> None:
    # Query requires query text
    with pytest.raises(ValueError, match="query is required"):
        KbInput(action="query", query="   ")

    # Ingest requires title and content
    with pytest.raises(ValueError, match="title is required"):
        KbInput(action="ingest", title="", content="Valid content")

    with pytest.raises(ValueError, match="content is required"):
        KbInput(action="ingest", title="Valid Title", content="")

    # Delete requires document_id
    with pytest.raises(ValueError, match="document_id is required"):
        KbInput(action="delete")

    # Valid inputs pass
    valid_q = KbInput(action="query", query="Where is my order?")
    assert valid_q.threshold == 0.65
    assert valid_q.limit == 3


@pytest.mark.asyncio
async def test_kb_agent_query_match() -> None:
    """When chunks meet the threshold, return verbatim facts."""
    agent = KbAgent(embedder=DefaultKbEmbedder())
    ctx = MagicMock(spec=AgentContext)
    ctx.client_id = uuid4()
    ctx.pool = MagicMock()
    ctx.idempotency_key = "kb_test_task"
    ctx.trace_id = uuid4()

    mock_row = {
        "id": uuid4(),
        "document_id": uuid4(),
        "chunk_index": 0,
        "content": "Return policy: 30 days money-back guarantee.",
        "heading": "Return Window",
        "question": None,
        "title": "Refunds and Returns Policy",
        "source_uri": None,
        "similarity": 0.85,
    }

    with patch("csa.agents.kb.agent.tx") as mock_tx, patch("csa.core.db.q.fetch_all") as mock_fetch:
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch.return_value = [mock_row]

        inp = KbInput(action="query", query="What is the return policy?", threshold=0.7)
        res = await agent.run(ctx, inp)

        assert res.ok is True
        assert len(res.data.facts) == 2
        assert "Return policy: 30 days" in res.data.facts[0].text
        assert "Refunds and Returns Policy - Return Window" in res.data.facts[0].source
        assert res.data.facts[1].id == "kb_summary"


@pytest.mark.asyncio
async def test_kb_agent_query_gap_recording() -> None:
    """When no chunks meet the threshold, automatically log a gap."""
    agent = KbAgent(embedder=DefaultKbEmbedder())
    ctx = MagicMock(spec=AgentContext)
    ctx.client_id = uuid4()
    ctx.pool = MagicMock()
    ctx.idempotency_key = "kb_test_gap"
    ctx.trace_id = uuid4()

    with (
        patch("csa.agents.kb.agent.tx") as mock_tx,
        patch("csa.core.db.q.fetch_all") as mock_fetch,
        patch("csa.core.db.q.execute") as mock_exec,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch.return_value = []  # No search matches

        inp = KbInput(action="query", query="Can I pay in Bitcoin?", threshold=0.7)
        res = await agent.run(ctx, inp)

        assert res.ok is True
        assert len(res.data.facts) == 1
        assert res.data.facts[0].id == "kb_gap_recorded"
        # Verify gap recording was executed
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "kb/record_gap.sql"
        assert args[1] == (ctx.client_id, "Can I pay in Bitcoin?")


@pytest.mark.asyncio
async def test_kb_agent_ingest_and_delete() -> None:
    """Test document ingestion into chunks and subsequent deletion."""
    agent = KbAgent(embedder=DefaultKbEmbedder())
    ctx = MagicMock(spec=AgentContext)
    ctx.client_id = uuid4()
    ctx.pool = MagicMock()
    ctx.idempotency_key = "kb_test_ingest"
    ctx.trace_id = uuid4()

    # 1. Ingest
    with (
        patch("csa.agents.kb.agent.tx") as mock_tx,
        patch("csa.agents.kb.agent.store_document") as mock_store,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_store.return_value = StoredDocument(
            outcome="ingested", document_id=uuid4(), chunk_count=1, version=1
        )

        inp = KbInput(
            action="ingest",
            title="Shipping Guide",
            content="Standard delivery takes 2-3 business days.\n\nExpress takes 1 day.",
            category="shipping",
        )
        res = await agent.run(ctx, inp)
        assert res.ok is True
        assert "Shipping Guide" in res.data.facts[0].text
        kwargs = mock_store.call_args.kwargs
        assert kwargs["embed_model"] == "hash-384"
        assert len(kwargs["chunks"]) == len(kwargs["embeddings"]) == 1

    # 2. Delete
    doc_id = uuid4()
    with (
        patch("csa.agents.kb.agent.tx") as mock_tx,
        patch("csa.core.db.q.execute"),
        patch("csa.core.db.q.fetch_all", return_value=[]),
        patch("csa.core.db.q.fetch_one") as mock_fetch_one,
    ):
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__.return_value = mock_conn
        mock_fetch_one.return_value = {"id": doc_id, "content_hash": "synthetic-hash"}

        del_inp = KbInput(action="delete", document_id=doc_id)
        res_del = await agent.run(ctx, del_inp)
        assert res_del.ok is True
        expected_txt = f"Document {doc_id} and its associated chunks have been deleted."
        assert expected_txt in res_del.data.facts[0].text
