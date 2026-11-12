"""LG08 golden-case runner: whole turns through the real worker, graph, agents and SQL.

Only the model is faked. ``CassetteGroq`` answers each ``json_completion`` from the case's
cassette, keyed by the response schema name, and records every prompt so cases can assert
what the model was shown (facts) and never shown (PII, other tenants' data).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypeVar
from zoneinfo import ZoneInfo

import httpx
import psycopg
from csa.agents.ecommerce.models import EcommerceInput
from csa.agents.kb.embedder import HASH_MODEL, _deterministic_embedding, vector_to_sql
from csa.agents.kb.models import KbInput
from csa.agents.staged.models import SubscriptionInput
from csa.contracts.callbacks import CallbackCodec
from csa.contracts.envelope import (
    Callback,
    Channel,
    Contact,
    ConversationRef,
    Envelope,
    Message,
    Meta,
)
from csa.core import crypto, db
from csa.decide.groq import GroqClient, GroqError
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

CASES_DIR = Path(__file__).parent / "cases"
T = TypeVar("T", bound=BaseModel)
# The golden tenant keeps the default company time zone, so a date a fact writes is a date in it.
CASE_ZONE = ZoneInfo("Asia/Kuala_Lumpur")
# `{{date+30}}` in a case file is the day (as a fact writes it, "%d %b %Y") 30 days from today in
# the company's time zone: the seed puts its renewal, end and resume dates on that same day.
_DATE_TOKEN = re.compile(r"\{\{date([+-]\d+)\}\}")


# Today, fixed once at import: the placeholders (collection) and the seed (run) must agree on the
# day even when a run crosses local midnight.
CASE_TODAY = datetime.now(CASE_ZONE).date()


def case_day(days: int) -> date:
    return CASE_TODAY + timedelta(days=days)


def case_noon(days: int) -> datetime:
    """Midday of ``case_day(days)`` in the company's zone: far from a day boundary."""
    return datetime.combine(case_day(days), time(12, 0), tzinfo=CASE_ZONE)


def fill_dates(text: str) -> str:
    return _DATE_TOKEN.sub(lambda m: case_day(int(m.group(1))).strftime("%d %b %Y"), text)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Product(Strict):
    sku: str
    name: str
    price: Decimal
    stock_qty: int = 100
    description: str | None = None


class Order(Strict):
    id: str
    status: str
    total: Decimal
    items: list[dict[str, Any]] = Field(default_factory=list)
    tracking_no: str | None = None
    carrier: str | None = None
    placed_days_ago: int | None = 1
    other_contact: bool = False


class Ticket(Strict):
    id: int
    subject: str
    status: str = "open"
    other_contact: bool = False


class KnowledgeArticle(Strict):
    title: str
    content: str
    other_tenant: bool = False


class PlanSeed(Strict):
    code: str
    name: str
    price: Decimal
    billing_interval: Literal["month", "year"] = "month"
    features: list[str] = Field(default_factory=list)
    signup_mode: Literal["staff", "auto"] = "staff"
    is_signup_open: bool = True


class ContactSeed(Strict):
    """The chat's own contact: a phone, and whether its channel verified it."""

    phone: str | None = None
    phone_verified: bool = False


class SubscriptionSeed(Strict):
    """One subscription of the chat's contact (or of another contact), by plan code."""

    # Fixed only when a case needs to type it (another contact's id in a customer message).
    id: uuid.UUID | None = None
    plan: str
    status: Literal["pending", "active", "paused", "cancelled"] = "active"
    price: Decimal
    renews_in_days: int | None = None
    cancel_at_period_end: bool = False
    pending_plan: str | None = None
    pending_in_days: int | None = None
    resume_in_days: int | None = None
    other_contact: bool = False


class ListingSeed(Strict):
    """One TikTok Shop listing (one SKU), copied into our tables ``synced_minutes_ago`` ago."""

    product_name: str
    variant_name: str
    seller_sku: str
    stock_qty: int
    price: Decimal
    product_status: str = "ACTIVATE"
    synced_minutes_ago: int = 10


