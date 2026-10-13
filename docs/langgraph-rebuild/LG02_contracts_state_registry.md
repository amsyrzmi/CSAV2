# LG02 — Contracts, graph state, capability registry, callback codec

## CONTEXT FOR CODING AGENT
Your current n8n system already has a good contract: an envelope in, `AgentResult{ok,status,data.facts[],data.ui,error}` out, facts as the only business truth. This phase turns it into Pydantic models so it is enforced at every boundary and JSON Schema is generated from code. Keep field names where possible so MCP/HTTP agents written against the old shape keep working. Put everything in `csa/contracts/`. No LangGraph imports here.

## Goal
One typed vocabulary for the whole platform: inbound events, turn state, plans, agent results, outbound messages, registry rows, and a compact callback codec.

## Tasks

### 1. Models (`csa/contracts/`)
```python
# envelope.py
class Contact(BaseModel):
    id: UUID | None
    external_id: str
    channel: Channel
    display_name: str | None


class Meta(BaseModel):
    is_first_contact: bool = False
    business_hours: bool = True
    pii_map: dict[str, str] = {}  # placeholder -> raw; never logged, never sent to a model
    turn_deadline_ms: int = 90_000


class Envelope(BaseModel):
    schema_version: Literal["3.0"] = "3.0"
    trace_id: UUID
    received_at: datetime
    channel: Channel
    idempotency_key: str  # f"{channel}:{endpoint.public_id}:{provider_event_id}"  — messages.idempotency_key is GLOBALLY unique, so never use bare tg:{chat}:{msg}
    client_id: UUID
    contact: Contact
    conversation: ConversationRef  # id, channel_thread, turn_count, status
    message: Message  # body_raw, body_redacted, language, attachments[], voice?
    callback: Callback | None = None  # structured button press (see §4)
    meta: Meta
```
```python
# facts.py  (same shape as today)
class Fact(BaseModel):
    id: str
    text: str
    source: str
    kind: Literal["verbatim", "derived"] = "verbatim"
    as_of: datetime | None = None
    confidence: float = 1.0


class UiOption(BaseModel):
    label: str
    action_id: str  # action_id = codec id, NOT raw callback_data


class UiConfirmation(BaseModel):
    required: bool
    order_id: str
    expires_at: datetime
    accept: UiOption
    reject: UiOption


class Ui(BaseModel):
    confirmation: UiConfirmation | None = None
    options: list[UiOption] = []


class AgentError(BaseModel):
    code: str
    category: Literal[
        "validation", "not_found", "conflict", "dependency_unmet", "upstream", "internal"
    ]
    message: str
    retryable: bool = False
    retry_after_ms: int | None = None
    customer_safe_hint: str | None = None


class AgentResult(BaseModel):
    ok: bool
    status: Literal["ok", "partial", "error"]
    task_id: str
    agent: str
    facts: list[Fact] = []
    ui: Ui = Ui()
    suggested_actions: list[str] = []
    requires_human: bool = False
    handoff_reason: str | None = None
    error: AgentError | None = None
    meta: ResultMeta  # trace_id, latency_ms, model, tokens
```
```python
# plan.py
class Task(BaseModel):
    task_id: str
    agent: str
    input: dict


class Plan(BaseModel):
    tasks: list[Task] = Field(default_factory=list, max_length=3)
    reply_strategy: Literal["auto_reply", "ask_clarifying", "escalate", "ignore", "confirm"]
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    intent: Intent
    sentiment: Literal["positive", "neutral", "frustrated", "angry"] = "neutral"
    confidence: float
    requires_confirmation: bool = False
    source: Literal["fast_path", "jev", "llm", "policy"]  # who decided — essential for debugging
```
Also: `Signals` (Jev answers, LG06), `Handoff`, `Draft`, `GateVerdict`, `OutboundMessage` (LG07).

### 2. Intent enum
Your 26 intents are too fine for a classifier and too coupled to agents. Define a **coarse** `Intent` (≈12–14: order, refund_returns, billing_invoice, booking, subscription, warranty, product_info, store_info, technical, complaint, human_request, chitchat, spam, other). Keep the fine-grained `action` inside each agent's own input model (e.g. `OrderInput.action`).

