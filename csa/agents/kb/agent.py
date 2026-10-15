"""Knowledge Base agent: semantic chunk retrieval, gap logging, and ingestion (LG13)."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel

from csa.agents.base import AgentContext, SideEffects
from csa.agents.kb.chunking import DEFAULT_MAX_CHARS, chunk_text
from csa.agents.kb.embedder import (
    DefaultKbEmbedder,
    KbEmbedder,
    KbEmbeddingError,
    vector_to_sql,
)
from csa.agents.kb.extract import ExtractedChunk
from csa.agents.kb.models import KbInput
from csa.agents.kb.store import store_document
from csa.contracts.facts import (
    AgentError,
    AgentResult,
    AgentResultData,
    Fact,
    ResultMeta,
    Ui,
)
from csa.contracts.plan import RiskClass
from csa.core import db
from csa.core.settings import get_settings
from csa.core.tenancy import tx


def _chunk_text(text: str, max_chunk_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    return chunk_text(text, max_chunk_chars) or [text.strip()]


class KbAgent:
    name = "kb_agent"
    Input = KbInput
    side_effects: SideEffects = "reversible"
    timeout_ms = 15000
    max_retries = 1
    ACTION_RISK: dict[str, RiskClass] = {
        "query": "read",
        "list_gaps": "read",
        "ingest": "commit",
        "delete": "commit",
        "resolve_gap": "commit",
    }

    def __init__(self, embedder: KbEmbedder | None = None) -> None:
        self._embedder = embedder

    @property
    def embedder(self) -> KbEmbedder:
        # Resolved lazily so building the agent registry does not require settings.
        if self._embedder is None:
            self._embedder = DefaultKbEmbedder.from_settings(get_settings())
        return self._embedder

    def risk(self, inp: BaseModel) -> RiskClass:
        assert isinstance(inp, KbInput)
        return self.ACTION_RISK[inp.action]

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult:
        assert isinstance(inp, KbInput)
        handler = {
            "query": self._query,
            "ingest": self._ingest,
            "delete": self._delete,
            "list_gaps": self._list_gaps,
            "resolve_gap": self._resolve_gap,
        }[inp.action]
        return await handler(ctx, inp)

    async def _query(self, ctx: AgentContext, inp: KbInput) -> AgentResult:
        assert inp.query is not None
        query_text = inp.query.strip()
        try:
            vec = await self.embedder.embed(query_text)
        except KbEmbeddingError as exc:
            return self._error(ctx, "embedding_unavailable", "upstream", str(exc), retryable=True)
        vec_sql = vector_to_sql(vec)

        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            rows = await db.q.fetch_all(
                "kb/search_chunks.sql",
                (vec_sql, ctx.client_id, self.embedder.model_name, vec_sql, inp.limit),
                conn=conn,
            )

        matches = [r for r in rows if float(r["similarity"]) >= inp.threshold]

        if matches:
            facts: list[Fact] = []
            for r in matches:
                header = f"{r['title']}"
                if r.get("heading"):
                    header += f" - {r['heading']}"
                facts.append(
                    Fact(
                        id=f"kb_chunk_{r['id']}",
                        text=r["content"],
                        kind="verbatim",
                        source=f"kb:{header}",
                    )
                )
            facts.append(
                Fact(
                    id="kb_summary",
                    text=f"Retrieved {len(matches)} relevant article(s) matching '{query_text}'.",
                    kind="derived",
                    source="kb_agent",
                )
            )
            return self._ok(ctx, facts)

        # No match meeting threshold -> automatically record gap for admin review
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            await db.q.execute(
                "kb/record_gap.sql",
                (ctx.client_id, query_text),
                conn=conn,
            )

        gap_msg = (
            f"No matching knowledge base documentation found for '{query_text}'. "
            f"Query logged for knowledge gap review."
        )
        return self._ok(
            ctx,
            [
                Fact(
                    id="kb_gap_recorded",
                    text=gap_msg,
                    kind="derived",
                    source="kb_agent",
                )
            ],
        )

    async def _ingest(self, ctx: AgentContext, inp: KbInput) -> AgentResult:
        assert inp.title is not None and inp.content is not None
        chunks = [
            ExtractedChunk(content=piece, heading=inp.title) for piece in _chunk_text(inp.content)
        ]
        content_hash = hashlib.sha256(inp.content.encode("utf-8")).hexdigest()
        try:
            embeddings = await self.embedder.embed_batch([c.content for c in chunks])
        except KbEmbeddingError as exc:
            return self._error(ctx, "embedding_unavailable", "upstream", str(exc), retryable=True)

        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            stored = await store_document(
                conn,
                client_id=ctx.client_id,
                title=inp.title,
                chunks=chunks,
                embeddings=embeddings,
                content_hash=content_hash,
                embed_model=self.embedder.model_name,
                category=inp.category,
                language=inp.language,
                source_uri=inp.source_uri,
            )

        if stored.outcome == "duplicate":
            msg = f"Knowledge base document '{inp.title}' already exists with identical content."
        else:
            msg = (
                f"Knowledge base document '{inp.title}' ingested successfully "
                f"with {stored.chunk_count} chunks."
            )
        return self._ok(
            ctx,
            [
                Fact(
                    id=f"kb_doc_{stored.document_id}",
                    text=msg,
                    kind="verbatim",
                    source="kb_agent",
                )
            ],
            duplicate=stored.outcome == "duplicate",
        )

    async def _delete(self, ctx: AgentContext, inp: KbInput) -> AgentResult:
        assert inp.document_id is not None
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            await db.q.execute(
                "kb/delete_chunks_by_document.sql",
                (ctx.client_id, inp.document_id),
                conn=conn,
            )
            row = await db.q.fetch_one(
                "kb/delete_document.sql",
                (ctx.client_id, inp.document_id),
                conn=conn,
            )

        if not row:
            return self._error(
                ctx, "document_not_found", "not_found", f"document {inp.document_id} not found"
            )

        return self._ok(
            ctx,
            [
                Fact(
                    id=f"kb_deleted_{inp.document_id}",
                    text=f"Document {inp.document_id} and its associated chunks have been deleted.",
                    kind="verbatim",
                    source="kb_agent",
                )
            ],
        )

    async def _list_gaps(self, ctx: AgentContext, inp: KbInput) -> AgentResult:
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            rows = await db.q.fetch_all(
                "kb/list_gaps.sql",
                (ctx.client_id, inp.limit),
                conn=conn,
            )

        facts = [
            Fact(
                id=f"kb_gap_{r['id']}",
                text=f"Query: '{r['query']}' | Hits: {r['hits']} | Resolved: {r['resolved']}",
                kind="verbatim",
                source="kb:gaps",
            )
            for r in rows
        ]
        return self._ok(ctx, facts)

    async def _resolve_gap(self, ctx: AgentContext, inp: KbInput) -> AgentResult:
        assert inp.gap_id is not None
        async with tx(ctx.pool, str(ctx.client_id)) as conn:
            row = await db.q.fetch_one(
                "kb/resolve_gap.sql",
                (ctx.client_id, inp.gap_id),
                conn=conn,
            )

        if not row:
            return self._error(ctx, "gap_not_found", "not_found", f"gap {inp.gap_id} not found")

        return self._ok(
            ctx,
            [
                Fact(
                    id=f"kb_gap_resolved_{inp.gap_id}",
                    text=f"Knowledge gap for query '{row['query']}' resolved.",
                    kind="verbatim",
                    source="kb_agent",
                )
            ],
        )

    def _ok(
        self,
        ctx: AgentContext,
        facts: list[Fact],
        ui: Ui | None = None,
        *,
        duplicate: bool | None = None,
    ) -> AgentResult:
        return AgentResult(
            ok=True,
            status="ok",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(facts=facts, ui=ui or Ui()),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0, was_duplicate=duplicate),
        )

    def _error(
        self,
        ctx: AgentContext,
        code: str,
        category: Any,
        message: str,
        *,
        hint: str | None = None,
        retryable: bool = False,
    ) -> AgentResult:
        return AgentResult(
            ok=False,
            status="error",
            task_id=ctx.idempotency_key,
            agent=self.name,
            data=AgentResultData(),
            error=AgentError(
                code=code,
                category=category,
                message=message,
                retryable=retryable,
                customer_safe_hint=hint,
            ),
            meta=ResultMeta(trace_id=ctx.trace_id, latency_ms=0),
        )