class Seed(Strict):
    policy: dict[str, Any] = Field(default_factory=dict)
    products: list[Product] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    first_contact: bool = False
    tickets: list[Ticket] = Field(default_factory=list)
    knowledge: list[KnowledgeArticle] = Field(default_factory=list)
    plans: list[PlanSeed] = Field(default_factory=list)
    subscriptions: list[SubscriptionSeed] = Field(default_factory=list)
    listings: list[ListingSeed] = Field(default_factory=list)
    contact: ContactSeed = Field(default_factory=ContactSeed)
    # Capability rows switched on for the company (as ``kb_agent`` is when it has knowledge).
    services: list[Literal["subscription_agent", "ecommerce_agent"]] = Field(default_factory=list)


class Expect(Strict):
    route: str | None = None
    agents: list[str] | None = None
    gate: str | None = None
    outbound_count: int | None = None
    deliveries: int = 1
    reply_contains: list[str] = Field(default_factory=list)
    reply_excludes: list[str] = Field(default_factory=list)
    buttons: int | None = None
    confirmation: bool | None = None
    escalations: int | None = None
    approvals: int = 0
    gate_reason: str | None = None
    compose_sees: list[str] = Field(default_factory=list)
    model_never_sees: list[str] = Field(default_factory=list)
    llm_not_called: list[str] = Field(default_factory=list)
    llm_calls: dict[str, int] = Field(default_factory=dict)
    status: str = "completed"
    order_statuses: list[str] | None = None
    reserved_qty: int | None = None
    ticket_statuses: list[str] | None = None
    ticket_events: dict[str, int] | None = None
    agent_errors: list[str] | None = None
    action_status: str | None = None
    kb_gaps: int | None = None
    human_muted: bool | None = None
    # Whether this turn's outbound reply went out on the handoff's authority (send_authority).
    handoff_sent: bool | None = None
    # Substrings of the note stored on this turn's escalation (every one must be in it).
    escalation_note_contains: list[str] = Field(default_factory=list)
    # The knowledge gap queries of the company after this turn, sorted (exact).
    kb_gap_queries: list[str] | None = None
    # Requests prepared for staff to decide (agent_preparation_requests) by this turn.
    prepared_requests: int | None = None
    # The reason of the escalation this turn wrote (it must have written one).
    escalation_reason: str | None = None
    # The conversation's unresolved-turn count (the escalation ladder) after this turn.
    clarify_count: int | None = None
    # The company's sign-up requests (signup_requests, any status) after this turn.
    signup_requests: int | None = None
    # Whether this turn's reply offers Telegram's "share my number" button.
    request_phone: bool | None = None


class Tap(Strict):
    from_turn: int = Field(ge=0)
    button: Literal["accept", "reject", "first_option"]
    expired: bool = False
    other_contact: bool = False


class Turn(Strict):
    say: str
    redacted: str | None = None
    # The language the channel reports for the message (Message.language).
    language: str = "en"
    # Staff tapped "resume AI" before this turn: the release the staff action runs.
    resume_ai: bool = False
    cassette: dict[str, Any] = Field(default_factory=dict)
    expect: Expect
    tap: Tap | None = None


class GoldenCase(Strict):
    id: str
    matrix: str
    description: str
    seed: Seed = Field(default_factory=Seed)
    turns: list[Turn] = Field(min_length=1)


def load_cases() -> list[GoldenCase]:
    cases = [
        GoldenCase.model_validate_json(fill_dates(path.read_text(encoding="utf-8")))
        for path in sorted(CASES_DIR.glob("*.json"))
    ]
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids)), "golden case ids must be unique"
    order_ids = [order.id for case in cases for order in case.seed.orders]
    assert len(order_ids) == len(set(order_ids)), "seeded order ids must be unique across cases"
    return cases


