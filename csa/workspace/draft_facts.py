"""Facts a batch email draft may state, gathered with the checks the tabs already apply.

``subscription_facts`` gives a customer's plan, status, renewal date and price, only when the user
may read Subscriptions (the rule ``csa/subscriptions/api.py`` applies) and the Subscriptions
package is active. ``kb_facts`` gives knowledge base passages for a topic, only when the Knowledge
base package is active. Both return an empty list instead of an error when a check says no, so a
draft is written without those facts and the card says "no facts used".

The knowledge base search is read-only: it embeds the topic and runs ``kb/search_chunks.sql``. It
never goes through ``KbAgent._query``, which records an unmatched query as a knowledge gap on the
admins' list; staff instruction text (with names) must never land there.

Facts are plain data with ids. A passage is untrusted text and is not sanitised here (the drafter
treats it as data); each fact's text is capped and so is the number of facts.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from fastapi import HTTPException, Request

from csa.agents.kb.embedder import KbEmbedder, KbEmbeddingError, vector_to_sql
from csa.console.contracts import ConsoleActor
from csa.console.services import require_package
from csa.core import db
from csa.core.tenancy import tx
from csa.email.models import DraftFact
from csa.subscriptions.api import require_subscriptions_read

logger = structlog.get_logger(__name__)

MAX_FACT_CHARS = 1500
MAX_FACTS = 5  # per source: a customer's subscriptions, or knowledge base passages


async def subscription_facts(
    request: Request, actor: ConsoleActor, client_id: UUID, customer_id: UUID
) -> list[DraftFact]:
    """The customer's subscriptions as facts; empty when the role or the package says no."""
    try:
        require_subscriptions_read(actor, client_id)
        await require_package(request.app.state.pool, client_id, frozenset({"subscriptions"}))
    except HTTPException:
        return []
    async with tx(request.app.state.pool, str(client_id)) as conn:
        zone = await _tenant_zone(conn, client_id)
        rows = await db.q.fetch_all(
            "subscriptions/subscriptions_for_customer.sql",
            {"client_id": client_id, "customer_id": customer_id, "limit": MAX_FACTS},
            conn=conn,
        )
    return [_subscription_fact(row, zone) for row in rows]


async def kb_facts(
    request: Request,
    client_id: UUID,
    topic: str,
    *,
    limit: int = 3,
    threshold: float = 0.35,
) -> list[DraftFact]:
    """Knowledge base passages for ``topic`` at or above ``threshold``; empty when the package is
    off, the topic is blank, or embedding is unavailable. Writes nothing, whatever the result."""
    topic = topic.strip()
    if not topic:
        return []
    state = request.app.state
    try:
        await require_package(state.pool, client_id, frozenset({"knowledge_base"}))
    except HTTPException:
        return []
    embedder: KbEmbedder | None = getattr(state, "kb_embedder", None)
    if embedder is None:
        logger.warning("draft_kb_facts_skipped", code="embedder_unavailable")
        return []
    try:
        vector = vector_to_sql(await embedder.embed(topic))
    except KbEmbeddingError:
        logger.warning("draft_kb_facts_skipped", code="embedding_unavailable")
        return []
    async with tx(state.pool, str(client_id)) as conn:
        rows = await db.q.fetch_all(
            "kb/search_chunks.sql",
            (vector, client_id, embedder.model_name, vector, max(1, min(limit, MAX_FACTS))),
            conn=conn,
        )
    return [
        DraftFact(
            id=f"kb_chunk_{row['id']}",
            text=str(row["content"])[:MAX_FACT_CHARS],
            source=f"kb:{row['title']}",
        )
        for row in rows
        if float(row["similarity"]) >= threshold
    ]


async def _tenant_zone(conn: Any, client_id: UUID) -> ZoneInfo | None:
    row = await db.q.fetch_one(
        "subscriptions/tenant_timezone.sql", {"client_id": client_id}, conn=conn
    )
    try:
        return ZoneInfo(row["timezone"]) if row and row["timezone"] else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _subscription_fact(row: dict[str, Any], zone: ZoneInfo | None) -> DraftFact:
    """Plan name, status, renewal date and price, saying what the Subscriptions tab says.

    A date is shown only where the subscription's state gives it a meaning: ``Renews on`` for an
    active subscription that carries on, ``Ends on`` when it is set to cancel at period end,
    ``Resumes on`` for a paused one with a resume date, and nothing for a cancelled one (its
    stored renewal date is left over from before). A scheduled plan change is added with its date.
    """

    def day(value: datetime) -> str:
        return (value.astimezone(zone) if zone else value).date().isoformat()

    status = row["status"]
    parts = [f"Plan: {row['plan_name'] or row['plan']}", f"Status: {status}"]
    renews: datetime | None = row["renews_at"]
    if status == "active" and renews is not None:
        label = "Ends on" if row["cancel_at_period_end"] else "Renews on"
        parts.append(f"{label}: {day(renews)}")
    elif status == "paused" and row["resume_at"] is not None:
        parts.append(f"Resumes on: {row['resume_at'].isoformat()}")
    if status != "cancelled" and row["pending_plan"] is not None:
        change = f"Plan change: {row['pending_plan_name'] or row['pending_plan']}"
        if row["pending_effective_at"] is not None:
            change += f" from {day(row['pending_effective_at'])}"
        parts.append(change)
    price: Decimal | None = row["price"]
    if price is not None:
        parts.append(f"Price: {price:f} {row['currency'] or ''}".rstrip())
    return DraftFact(
        id=f"subscription_{row['id']}",
        text="; ".join(parts)[:MAX_FACT_CHARS],
        source=f"subscription:{row['id']}",
    )
