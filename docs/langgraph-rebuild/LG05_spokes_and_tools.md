# LG05 — Spoke framework, order agent port, callbacks, adding new agents

## CONTEXT FOR CODING AGENT
Port `21_order_agent` (and later `22_ticket_agent`, `23_agent_escalation`, booking, kb, etc.) into Python. Most of your "agents" are **deterministic tools** (SQL + fact builders), not LLM agents — implement them as plain async functions behind one wrapper. Only agents that truly need reasoning (e.g. `kb_agent` RAG) get an LLM. Reuse your existing SQL verbatim: move it to `db/sql/orders/*.sql`. Read `LG02` (AgentResult, registry, codec) first.

## Goal
A uniform `Agent` interface with timeout/retry/circuit-breaker/logging in one wrapper, the order agent fully ported (with the 4 known bugs fixed), and a checklist that makes adding an agent a same-day task.

## Interface

```python
class Agent(Protocol):
    name: str
    Input: type[BaseModel]  # source of truth for the schema

    async def run(self, ctx: AgentContext, inp: BaseModel) -> AgentResult: ...


@dataclass
class AgentContext:
    client_id: UUID
    contact_id: UUID
    trace_id: UUID
    conversation_id: UUID | None
    idempotency_key: str  # derived: f"{envelope.idempotency_key}:{task_id}"  ← fixes bug #4
    tx: TenantTx
    clock: Clock
    settings: Settings
    locale: str
    client_cfg: ClientConfig
```
**Wrapper (`csa/agents/runner.py`)** — the same for every agent kind:
1. Resolve from registry (`impl_kind`: `python` | `mcp` | `http` | `n8n_webhook`).
2. Validate `task.input` against `Agent.Input` → validation failure = `AgentResult(error.category="validation", customer_safe_hint=...)`, never an exception.
3. `asyncio.wait_for(timeout_ms)`; retry only when `error.retryable` **and** the agent declares `side_effects != "irreversible"` or uses the idempotency key.
4. Update circuit breaker (LG02 §5).
5. Insert `agent_runs` (input without PII ids, output, latency, model, tokens) — same columns your `Build Log` node writes today.
6. Catch-all: any exception → `AgentResult(ok=False, error.category="internal")`.

## Agent kinds
- `python:` — in-process classes in `csa/agents/<name>/`. Default.
- `mcp:` — adapter calling your FastMCP server (`csa_mcp:8001`); tool schema ⇒ `Input`. Good for agents written by others or in other languages.
- `http:` — signed POST (HMAC) returning the `AgentResult` JSON shape.
- `n8n_webhook:` — signed POST to an n8n webhook; n8n returns `AgentResult`. **Use only for client-specific or SaaS-glue automations**, never for core spokes. Timeouts shorter than the turn deadline; side-effect-free by default.

## Order agent port

Input model (replaces prose rules in the planner prompt):
```python
class OrderInput(BaseModel):
    action: Literal[
        "list", "status", "search_products", "product_detail", "create_draft", "confirm", "cancel"
    ]
    order_id: str | None = None
    sku: str | None = None
    query: str | None = None
    items: list[Item] | None = None
    limit: int = 5
    callback_id: str | None = None  # codec id for confirm/cancel (never raw tokens from the model)
```
Action map (SQL file → fact builder):

| Action | SQL (from n8n node) | Notes |
|---|---|---|
| list | `List Recent Orders` | exclude draft/cancelled as today |
| status | `Order Status` (view `v_order_status`) | **Resolver:** no `order_id` ⇒ 1 active order → use it; >1 → return facts for each + `ui.options` ("Track ORD-…" via codec); 0 → "no orders" fact. This fixes "where is my order?" asking for an id |
| search_products / product_detail | `Search Products` / `Product Detail` | Later: pgvector/TEI hybrid search instead of `ILIKE` |
| create_draft | `Create Draft` CTE (verbatim) | idempotency key = `draft:{ctx.idempotency_key}`; token issued through the **codec**, TTL 30 min |
| confirm | `Confirm Order` | only reachable from a **button callback** (the planner can never emit it); verifies contact via codec |
| cancel | `Cancel Reservation` | draft/pending only; shipped ⇒ error with hint to refund/ticket path |
| expire_sweep | `Expiry Sweep` | **Removed from the agent.** It becomes a scheduled job (LG10) — a core invariant, not a customer request, and it must not be reachable from a customer turn |