class CassetteGroq(GroqClient):
    """Replays recorded model answers; an unrecorded call fails like a provider error."""

    def __init__(self) -> None:
        super().__init__("cassette-key", "cassette-model", httpx.AsyncClient())
        self.cassette: dict[str, list[Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.missing: list[str] = []

    def load(self, cassette: dict[str, Any]) -> None:
        self.cassette = {
            name: list(value) if isinstance(value, list) else [value]
            for name, value in cassette.items()
        }
        self.calls = []
        self.missing = []

    async def close(self) -> None:
        await self._http.aclose()

    async def json_completion(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        timeout: float,
        max_tokens: int = 1500,
    ) -> T:
        name = schema.__name__
        self.calls.append((name, f"{system}\n{user}"))
        queue = self.cassette.get(name)
        if not queue:
            self.missing.append(name)
            raise GroqError("cassette_missing")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, dict) and "error" in answer:
            raise GroqError(str(answer["error"]))
        return schema.model_validate(answer)


class Tenant(BaseModel):
    client_id: uuid.UUID
    endpoint_id: uuid.UUID
    contact_id: uuid.UUID
    conversation_id: uuid.UUID
    external_id: str


def seed_tenant(owner_dsn: str, case: GoldenCase) -> Tenant:
    tenant = Tenant(
        client_id=uuid.uuid4(),
        endpoint_id=uuid.uuid4(),
        contact_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        external_id=str(700_000 + uuid.uuid4().int % 100_000),
    )
    credential_id = uuid.uuid4()
    token = crypto.encrypt(
        "12345:golden-test-token",
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )
    now = datetime.now(UTC)
    cid = tenant.client_id
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version, config) "
            "VALUES (%s, %s, %s, 'lg08-golden', %s)",
            (cid, f"golden-{case.id}-{cid.hex[:8]}", "Golden", Jsonb({"policy": case.seed.policy})),
        )
        conn.execute("SELECT public.seed_core_capabilities(%s)", (cid,))
        if case.seed.knowledge:
            conn.execute(
                "INSERT INTO capability_registry(client_id,name,display_name,description,"
                "pattern,risk_class,workflow_id,input_schema,timeout_ms,max_retries,"
                "capability_kind,is_enabled,client_enabled) "
                "VALUES (%s,'kb_agent','Knowledge base','Synthetic golden retrieval',"
                "'python','low','python:kb_agent',%s,15000,1,'dispatched',true,true)",
                (cid, Jsonb(KbInput.model_json_schema())),
            )
        for service in case.seed.services:
            schema = SubscriptionInput if service == "subscription_agent" else EcommerceInput
            conn.execute(
                "INSERT INTO capability_registry(client_id,name,display_name,description,"
                "pattern,risk_class,workflow_id,input_schema,timeout_ms,max_retries,"
                "capability_kind,is_enabled,client_enabled) "
                "VALUES (%s,%s,%s,'Synthetic golden service','python','low',%s,%s,15000,1,"
                "'dispatched',true,true) ON CONFLICT (client_id,name) DO UPDATE "
                "SET is_enabled=true, client_enabled=true",
                (cid, service, service, f"python:{service}", Jsonb(schema.model_json_schema())),
            )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token) "
            "VALUES (%s, %s, 'telegram', %s)",
            (credential_id, cid, token),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (tenant.endpoint_id, cid, f"golden-{uuid.uuid4().hex}", credential_id),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id, telegram_id, full_name, phone, "
            "phone_verified_at) VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)",
            (
                tenant.contact_id,
                cid,
                int(tenant.external_id),
                "Golden Contact",
                case.seed.contact.phone,
                case.seed.contact.phone_verified,
            ),
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, %s, 'telegram', %s)",
            (tenant.conversation_id, cid, tenant.contact_id, tenant.external_id),
        )
        other_contact_id = uuid.uuid4()
        other_conversation_id = uuid.uuid4()
        if (
            any(order.other_contact for order in case.seed.orders)
            or any(ticket.other_contact for ticket in case.seed.tickets)
            or any(sub.other_contact for sub in case.seed.subscriptions)
        ):
            conn.execute(
                "INSERT INTO contacts (id,client_id,full_name) VALUES (%s,%s,'Other Contact')",
                (other_contact_id, cid),
            )
            if any(ticket.other_contact for ticket in case.seed.tickets):
                conn.execute(
                    "INSERT INTO conversations(id,client_id,contact_id,channel,channel_thread) "
                    "VALUES (%s,%s,%s,'telegram',%s)",
                    (other_conversation_id, cid, other_contact_id, str(other_contact_id.int)),
                )
        for product in case.seed.products:
            conn.execute(
                "INSERT INTO products (client_id, sku, name, price, stock_qty, description) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    cid,
                    product.sku,
                    product.name,
                    product.price,
                    product.stock_qty,
                    product.description,
                ),
            )
        for plan in case.seed.plans:
            conn.execute(
                "INSERT INTO plans (client_id,code,name,price,billing_interval,features,"
                "signup_mode,is_signup_open) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    cid,
                    plan.code,
                    plan.name,
                    plan.price,
                    plan.billing_interval,
                    Jsonb(plan.features),
                    plan.signup_mode,
                    plan.is_signup_open,
                ),
            )
        for index, sub in enumerate(case.seed.subscriptions):
            conn.execute(
                "INSERT INTO subscriptions (id,client_id,contact_id,plan,status,price,started_at,"
                "renews_at,resume_at,pending_plan,pending_effective_at,cancel_at_period_end) "
                "VALUES (coalesce(%s::uuid,uuid_generate_v4()),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    sub.id,
                    cid,
                    other_contact_id if sub.other_contact else tenant.contact_id,
                    sub.plan,
                    sub.status,
                    sub.price,
                    now - timedelta(days=60 - index),
                    None if sub.renews_in_days is None else case_noon(sub.renews_in_days),
                    None if sub.resume_in_days is None else case_day(sub.resume_in_days),
                    sub.pending_plan,
                    None if sub.pending_in_days is None else case_noon(sub.pending_in_days),
                    sub.cancel_at_period_end,
                ),
            )
        if case.seed.listings:
            store_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO channel_credentials (id, client_id, channel, shop_id) "
                "VALUES (%s, %s, 'tiktok_shop', %s)",
                (store_id, cid, f"SYNTH-SHOP-{store_id}"),
            )
            for index, listing in enumerate(case.seed.listings):
                conn.execute(
                    "INSERT INTO marketplace_listings (client_id,store_id,channel,"
                    "external_product_id,external_sku_id,product_name,variant_name,seller_sku,"
                    "price,stock_qty,product_status,synced_at) "
                    "VALUES (%s,%s,'tiktok_shop',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        cid,
                        store_id,
                        f"SYNTH-PRODUCT-{index}",
                        f"SYNTH-SKU-{index}",
                        listing.product_name,
                        listing.variant_name,
                        listing.seller_sku,
                        listing.price,
                        listing.stock_qty,
                        listing.product_status,
                        now - timedelta(minutes=listing.synced_minutes_ago),
                    ),
                )
        for index, order in enumerate(case.seed.orders):
            placed = (
                None
                if order.placed_days_ago is None
                else now - timedelta(days=order.placed_days_ago)
            )
            conn.execute(
                "INSERT INTO orders (id, client_id, contact_id, status, total, items, "
                "tracking_no, carrier, placed_at, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    order.id,
                    cid,
                    other_contact_id if order.other_contact else tenant.contact_id,
                    order.status,
                    order.total,
                    Jsonb(order.items),
                    order.tracking_no,
                    order.carrier,
                    placed,
                    now - timedelta(minutes=len(case.seed.orders) - index),
                ),
            )
        for ticket in case.seed.tickets:
            conn.execute(
                "INSERT INTO tickets "
                "(id,client_id,contact_id,conversation_id,subject,status,category) "
                "VALUES (%s,%s,%s,%s,%s,%s,'other')",
                (
                    ticket.id,
                    cid,
                    other_contact_id if ticket.other_contact else tenant.contact_id,
                    other_conversation_id if ticket.other_contact else tenant.conversation_id,
                    ticket.subject,
                    ticket.status,
                ),
            )
        for article in case.seed.knowledge:
            article_tenant = cid
            if article.other_tenant:
                article_tenant = uuid.uuid4()
                conn.execute(
                    "INSERT INTO clients(id,slug,name,platform_version) "
                    "VALUES (%s,%s,'Foreign synthetic KB','lg08-golden')",
                    (article_tenant, f"golden-kb-{article_tenant.hex}"),
                )
            document_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO kb_documents(id,client_id,title,embed_model,chunk_count) "
                "VALUES (%s,%s,%s,%s,1)",
                (document_id, article_tenant, article.title, HASH_MODEL),
            )
            conn.execute(
                "INSERT INTO kb_chunks(id,client_id,document_id,chunk_index,content,embedding) "
                "VALUES (%s,%s,%s,0,%s,%s::vector)",
                (
                    uuid.uuid4(),
                    article_tenant,
                    document_id,
                    article.content,
                    vector_to_sql(_deterministic_embedding(article.content)),
                ),
            )
    return tenant