### 3. TurnState (consumed by LG04)
```python
class TurnState(BaseModel):
    envelope: Envelope  # immutable
    ctx: Context | None = None  # summary, recent turn ids, customer_facts, flags
    guard: GuardResult | None = None
    signals: Signals | None = None
    plan: Plan | None = None
    results: Annotated[list[AgentResult], operator.add] = []  # reducer for parallel spokes
    facts: list[Fact] = []
    ui: Ui = Ui()
    failures: list[AgentError] = []
    handoff: Handoff | None = None
    draft: Draft | None = None
    verdict: GateVerdict | None = None
    outbound: list[OutboundMessage] = []
    regen_count: int = 0
```
Rules: **state stays small** (ids and summaries, never full histories); never put `pii_map` into a model prompt; checkpoints are written every step, so anything you add costs DB writes. `thread_id = str(envelope.trace_id)`.

### 4. Callback codec on the existing `pending_actions` table (fixes the 61/64-byte problem)
- Telegram `callback_data` max is 64 bytes; today `ord:confirm:ORD-XXXXXXXX:cfm_<32hex>` is 61.
- `pending_actions` already has what's needed: `id uuid`, `client_id`, `conversation_id`, `contact_id`, `agent`, `action`, `payload jsonb`, `status` (default `open`), `expires_at`. **None of your seven workflows use it.** Make it the single store for button/confirmation payloads.
- Wire format: `callback_data = "a:" + <pending_actions.id>` = 38 bytes. WhatsApp button ids (much higher limit) carry the same string.
- API in `csa/contracts/callbacks.py`:
  - `issue(agent, action, payload, contact_id, conversation_id, ttl) -> str`
  - `resolve(id, contact_id) -> PendingAction | None` (None if expired, not `open`, or wrong contact)
  - `consume(id)` = `UPDATE pending_actions SET status='used' WHERE id=$1 AND status='open' AND expires_at > now() RETURNING *` — atomic, so a double tap cannot run twice.
- Order confirm: keep `order_confirmations` as the domain record; put its token inside `payload`. The model never sees or emits it.
- Ticket cancel (today an LLM-supplied `confirm:true`) uses the same mechanism: first call issues a pending action plus "Yes, cancel" / "Keep ticket" buttons; only the consumed action cancels. Typed "yes" never confirms anything.
- Standardize `status` to `open | used | expired | cancelled` (the table has no CHECK constraint today — add one).

### 5. Capability registry (already rich — extend minimally)
Existing columns to **use, not duplicate**: `name, display_name, description, pattern, tier, version, risk_class, workflow_id (NOT NULL), input_schema, config_schema, timeout_ms (20000), max_retries (1), max_tool_calls, max_iterations, requires_human, is_enabled, client_enabled, circuit_state, fail_count, opened_at, capability_kind ('dispatched'|'ingress'), read_scopes, write_scopes, authorization_mode ('contact')`.

Add only (authoritative DDL: LG11 M04):
```sql
ALTER TABLE capability_registry
  ADD COLUMN impl_kind text NOT NULL DEFAULT 'n8n',   -- n8n | python | mcp | http | n8n_webhook (default keeps existing rows working mid-migration)
  ADD COLUMN impl_ref text,                            -- 'order_agent' | 'http://csa_mcp:8001/...' | webhook endpoint id
  ADD COLUMN routing_hint text;                        -- one line used to generate the per-agent decision question
ALTER TABLE capability_registry
  ADD CONSTRAINT capability_registry_circuit_state_check CHECK (circuit_state IN ('closed','open','half_open'));
```
- `workflow_id` was NOT NULL: LG11 M04 relaxes it (python/mcp agents have no workflow); keep the legacy id on existing rows until cutover.
- Use **`risk_class`** (not a new column) to drive policy: `low` = read-only, `medium` = reversible write, `high` = irreversible/monetary. Retries and approvals key off it.
- `read_scopes`, `write_scopes`, `authorization_mode='contact'` are your least-privilege model. Enforce in the spoke wrapper: a `contact`-scoped agent gets a `TenantTx` whose queries must filter by `contact_id` (assert in tests); declared scopes are checked in code review/CI.
- `pattern` and `capability_kind='ingress'`: check which values you actually use (e.g. the voice gateway as an ingress capability) and preserve them.
- **Circuit breaker (implemented since this original discovery).** `csa/agents/breaker.py` tracks thresholds and half-open probes; `PostgresBreakerStore` persists state and queues a deduplicated `circuit_opened` staff notification on open. `tests/test_staff_path.py::test_open_circuit_queues_one_staff_alert` covers the notification; retain the original design thresholds when changing this behavior.
- **Source of truth for input schemas is the Pydantic `Input` class in code**; a startup sync writes `input_schema` JSON into the row so admin tooling and MCP clients can read it. A CI check fails if they drift.