Fixes baked into the port:
1. Idempotency key now propagated (bug #4).
2. `ord:buy:{sku}:{qty}` and `ord:browse` handled via codec → `create_draft` / `search_products` (bug #3). Add a golden case for each button.
3. `callback_data` ≤ 64 bytes (codec).
4. Ops "new order awaiting approval" message is an **event** `order.submitted` consumed by a notifier that reads the ops chat from `clients.config.ops` and sends through the outbox (retries + logging). No hard-coded chat id.
5. One shared `facts.py` with `money()`, `day()` (Asia/Kuala_Lumpur or client tz), `items_text()`.
6. **Reservation accounting aggregates per product everywhere** (reserve in `create_draft`, release in `cancel` and in the sweep). Today all three use `UPDATE products … FROM <multi-row source>`, which applies only one source row per product (finding #25). `create_draft` also **merges duplicate SKUs** (sum their quantities) and checks the **total** per product against `available_qty`, closing the split-line oversell. Add a property test: random orders with duplicate SKU lines and concurrent drafts; invariant `reserved_qty == Σ qty of stock-holding orders` (LG10 §4).
7. **Unknown action is a validation error**, never "run the sweep" (today the Router's fallback output is the expiry sweep; finding #26).
8. **Channel is a parameter**, not the literal `'telegram'` in `Create Draft`; `orders_channel_chk` is extended (LG11 M03) and `order_confirmations.channel` is set from the envelope (`web_console` maps to the order channel `web`).
9. **Order ids:** use `new_order_id()` (10 hex chars, LG11 M10) and retry on a primary-key conflict (up to 3 times); today's 8-hex id is a global PK and an id collision aborts the statement (finding #34). Accept 8–12 character ids when customers type them.

SQL changes to the existing statements (same structure, three edits):
```sql
-- create_draft: merge duplicate SKUs; compare TOTAL demand per product
want AS (
  SELECT r.client_id, it->>'sku' AS sku, SUM(GREATEST(1, COALESCE((it->>'qty')::int, 1))) AS qty
  FROM req r CROSS JOIN LATERAL jsonb_array_elements(r.items) it
  GROUP BY r.client_id, it->>'sku'
),
-- ... unchanged priced / validated / ins_order / ins_items ...
reserve AS (                                   -- aggregate; never UPDATE … FROM a multi-row source
  UPDATE products p SET reserved_qty = p.reserved_qty + x.qty, updated_at = now()
  FROM (SELECT product_id, SUM(qty) AS qty FROM ins_items GROUP BY product_id) x
  WHERE p.id = x.product_id RETURNING p.id
),
-- cancel (and the sweep, LG10): release the same way
rel AS (
  UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - x.qty, 0), updated_at = now()
  FROM (SELECT oi.product_id, SUM(oi.qty) AS qty
        FROM order_items oi JOIN upd ON upd.id = oi.order_id GROUP BY oi.product_id) x
  WHERE p.id = x.product_id RETURNING p.id
)
```
Keep `GREATEST(…,0)` only as a safety net and **report when it clamps** (LG10 job stats) instead of hiding drift.

Keep `_approval` semantics: confirm moves the order to `pending_confirmation` + `cod_pending`, stock stays reserved, staff approve → (separate staff flow, LG07 approvals pattern).

## Other spokes (outline only — port after order agent)
New agents (email, web research, subscription, teach-a-task, more invoice) are planned in `LG13_agent_catalog_and_console.md` and are not part of LG05.

| Spoke | Kind | Notes |
|---|---|---|
| ticket_agent | python | categories enum (`returns, shipping, product, billing, technical, other`) lives in its `Input`; delay→shipping, damaged/missing→product mapping becomes a validator + resolver, not prompt text |
| escalation_agent | python | creates ticket + ops notification (outbox); returns the fact "a human agent will follow up, ticket X" — the composer may only promise follow-up if this fact exists |
| booking_agent | python | `list/check_availability/book/reschedule/cancel`; Google Calendar API via a tenant-scoped OAuth adapter; dates/time zones resolved in code |
| kb_agent | python (LLM) | TEI + pgvector retrieval → facts with `kind="verbatim"` chunks + source; logs gaps to `kb_gaps` |
| refund_status / warranty / invoice | python | views `v_invoice_status`, `v_warranty_eligibility` already exist |
| einvoice (LHDN MyInvois) | python | OAuth2 + document signing/validation; legal/financial ⇒ code with tests, **not** n8n |

## Google Calendar API integration (booking spoke)
Calendar is a **tool/API integration**, not a messaging channel. It can use the
Google Cloud project and OAuth client created for Gmail, but must use a
separately authorized per-user/per-tenant calendar connection and its own
least-privilege scopes. Enable the **Google Calendar API** in that Cloud
project. For availability-only operations request
`https://www.googleapis.com/auth/calendar.freebusy`; to create/update events on
the connected user's own calendars prefer
`https://www.googleapis.com/auth/calendar.events.owned` where it satisfies the
calendar-sharing model. If booking into a shared/third-party calendar requires
broader access, request `calendar.events` only with explicit consent. Verify
scope classification and external OAuth verification requirements in Google
Cloud before onboarding customer accounts.

Use a dedicated test Google account and a separate test calendar (synthetic
appointments only); grant the OAuth app access to that calendar and verify
availability, create, update/reschedule, cancel, duplicate requests, conflicts,
time-zone/DST boundaries, and retry-after-timeout idempotency. Resolve dates in
code using the client's configured time zone; re-check free/busy immediately
before a write. Store the selected calendar ID and encrypted refresh token in
the tenant's credential record, not source or global environment. Do not use
service-account access to personal calendars; Workspace domain-wide delegation
is only for domains administered by the operator and requires explicit admin
approval.

**Account setup checklist:** (1) Google account with access to a test calendar;
(2) Google Cloud project (the same project as Gmail is fine); (3) enable Google
Calendar API; (4) configure OAuth consent screen/client and exact HTTPS
redirect URI; (5) add the test account as a test user until OAuth verification
is complete. For production, publish the privacy/terms/homepage and complete
any Google OAuth verification/security assessment required by the selected
scopes.

Official guide: [Google Calendar API authorization scopes](https://developers.google.com/workspace/calendar/api/auth).

Booking-spoke acceptance gate:
- Preparation update (2026-10-15): `csa/agents/staged/service.py` now exposes local booking
  history and unexecuted requests, disabled per tenant by default. This does not satisfy
  the Calendar integration acceptance items below; see LG13 and the Wave 2/3 handoff.
- [ ] OAuth grants only the configured availability/write scopes and tokens are tenant-bound/encrypted.
- [ ] Availability and event writes use the selected test calendar; duplicate requests and retry-after-timeout do not create duplicate events.
- [ ] Conflict re-check, reschedule/cancel authorization, daylight-saving transitions, and client time-zone handling pass tests.
- [ ] Test-account live integration succeeds before enabling the spoke for any customer calendar; external OAuth verification is complete when required.

## Staff order-approval flow (new — nothing implements it today)
What exists: `confirm` moves an order to `pending_confirmation` / `cod_pending`, keeps stock reserved, and `Notify Support Group` posts a text message to a hard-coded chat. **No workflow moves an order on to `confirmed`** (you've said you don't have one), so confirmed orders sit in `pending_confirmation` until someone edits the database. This is new work, not a port (~2–3 days).
Design:
- The ops notification (via the Notifier, below) carries **Approve / Reject** buttons issued through the callback codec (`pending_actions`: `agent='order_approval'`, `action='approve|reject'`, `payload={order_id}`), bound to the ops chat; only users listed in `staff_notification_targets` (or a staff allow-list) may press them. Ops taps arrive at the gateway as callbacks from the ops chat and go to an `order_approval` handler — a **staff path**, never reachable from a customer conversation.
- **Approve:** `orders.status='confirmed'`, `confirmed_at=now()`, `order_events 'staff_approved'`, **commit the stock** (`stock_qty -= qty` and `reserved_qty -= qty`, aggregated per product), outbox message to the customer ("Order … is confirmed"). **Reject:** `cancelled`, release the reservation (aggregated), outbox message with the reason.
- Later states (`processing`, `shipped` with `tracking_no`/`carrier`, `delivered`) use the same ops buttons or your web console; each writes an `order_events` row and can notify the customer.
- `order_approval_watch` (LG10) reminds staff at 50 % and 100 % of `config.orders.staff_approval_ttl` and never auto-cancels unless the client opts in.
- **Business decisions to confirm:** (1) stock is committed at *approval*, not at shipment — this makes the stock-holding statuses exactly `draft` and `pending_confirmation`, which is LG10's default; (2) cancelling after `confirmed` goes through refund/ticket, with stock returned only by an explicit staff action.

## Ticket agent port (`22_ticket_agent`)
Input model: `action: create|get|list|add_note|update|cancel|reopen` (the alias map becomes a validator: delete/remove/close → cancel, status/view/check → get, edit/modify → update, note/comment/append/add_info → add_note), `ticket_id` (accepts `#12`, `TKT-12`, `12` → int), `subject`, `category` (`returns|shipping|product|billing|technical|other`; `escalation` is reserved for internal callers), `priority`, `description`, `note`.
- Replace "build SQL as a string in JS, run `{{ $json.sql }}`" with **one static `.sql` file per action** (`db/sql/tickets/create.sql`, `cancel.sql`, …). Same parameterized logic, now unit-testable and reviewable.
- Keep: duplicate guard (same contact + conversation + subject within 10 min), atomic mutate-with-`ticket_events` audit, `reopen` only within 7 days, "customers can't buy a 1-hour SLA" (urgent → high unless category `escalation`).
- SLA hours (`urgent 1, high 4, normal 24, low 72`) move to `clients.config.sla`; times are formatted in the client's timezone through the shared `facts.py` (today `Asia/Kuala_Lumpur` is hard-coded in several Code nodes).
- **Cancel confirmation:** replace the LLM-supplied `confirm:true` with the pending-action codec (LG02 §4): the first call returns the "not cancelled yet" fact plus Yes/Keep buttons; only the consumed action cancels.
- Planner-prompt rules about ticket categories disappear: the `Input` model plus a resolver (delay → shipping, damaged/missing → product, refund request → returns) own them.

## Escalation agent port (`23_agent_escalation`)
Keep (it's good): validate loudly, **reuse an open escalation ticket for the conversation**, otherwise create one through the ticket agent, mark the conversation escalated, load handoff context, notify staff, and promise "a human agent will follow up" (fact `f1`) only when a ticket exists or staff were notified (`status: partial` if only one succeeded, `ESCALATION_FAILED` + `requires_human` if neither).
Change:
- Ticket creation + conversation mute happen in one transaction.
- The mute it writes (`owned_by`, `ai_muted_until`) is now enforced by the graph (LG04 port note 1).
- Staff notification goes through the **Notifier** below, not a hard-coded sub-workflow.
- Contact details (name, phone, email, telegram id) go to staff only — never into model prompts or customer-visible facts.

## Notifier (replaces `40_notify_staff` and every hard-coded chat id)
What `40_notify_staff` does today (keep the good parts): fan out to a target list, build a staff message (customer name, channel, reason, conversation id, ticket, phone with a `wa.me` link, email, Telegram deep link), aggregate per-target outcomes, log to `action_log` and `dlq` **without contact details**. Problems: the target list is **one hard-coded chat for every client**; only Telegram is built (`Not Built Yet` for the rest); sending is not deduplicated (the `action_log` row is written after sending, and skipped when there is no conversation); `dlq` rows have no consumer.
New design — `notify(client_id, event, payload)`:
```sql
CREATE TABLE staff_notification_targets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES clients(id),
  channel text NOT NULL CHECK (channel IN ('telegram','email','whatsapp','webhook')),
  address text NOT NULL,                                  -- chat id / email / E.164 / https URL
  events text[] NOT NULL DEFAULT '{escalation,approval_needed,order_submitted,dlq,circuit_opened}',
  min_priority text NOT NULL DEFAULT 'normal',
  quiet_hours jsonb, is_enabled boolean NOT NULL DEFAULT true, verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (client_id, channel, address));
```
Seed your own client with the chat id currently hard-coded. Each matching target becomes an **outbox row** (`kind='notification'`, idempotency key `notify:{event}:{trace_id}:{target_id}`) so alerts retry, never duplicate, and every outcome lands in `outbound_delivery_attempts`. A target that goes `dead` raises an ops alert through the *other* targets (or logs loudly if none). Staff messages may contain customer contact details (they need them); audit/DLQ payloads never do. Render Telegram with HTML parse mode, not legacy Markdown. The ops bot may be the same bot as the customer bot or a dedicated staff bot (`webhook_endpoints.credential_id` is unique, so each bot is one endpoint).

## "Add a new agent in a day" checklist
1. `csa/agents/<name>/input.py` — Pydantic `Input` (+ examples in `Field(examples=...)`).
2. `csa/agents/<name>/agent.py` — `run()` returning facts/ui/errors; SQL in `db/sql/<name>/`.
3. Fact builders only; never free-text business claims outside `Fact`.
4. Register: migration inserting a `capability_registry` row (`impl_kind`, `routing_hint`, `side_effects`, `timeout_ms`).
5. Add 5–10 golden cases (LG08) including failure and "missing input" cases.
6. Enable per client via `client_enabled`.
7. No prompt edits. (If you feel the urge to edit the planner prompt, the `Input` model or `routing_hint` is wrong.)

## Acceptance criteria
- [ ] All order actions pass golden cases against the seed (LG08), including button flows and wrong-contact taps.
  - Partial evidence: `tests/test_order_agent.py` covers all actions, button flows and wrong-contact taps; golden cases wait on the LG08 seed.
- [x] Double-tap on a product button creates one draft (idempotency test with concurrent calls). — `test_concurrent_identical_requests_create_one_draft`
- [ ] Draft expires after TTL via cron; reservation released; `order_events` row written.
- [x] Spoke wrapper tests: timeout, retryable error with retry, circuit opens after N failures, half-open probe closes it. — `tests/test_agents_runner.py` (11 tests)
- [x] No hard-coded chat ids/workflow ids remain in the repo (`grep` test). — `tests/test_no_hardcoded_ids.py`

## Pitfalls
- Don't recreate "agent" as an LLM loop when a SQL query is enough — each unnecessary LLM hop costs latency and failure modes.
- Never accept tokens or ids for confirm/cancel from model output; only from the codec.
- Registry rows are config, schemas are code — don't let them drift (startup sync + a CI check).


## LG05 progress notes

Implemented and tested (`tests/test_agents_runner.py`, `tests/test_order_agent.py`):
- `csa/agents/` wrapper (timeout, retry, validation, catch-all, circuit breaker, `agent_runs`).
- `order_agent`, `escalation_agent` (now creates/reuses an `escalation` ticket, mutes the conversation, queues staff notifications), `ticket_agent` (create/get/list/add_note/cancel with button confirmation/reopen, duplicate guard, ownership checks) and `order_approval` (staff approve commits stock, reject releases it; requires an enabled staff target; not routable from customer callbacks).
- Notifier core `csa/notify/notifier.py`: matches `staff_notification_targets` by event/priority and queues idempotent `staff_notifications` rows in the caller's transaction. Migration `0160` adds both tables (RLS) and `seed_core_capabilities(client_id)`, which seeds the registry rows (applied to the sandbox tenants by the seed file).

Staff sender and callback path (migration `0170`, `tests/test_staff_path.py`, `tests/test_staff_codec.py`):
- `csa/notify/sender.py` is driven by the delivery process: claims due `staff_notifications` (SKIP LOCKED), sends to Telegram with inline buttons, retries with backoff, dead-letters after 5 attempts (logged at error level), reclaims stale leases, and defers during `quiet_hours` (`{"start","end","tz"}`; urgent bypasses).
- Customer confirm queues an `approval_needed` notification; the sender issues one-time `staff_actions` (Approve/Reject, 24 h TTL) with `s:<uuid>` callback data.
- The gateway routes `s:` taps (`csa/staff/callbacks.py`): sender must equal an enabled target's address, then `order_approval` runs and the customer gets an outbox message (idempotent per order/action). Wrong chat, repeat taps and expired actions are refused without side effects.
- Not yet verified live with the real dev bot (staff chat id must be inserted as a target). The customer reply uses the endpoint that received the tap, which is right for one bot per tenant.

Not done (explicitly deferred):
- The `circuit_opened` and `dlq` producers now queue deduplicated staff notifications in the same transaction as the breaker/DLQ state change; coverage is in `tests/test_staff_path.py` and `tests/test_worker.py`. Non-Telegram staff notification targets remain unimplemented.
- Staff targets are inserted by hand (no admin API yet; see LG13).
- Draft-expiry cron and approval reminders (LG10), booking/Calendar, kb, WhatsApp orders (LG11 M03).
- Golden cases against the LG08 seed.
- The ticket SLA hours default in code; reading them from `clients.config.sla` is not done.