def enqueue_turn(
    owner_dsn: str,
    tenant: Tenant,
    case: GoldenCase,
    turn: Turn,
    index: int,
    action_id: uuid.UUID | None = None,
) -> uuid.UUID:
    if turn.resume_ai:
        # Before `now`: the release suppresses automation for messages received before it.
        resume_ai(owner_dsn, tenant)
    trace_id = uuid.uuid4()
    now = datetime.now(UTC)
    envelope = Envelope(
        trace_id=trace_id,
        received_at=now,
        channel=Channel.TELEGRAM,
        idempotency_key=f"telegram:golden-{case.id}-{trace_id.hex}",
        client_id=tenant.client_id,
        contact=Contact(
            id=tenant.contact_id,
            external_id=tenant.external_id,
            channel=Channel.TELEGRAM,
            display_name="Golden Contact",
        ),
        conversation=ConversationRef(
            id=tenant.conversation_id,
            channel_thread=tenant.external_id,
            turn_count=index,
            status="open",
        ),
        message=Message(
            body_raw=turn.say,
            body_redacted=turn.redacted if turn.redacted is not None else turn.say,
            language=turn.language,
        ),
        meta=Meta(is_first_contact=case.seed.first_contact and index == 0),
        callback=Callback(action_id=action_id) if action_id is not None else None,
    )
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ingress_events (id, client_id, endpoint_id, contact_id, "
            "idempotency_key, payload_hash, trace_id, raw, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4(),
                tenant.client_id,
                tenant.endpoint_id,
                tenant.contact_id,
                envelope.idempotency_key,
                hashlib.sha256(turn.say.encode()).hexdigest(),
                trace_id,
                Jsonb(
                    {
                        "envelope": envelope.model_dump(mode="json"),
                        "source": {"channel": "telegram", "kind": "message"},
                    }
                ),
                now,
            ),
        )
    return trace_id


