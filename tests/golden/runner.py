"""LG08 golden-case runner: whole turns through the real worker, graph, agents and SQL.

Only the model is faked. ``CassetteGroq`` answers each ``json_completion`` from the case's
cassette, keyed by the response schema name, and records every prompt so cases can assert
what the model was shown (facts) and never shown (PII, other tenants' data).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypeVar

import httpx
import psycopg
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
from csa.core import crypto
from csa.decide.groq import GroqClient, GroqError
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

CASES_DIR = Path(__file__).parent / "cases"
T = TypeVar("T", bound=BaseModel)


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


class Seed(Strict):
    policy: dict[str, Any] = Field(default_factory=dict)
    products: list[Product] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    first_contact: bool = False
    tickets: list[Ticket] = Field(default_factory=list)


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


class Tap(Strict):
    from_turn: int = Field(ge=0)
    button: Literal["accept", "reject", "first_option"]
    expired: bool = False
    other_contact: bool = False


class Turn(Strict):
    say: str
    redacted: str | None = None
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
        GoldenCase.model_validate_json(path.read_text(encoding="utf-8"))
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
            "INSERT INTO contacts (id, client_id, telegram_id, full_name) VALUES (%s, %s, %s, %s)",
            (tenant.contact_id, cid, int(tenant.external_id), "Golden Contact"),
        )
        conn.execute(
            "INSERT INTO conversations (id, client_id, contact_id, channel, channel_thread) "
            "VALUES (%s, %s, %s, 'telegram', %s)",
            (tenant.conversation_id, cid, tenant.contact_id, tenant.external_id),
        )
        other_contact_id = uuid.uuid4()
        if any(order.other_contact for order in case.seed.orders):
            conn.execute(
                "INSERT INTO contacts (id,client_id,full_name) VALUES (%s,%s,'Other Contact')",
                (other_contact_id, cid),
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
                    tenant.contact_id,
                    tenant.conversation_id,
                    ticket.subject,
                    ticket.status,
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
            language="en",
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
    )


def check(expect: Expect, seen: Observed, llm: CassetteGroq, escalations_before: int) -> None:
    called = [name for name, _ in llm.calls]
    prompts = "\n".join(prompt for _, prompt in llm.calls)
    compose = "\n".join(prompt for name, prompt in llm.calls if name == "ComposeOut")
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
    ):
        expected = getattr(expect, field)
        if expected is not None:
            assert getattr(seen, field) == expected, detail
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
