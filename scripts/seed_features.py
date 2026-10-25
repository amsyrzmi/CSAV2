"""Offline customer-service seed fixtures; no model/provider configuration is loaded."""

from __future__ import annotations

from typing import Any

from csa.agents.kb.embedder import DefaultKbEmbedder, vector_to_sql
from csa.core import db
from psycopg import AsyncClientCursor, AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from scripts.seed_dev import seed_id


class SeedArticle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str
    content: str
    category: str


async def execute_seed(conn: AsyncConnection[Any], filename: str, params: dict[str, Any]) -> None:
    # ClientCursor binds values with psycopg adaptation, allowing a trusted seed file
    # to contain several statements. Never concatenate values into the SQL text.
    async with AsyncClientCursor(conn) as cursor:
        await cursor.execute(db.q.load(f"seed/{filename}.sql"), params)


async def customer_service(conn: AsyncConnection[Any], params: dict[str, Any]) -> None:
    slug = params["slug"]
    await execute_seed(
        conn,
        "customer_service",
        {
            **params,
            "message_id": seed_id(slug, "inbox:inbound"),
            "reply_id": seed_id(slug, "inbox:outbound"),
            "trace_id": seed_id(slug, "inbox:approval"),
        },
    )
    articles = (
        SeedArticle(
            title=f"{slug} returns policy",
            content=(
                f"{slug} synthetic returns policy: unused items may be returned within "
                "14 days with proof of purchase. Customers pay return shipping. "
                "Refund eligibility requires staff review."
            ),
            category="returns",
        ),
        SeedArticle(
            title=f"{slug} shipping policy",
            content=(
                f"{slug} synthetic shipping policy: standard delivery takes 3 to 5 working "
                "days after dispatch. Tracking is available when the order is shipped."
            ),
            category="shipping",
        ),
    )
    embedder = DefaultKbEmbedder()  # Explicit offline hash-384, independent of TEI settings.
    for article in articles:
        await conn.execute(
            db.q.load("seed/kb.sql"),
            {
                "client_id": params["client_id"],
                "document_id": seed_id(slug, f"kb:{article.category}"),
                "chunk_id": seed_id(slug, f"kb:{article.category}:chunk"),
                "title": article.title,
                "content": article.content,
                "category": article.category,
                "embedding": vector_to_sql(await embedder.embed(article.content)),
                "metadata": Jsonb({"synthetic": True}),
            },
        )