def resume_ai(owner_dsn: str, tenant: Tenant) -> None:
    """What staff's "resume AI" tap does to the conversation (csa.staff.callbacks), same SQL."""
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        released = conn.execute(
            db.q.load("escalation/release_conversation.sql"),
            (tenant.conversation_id, tenant.client_id),
        ).fetchone()
    assert released is not None, "resume_ai needs an escalated conversation"


class Observed(BaseModel):
    route: str | None
    agents: list[str]
    gate: str | None
    replies: list[str]
    uis: list[dict[str, Any]]
    escalations: int
    status: str | None
    deliveries: int
    approvals: int
    gate_reason: str | None
    order_statuses: list[str]
    reserved_qty: int
    ticket_statuses: list[str]
    ticket_events: dict[str, int]
    agent_errors: list[str]
    action_status: str | None
    kb_gaps: int
    human_muted: bool
    handoff_sent: bool
    escalation_notes: list[str]
    kb_gap_queries: list[str]
    escalation_reasons: list[str]
    prepared_requests: int
    clarify_count: int
    signup_requests: int


def observe(
    owner_dsn: str, tenant: Tenant, trace_id: uuid.UUID, action_id: uuid.UUID | None = None
) -> Observed:
    with psycopg.connect(owner_dsn) as conn:
        events = conn.execute(
            "SELECT node, agent, summary FROM agent_events WHERE trace_id=%s ORDER BY id",
            (trace_id,),
        ).fetchall()
        outbound = conn.execute(
            "SELECT body, ui FROM messages WHERE trace_id=%s AND direction='outbound' "
            "ORDER BY created_at",
            (trace_id,),
        ).fetchall()
        escalations = conn.execute(
            "SELECT count(*) FROM escalations WHERE client_id=%s", (tenant.client_id,)
        ).fetchone()
        status = conn.execute(
            "SELECT status FROM ingress_events WHERE trace_id=%s", (trace_id,)
        ).fetchone()
        deliveries = conn.execute(
            "SELECT count(*) FROM outbound_deliveries WHERE trace_id=%s AND status='pending'",
            (trace_id,),
        ).fetchone()
        approvals = conn.execute(
            "SELECT count(*) FROM approvals WHERE trace_id=%s AND status='pending'",
            (trace_id,),
        ).fetchone()
        agent_runs = conn.execute(
            "SELECT DISTINCT agent FROM agent_runs WHERE trace_id=%s ORDER BY agent",
            (trace_id,),
        ).fetchall()
        orders = conn.execute(
            "SELECT status FROM orders WHERE client_id=%s ORDER BY id", (tenant.client_id,)
        ).fetchall()
        reserved = conn.execute(
            "SELECT coalesce(sum(reserved_qty),0) FROM products WHERE client_id=%s",
            (tenant.client_id,),
        ).fetchone()
        tickets = conn.execute(
            "SELECT status FROM tickets WHERE client_id=%s ORDER BY id", (tenant.client_id,)
        ).fetchall()
        ticket_events = conn.execute(
            "SELECT event_type,count(*) FROM ticket_events WHERE client_id=%s GROUP BY event_type",
            (tenant.client_id,),
        ).fetchall()
        agent_errors = conn.execute(
            "SELECT error_code FROM agent_runs WHERE trace_id=%s AND NOT ok ORDER BY error_code",
            (trace_id,),
        ).fetchall()
        action = (
            conn.execute(
                "SELECT status FROM pending_actions WHERE id=%s AND client_id=%s",
                (action_id, tenant.client_id),
            ).fetchone()
            if action_id is not None
            else None
        )
        gaps = conn.execute(
            "SELECT count(*) FROM kb_gaps WHERE client_id=%s", (tenant.client_id,)
        ).fetchone()
        ownership = conn.execute(
            "SELECT (owned_by='human_support' AND "
            "(ai_muted_until IS NULL OR ai_muted_until>now())) IS TRUE "
            "FROM conversations WHERE client_id=%s AND id=%s",
            (tenant.client_id, tenant.conversation_id),
        ).fetchone()
        handoff = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM messages WHERE trace_id=%s AND direction='outbound' "
            "AND send_authority='handoff')",
            (trace_id,),
        ).fetchone()
        notes = conn.execute(
            "SELECT note FROM escalations WHERE client_id=%s AND trace_id=%s "
            "AND note IS NOT NULL ORDER BY created_at",
            (tenant.client_id, trace_id),
        ).fetchall()
        gap_queries = conn.execute(
            "SELECT query FROM kb_gaps WHERE client_id=%s ORDER BY query", (tenant.client_id,)
        ).fetchall()
        reasons = conn.execute(
            "SELECT reason FROM escalations WHERE client_id=%s AND trace_id=%s ORDER BY created_at",
            (tenant.client_id, trace_id),
        ).fetchall()
        prepared = conn.execute(
            "SELECT count(*) FROM agent_preparation_requests WHERE client_id=%s AND trace_id=%s",
            (tenant.client_id, trace_id),
        ).fetchone()
        clarify = conn.execute(
            "SELECT clarify_count FROM conversations WHERE client_id=%s AND id=%s",
            (tenant.client_id, tenant.conversation_id),
        ).fetchone()
        signups = conn.execute(
            "SELECT count(*) FROM signup_requests WHERE client_id=%s", (tenant.client_id,)
        ).fetchone()
    route = next((s.get("route") for node, _, s in events if node == "route"), None)
    gate = next((s.get("decision") for node, _, s in events if node == "gate"), None)
    gate_reason = next((s.get("reason") for node, _, s in events if node == "gate"), None)
    agents = [row[0] for row in agent_runs]
    return Observed(
        route=route,
        agents=agents,
        gate=gate,
        replies=[row[0] for row in outbound],
        uis=[row[1] or {} for row in outbound],
        escalations=escalations[0] if escalations else 0,
        status=status[0] if status else None,
        deliveries=deliveries[0] if deliveries else 0,
        approvals=approvals[0] if approvals else 0,
        gate_reason=gate_reason,
        order_statuses=[row[0] for row in orders],
        reserved_qty=reserved[0] if reserved else 0,
        ticket_statuses=[row[0] for row in tickets],
        ticket_events=dict(ticket_events),
        agent_errors=[row[0] for row in agent_errors],
        action_status=action[0] if action else None,
        kb_gaps=gaps[0] if gaps else 0,
        human_muted=ownership[0] if ownership else False,
        handoff_sent=bool(handoff and handoff[0]),
        escalation_notes=[row[0] for row in notes],
        kb_gap_queries=[row[0] for row in gap_queries],
        escalation_reasons=[row[0] for row in reasons],
        prepared_requests=prepared[0] if prepared else 0,
        clarify_count=clarify[0] if clarify else 0,
        signup_requests=signups[0] if signups else 0,
    )