### 6. Loader
`registry.load(client_id)` returns enabled capabilities where `is_enabled AND client_enabled AND circuit_state <> 'open'`, cached per client for 30 s. Planner/Jev questions are generated from this list (LG06) — adding an agent never touches a prompt.

## Acceptance criteria
- [x] All models import with no LangGraph dependency; `pyright` clean. Evidence: `tests/test_import_boundary.py`; `make lint`.
- [x] Current n8n order list/status/search/draft/confirm/cancel output shapes validate in `tests/test_contracts.py`; identifiers and customer details are synthetic. Intentional boundary normalization keeps legacy `callback_data` separate from compact `action_id` and extracts private `_approval` data into a server-only typed payload.
- [x] `AgentResult.model_json_schema()` snapshot test committed. Evidence: `tests/test_contracts.py::test_agent_result_schema_matches_snapshot`.
- [x] Callback codec: expired, reused, and wrong-contact ids all resolve to `None` (tests). Evidence: `tests/test_callbacks.py`.
- [x] Registry loader respects `circuit_state` and client enablement. Evidence: `tests/test_registry.py` uses generated two-tenant test rows; the shared LG08 demo seed does not exist yet.

## LG02 implementation notes

- Contracts live in `csa/contracts/`; only the graph package may depend on LangGraph.
- `PendingActionStore` requires `client_id` as well as the action, conversation,
  and contact ids so every callback lookup runs inside the existing
  transaction-local tenant RLS scope. `CallbackCodec` emits only `a:<uuid>`.
- Callback inserts verify that the contact and conversation belong to the same
  client. Expired actions are marked `expired` when resolved; consumption uses
  one conditional `UPDATE ... RETURNING` so concurrent taps cannot both win.
- The registry reads only enabled/client-enabled rows whose circuit is not
  `open`, caches per client for 30 seconds, and can sync `input_schema` from a
  Pydantic model. New implementation columns have model defaults for the
  pre-LG11 schema.
- Circuit-breaker transitions remain in the LG05 spoke wrapper; LG02 supplies
  the typed registry state and loader only.
- LG02 adds no migration. The pending-action status CHECK and capability
  registry DDL remain in LG11 M03/M04, which own the ordered schema-change
  inventory and the production-data prechecks.
- Integration tests use disposable Postgres containers and synthetic tenant
  rows. No server database, production credentials, or customer data were
  used.
- `AgentResult` accepts the current `data.facts` / `data.ui` wire envelope and
  exposes `facts`, `ui`, and `suggested_actions` as typed accessors for the
  coordinator. The supplied list example is covered with synthetic order data
  in `tests/test_contracts.py`.
- Legacy UI `callback_data` is stored distinctly as `legacy_callback_data`, not
  treated as a compact `action_id`. LG07 must replace it with a `PendingAction`
  and compact `a:<uuid>` token before sending buttons; raw legacy values must
  never be forwarded to a channel. `ResultMeta.was_duplicate` is optional
  because it appears on some current n8n results but not all.
- The n8n `_approval` block is parsed into `AgentWireResponse.approval`, a
  separate server-side typed payload. It is excluded from default serialization
  and object repr so order/customer details do not enter customer-facing facts
  or routine logs; LG07 owns its approval workflow.

## Pitfalls
- Don't let optional fields creep in "just in case" — missing data should be a validation error, not a silent `None`.
- Keep `Fact.text` English (as today) and translate in the composer; ids/amounts/SKUs must never be translated.