def check(expect: Expect, seen: Observed, llm: CassetteGroq, escalations_before: int) -> None:
    called = [name for name, _ in llm.calls]
    prompts = "\n".join(prompt for _, prompt in llm.calls)
    compose = "\n".join(
        prompt for name, prompt in llm.calls if name in {"ComposeOut", "HandoffOut"}
    )
    reply = "\n".join(seen.replies)
    detail = json.dumps(
        {"seen": seen.model_dump(), "llm_calls": called, "missing": llm.missing}, default=str
    )
    assert seen.status == expect.status, detail
    assert seen.deliveries == expect.deliveries, detail
    assert seen.approvals == expect.approvals, detail
    for field in (
        "order_statuses",
        "reserved_qty",
        "ticket_statuses",
        "ticket_events",
        "agent_errors",
        "action_status",
        "kb_gaps",
        "human_muted",
        "handoff_sent",
        "prepared_requests",
        "clarify_count",
        "signup_requests",
    ):
        expected = getattr(expect, field)
        if expected is not None:
            assert getattr(seen, field) == expected, detail
    if expect.kb_gap_queries is not None:
        assert seen.kb_gap_queries == sorted(expect.kb_gap_queries), detail
    if expect.escalation_reason is not None:
        assert seen.escalation_reasons == [expect.escalation_reason], detail
    stored = "\n".join(seen.escalation_notes)
    for text in expect.escalation_note_contains:
        assert text in stored, f"escalation note missing {text!r}: {detail}"
    if expect.gate_reason is not None:
        assert seen.gate_reason == expect.gate_reason, detail
    assert not llm.missing, f"unrecorded model calls {llm.missing}: {detail}"
    if expect.route is not None:
        assert seen.route == expect.route, detail
    if expect.agents is not None:
        assert seen.agents == sorted(expect.agents), detail
    if expect.gate is not None:
        assert seen.gate == expect.gate, detail
    if expect.outbound_count is not None:
        assert len(seen.replies) == expect.outbound_count, detail
    for text in expect.reply_contains:
        assert text in reply, f"reply missing {text!r}: {detail}"
    for text in expect.reply_excludes:
        assert text not in reply, f"reply must not contain {text!r}: {detail}"
    if expect.buttons is not None:
        buttons = sum(len(ui.get("options") or []) for ui in seen.uis)
        assert buttons == expect.buttons, detail
    if expect.confirmation is not None:
        has = any(ui.get("confirmation") for ui in seen.uis)
        assert has is expect.confirmation, detail
    if expect.request_phone is not None:
        offered = any(ui.get("request_phone") for ui in seen.uis)
        assert offered is expect.request_phone, detail
    if expect.escalations is not None:
        assert seen.escalations - escalations_before == expect.escalations, detail
    for text in expect.compose_sees:
        assert text in compose, f"composer was not shown {text!r}: {detail}"
    for text in expect.model_never_sees:
        assert text not in prompts, f"model was shown {text!r}"
    for name in expect.llm_not_called:
        assert name not in called, detail
    for name, count in expect.llm_calls.items():
        assert called.count(name) == count, detail


def tap_action(tap: Tap, previous: list[Observed]) -> uuid.UUID:
    assert tap.from_turn < len(previous), "tap must refer to an earlier turn"
    uis = previous[tap.from_turn].uis
    assert len(uis) == 1, "tap source must have one outbound reply"
    button = (
        uis[0]["options"][0] if tap.button == "first_option" else uis[0]["confirmation"][tap.button]
    )
    action = CallbackCodec.decode(button["action_id"])
    assert action is not None, "outbound button must contain a valid callback token"
    return action


def prepare_tap(owner_dsn: str, tenant: Tenant, tap: Tap, action_id: uuid.UUID) -> Tenant:
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        if tap.expired:
            row = conn.execute(
                "UPDATE pending_actions SET expires_at=now()-interval '1 second' "
                "WHERE id=%s AND client_id=%s RETURNING id",
                (action_id, tenant.client_id),
            ).fetchone()
            assert row is not None, "tap expiry must update an existing action"
        if not tap.other_contact:
            return tenant
        other = tenant.model_copy(
            update={
                "contact_id": uuid.uuid4(),
                "conversation_id": uuid.uuid4(),
                "external_id": str(800_000 + uuid.uuid4().int % 100_000),
            }
        )
        conn.execute(
            "INSERT INTO contacts (id,client_id,telegram_id,full_name) "
            "VALUES (%s,%s,%s,'Other Contact')",
            (other.contact_id, other.client_id, int(other.external_id)),
        )
        conn.execute(
            "INSERT INTO conversations (id,client_id,contact_id,channel,channel_thread) "
            "VALUES (%s,%s,%s,'telegram',%s)",
            (other.conversation_id, other.client_id, other.contact_id, other.external_id),
        )
        return other
