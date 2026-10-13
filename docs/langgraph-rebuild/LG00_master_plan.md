# LG00 — CSA Platform Rebuild: LangGraph + Jev (System One) — Master Plan

> Numbering note: `LG00–LG11` is separate from your existing build plan `00–13` so nothing collides in the repo.
> Each phase file starts with a **CONTEXT FOR CODING AGENT** block so it can be pasted straight into Claude Code as a prompt.

---

## 1. Verdict (no hedging)

**Go full LangGraph (Python) for the runtime. Demote n8n to a peripheral. Do not build the "hybrid" where LangGraph calls n8n sub-workflows inside the conversation path.**

- Today n8n is the **brain** (ingress → coordinator → spokes → egress are all n8n workflows calling each other). That is the part to replace.
- n8n stays only for things it is genuinely good at: per-client custom automations you sell as an agency, glue to OAuth-heavy SaaS, and low-risk scheduled jobs. The platform calls it through one signed `n8n_webhook` agent type (see LG05). It is never in the hot path of a customer turn.
- Your **database is the real asset** (45 tables, atomic idempotent SQL, registry, approvals, outbox tables). It carries over almost untouched. You are replacing roughly the orchestration layer, not "starting over".
- **Replies come from a normal LLM.** The composer in egress is a regular LLM (Groq `gpt-oss-120b` today, configurable per client, with a failover model). Jev never writes text; it only makes narrow decisions (LG06), and the platform runs fine on an LLM decision provider alone until you have Jev access. Verbatim/template replies are the *exception*, listed in LG07.

**Why not hybrid-in-the-middle:** two runtimes, two deploy pipelines, two failure modes, an extra network hop per spoke, and no types across the boundary. You inherit n8n's weaknesses *and* LangGraph's.

**Why not stay on n8n:** the four files you sent already show the ceiling (section 2). The problem is not raw throughput. It is correctness, testability and change velocity — and those get worse with every agent and channel you add.

**Honest performance truth:** the orchestrator is not your latency bottleneck. LLM calls are. Moving to LangGraph will not make a turn fast by itself. Speed comes from: (a) replacing LLM *decisions* with Jev/deterministic code, (b) skipping the planner LLM on turns that don't need it, (c) running spokes in parallel (n8n runs them serially), (d) skipping the composer LLM when facts are already customer-ready (you already do this for callbacks). Scalability comes from stateless workers, a queue, per-conversation ordering, and an outbox — all of which you must build; LangGraph does not give them to you.

**Sizing at your volume (~100 messages/day, growing):** 100/day is about 4 an hour; even with 10x bursts that is roughly one message a minute. n8n would carry that load without trouble, so **raw throughput is not why I recommend the rebuild** — correctness, testability, human-in-the-loop, multi-channel and change velocity are. A single worker process handles orders of magnitude more than you need (back-of-envelope in LG09). What will bind first as you grow is LLM-provider rate limits and outages, then your own review capacity — not the orchestrator. So: one Postgres, one gateway, one worker, one delivery worker. No Redis, no Kafka, no Kubernetes.

**Honest cost truth:** this is real work (estimate in section 7). LangGraph is a library, not a platform: you own the gateway, queue, workers, retries, observability. It also moves fast (1.x line has had many releases in 2026) — pin versions and confine it to one package.

---

## 2. Evidence from the four workflows you shared

Severity: **H** = breaks behaviour now, **M** = latent risk / blocks scale, **L** = hygiene.

| # | Sev | Finding | Where | Consequence | Fixed in |
|---|-----|---------|-------|-------------|----------|
| 1 | H | `Merge Handoff Result` reads `$('Handoff Decision')` but the node is named `Handoff Decisions` | 10_coordinator | Every handoff path (angry customer, `human_request`, agent failure) should throw before the composer → silence. Stringly-typed node refs, no compile step. *(Verify in your instance.)* | LG04 |
| 2 | H | Telegram Trigger exports `updates: ["message"]` only | 01_ingress | `callback_query` (Confirm/Cancel/pick buttons) never arrives if the export is accurate. *(Ignore if buttons already work for you — export may be stale.)* | LG03 |
| 3 | H | `Normalize Callback` only accepts `confirm`/`cancel`/`pick`; `Wrap Products` emits `ord:buy:*` and `ord:browse` | 01 vs 21 | "Order 1 / 2 / 5" buttons are dead; `Build Callback Plan` supports `create_draft` but nothing produces it | LG05 |
| 4 | M | Draft idempotency key reads `input.coordinator_base?.idempotency_key`; `Prepare Tasks` never forwards `coordinator_base` | 10 vs 21 | Falls back to a random UUID every time → idempotency silently off | LG05 |
| 5 | M | `Risk Score` reads `client.auto_send_threshold` (never set → 0.75); `confidence` hard-coded `0.85` in `Normalize Composed`; `auto_send_by_intent` / `refund_auto_approve_limit` loaded but never enforced | 01, 30 | The risk gate is mostly decorative; client policy config does nothing | LG07 |
| 6 | H | Approval path is a dead end: row in `approvals` + text to ops chat; nothing in these files resumes it | 30 | No real human-in-the-loop | LG07 |
| 7 | H | Secrets/IDs hard-coded: Telegram bot token inside the `Send Telegram` URL; ops chat id in two workflows; `CORE_AGENTS` workflow IDs in code | 30, 21, 10 | **Rotate the bot token now** (BotFather → revoke). Breaks on import/export and multi-env | LG01 |
| 8 | M | Spokes run through `SplitInBatches` | 10 | Latency = sum of spokes, not max | LG04 |
| 9 | M | Tenant resolved with `WHERE ct.telegram_id=$1 LIMIT 1`; unknown contact throws "Client not found" | 01 | New customers get silence; wrong client possible if one Telegram id exists under two clients | LG03 |
| 10 | M | Egress is Telegram-only; `outbound_deliveries` / `_attempts` / `dlq` unused; "claim then send" with no recovery | 30 | No retries; a failed send blocks its own retry; every new channel = clone the workflow | LG07 |
| 11 | M | Planner prompt hard-codes order/booking/ticket rules (order + booking duplicated twice, stray `)`) | 10 | Every new agent = edit a mega-prompt; tokens wasted; coupling | LG04/06 |
| 12 | L | Copy-paste: Normalize/Attach Client ×2 (message vs callback), `makeUuid`/`isBusinessHours` ×2, `money`/`day` helpers repeated | 01, 21 | Drift | LG02 |
| 13 | M | Language: `11_context` overrides ingress `detectLanguage` (good, handles zh/ta by script) but Malay detection is raw substring matching — `tak` and `mana` match inside English words ("take", "manager"), so two such words flip English text to Malay; the detector never returns `other`, so the unsupported-language escalation is unreachable (Thai → English); `Save Locale` reads `plan.language`, which the planner never emits, so `contacts.locale` is never written | 11, 01, 30 | Wrong reply language; dead escalation branch | LG06 |
| 14 | L | Persona "EmpatTingkat" hard-coded in composer for all clients | 30 | Tenant brand leak | LG07 |
| 15 | L | `ord:confirm:ORD-XXXXXXXX:cfm_<32hex>` = 61 of Telegram's 64-byte `callback_data` limit | 21 | No headroom for any new field | LG02/05 |
| 16 | L | Ops/support chat ids in code; `isBusinessHours` fixed to KL 09–18 | 21, 30, 01 | Not per-client | LG03 |
| 17 | M | Escalation mute is written but never read: `23` sets `status='escalated'`, `owned_by='human_support'`, `ai_muted_until=+24h`, and no workflow reads any of them to suppress replies. *(Reopening on the customer's next message is already blocked by your `trg_keep_escalated` trigger — confirmed from `\sf keep_escalated_status`: it keeps `escalated` while `owned_by='human_support'` and the mute is unset or unexpired; a deliberate release must change `owned_by`.)* | 23, 11, DB | After handing a customer to a human, the bot keeps answering | LG04 |
| 18 | M | `clarify_count`, `conversations.summary`, `sentiment` and `last_intent` are never written (egress only increments `turn_count`); `clarify_count` and `summary` are read | 10, 11, 30 | The "clarify once, then escalate" cap never fires; rolling summary always empty; two columns are dead | LG04/07 |
| 19 | M | `capability_registry.circuit_state / fail_count / opened_at` are never written by any of the ten workflows (only filtered on in `10`); your `pg_trigger` list has six user triggers and none is on `capability_registry` or `agent_runs`; `pg_cron` is not installed | 10, DB | The circuit breaker has no writer (the only unread code is the body of the four `csa_*`/`purge_*` functions, unlikely to touch it) | LG02/05 |
| 20 | M | `Recent Orders` (11) has no status filter and `ORDER BY placed_at DESC` — Postgres sorts NULLs first on DESC, so drafts come first and cancelled orders are included (your own `orders_recent_idx` is `placed_at DESC NULLS LAST`, so the query doesn't even match the index); feeds the planner and the clarify facts | 11, 10 | "Order X is draft" presented as your orders | LG04 |
| 21 | M | Raw PII flows back into prompts: egress stores the rehydrated reply in `messages.body` with no `redacted_body`, and `Recent Turns` reads `COALESCE(redacted_body, body)` | 30, 11 | Next turn's LLM sees raw PII from your own replies | LG04/07 |
| 22 | L | Ticket cancel confirmation is an LLM-supplied `confirm:true` (orders use server-issued tokens); `pending_actions` exists but no workflow uses it | 22 | Weakest confirmation in the system | LG02/05 |
| 23 | L | Ticket agent builds SQL as strings in JS and runs `{{ $json.sql }}`; staff notifications are split between hard-coded chat ids (21, 30) and `40_notify_staff` (23) | 22, 21, 30, 23 | Untestable; inconsistent notifications | LG05 |
| 24 | L | `11_context` runs 8 sequential queries; its new-contact path is unreachable for Telegram (ingress joins existing contacts first); a `web_console` channel with `actor_type` exists that isn't on your channel list | 11, 01 | Latency; a hidden fifth channel | LG03/04 |
| 25 | H | **Reservation accounting is wrong in all three places that touch it.** `Create Draft` (reserve), `Cancel Reservation` and `Expiry Sweep` (release) all use `UPDATE products … FROM <rows>` where several source rows can match one product; Postgres applies only one of them. Two lines of the same SKU in one draft, or several expiring orders for one product, are miscounted. `Create Draft` also checks each line against `available_qty` separately, so a split order (A×2 + A×2 with 3 available) passes and oversells | 21 | `reserved_qty` drifts (the `>= 0` CHECK and `GREATEST(…,0)` hide the downward direction); oversell possible | LG05/LG10 |
| 26 | H | **The global sweep is reachable from a customer turn.** Router output 7 is both the `fallback` action and the switch's fallback output, and `Input` doesn't validate `action`, so any unknown action runs the non-tenant-scoped sweep. The 5-min cron that should call it isn't in the files you sent | 21 | Unintended global writes; sweep returns raw counts as a "result" | LG05/10 |
| 27 | M | Sweep ignores `pending_confirmation` orders (stock stays reserved while staff haven't decided) and drafts without a confirmation row; the customer is never told a draft expired | 21 | Indefinite stock holds | LG10 |
| 28 | H | **Voice transcripts skip PII redaction:** `Rebuild Voice Envelope` sets `body_redacted = transcript` and the envelope's `pii_map` is empty | 02 | Raw PII from voice notes goes to the planner/composer and into `messages` | LG03 |
| 29 | M | Voice jobs have no failure path: `Submit to Voice Gateway` has no error branch and nothing times out a job stuck in `processing`; gateway URL is the Docker bridge IP `172.17.0.1:8082`; the callback goes out through the public n8n URL; the submit path skips `ingress_dedup` | 01_voice, 02 | Silent customer on any voice-gateway hiccup; duplicate jobs on Telegram retries | LG03/10 |
| 30 | H | **Voice callback bearer token is hard-coded in a Code node** (and compared with `!==`) | 02 | Anyone with the JSON can forge transcripts. Rotate it; new callback is an internal, per-job HMAC | LG03/09 |
| 31 | M | `40_notify_staff` hard-codes one ops chat for every client; failures go to `dlq`, which nothing in your files consumes; the `action_log` write happens after sending, so it doesn't prevent duplicate alerts, and is skipped when there is no conversation id | 40 | One client's escalations reach your chat; failed alerts rot silently | LG05/07 |
| 32 | M | `messages.idempotency_key` is **globally** unique but keys are `tg:{chat}:{msg}(:in/:out)`; the same person using two clients' bots can collide, and `ON CONFLICT DO NOTHING` then silently drops the second message record | 01, 11, 30 | Missing message history | LG02 |
| 33 | L | `trg_webhook_delivery` → `enqueue_webhook_delivery()` is **scoped to `channel='webhook'` conversations** whose endpoint has a `callback_url` (sync endpoints get a 10 s delay so the sync reply can win); Telegram and other channels are untouched. It swallows its own exceptions (`RAISE WARNING`), so a failure leaves an outbound message with no delivery row | DB | Webhook callbacks can vanish silently; the new outbox leaves webhook enqueueing to the trigger | LG07/LG10 |
| 34 | M | `orders.id` is `'ORD-' + 8 hex chars` (32 bits) and is the **global** primary key; `Create Draft`'s `ON CONFLICT` only covers the idempotency index, so an id collision aborts the statement | 21, DB | ~50 % collision chance around 77k orders (birthday bound) — far off at your volume, trivial to fix | LG05/LG11 |
| 35 | M | `Create Draft` hard-codes `channel='telegram'`; `orders_channel_chk` allows only `telegram, web, tiktok_shop, shopee, manual`; `order_confirmations.channel` defaults to `telegram` | 21, DB | WhatsApp/email orders are mislabeled or rejected | LG05/LG11 |
| 36 | L | Housekeeping: duplicate indexes on `products` (`products_name_trgm` / `products_name_trgm_idx`, `products_cat_idx` / `products_category_idx`); `tickets` has no CHECKs on status/priority/category (validation exists only in JS); `voice_transcripts.callback_attempts` and `message_id` are never written; `idx_tickets_sla` covers only `status='open'` | DB | Write overhead, invalid values possible, dead columns | LG11 |

> **Caveat on #25–#27.** I judged the sweep from the node inside `21_order_agent`. Your database also has `csa_expire_drafts()`, `csa_expire_pending_approvals()`, `csa_retention()` and `purge_ingress_raw()` — probably the sweep that is *meant* to run on a schedule (there is no `pg_cron`, so something else would have to call it). Findings #25 and #26 still hold for the node and for `create_draft`/`cancel`; #25–#27 must be re-checked against those function bodies (they arrive with the schema dump, §9). If nothing calls them, **drafts never expire in production today.**

**What is genuinely good — keep it:**
- The envelope + `AgentResult { ok, status, data.facts[], data.ui, error{code, category, retryable, customer_safe_hint} }` contract.
- "Facts are the only business details the composer may state" + deterministic grounding check.
- PII redaction at ingress, rehydration at egress (the LLM never sees raw PII).
- Atomic, idempotent SQL (the `Create Draft` CTE is the best code in the repo), confirmation tokens, reservations with expiry.
- Policy-beats-model overrides (`HARD_ESCALATE`, clarify cap, never-auto intents).
- `capability_registry` with `circuit_state`; `deployment_mode` (`suggest` default).
- Escalation only promises a human follow-up when a ticket exists or staff were actually notified; it reuses an open escalation ticket per conversation.
- Ticket agent: duplicate guard, atomic mutate-plus-audit-event SQL, "can't buy a 1-hour SLA by shouting".
- The table design for queue / outbox / DLQ / approvals / pending actions already exists — the rebuild mostly *uses* it.
- Voice: async job + callback with an idempotent `WHERE status='processing'` update; `40_notify_staff` aggregates per-target outcomes and keeps PII out of audit/DLQ payloads.

---

## 3. Principles (non-negotiable)

1. **Postgres is the system of record.** LangGraph checkpoints hold only in-flight turn state.
2. **One inbound event = one graph run.** `thread_id = trace_id`. Do *not* use LangGraph as your chat-memory store; `messages` / `conversations` already are.
3. **Code holds control flow.** Policy in code, narrow judgments in Jev, language in the LLM.
4. **Every side effect is idempotent and goes through the outbox.** LangGraph is at-least-once; design for replays.
5. **Channel-agnostic core.** No `telegram_id` below the adapter layer.
6. **Tenancy enforced by the database (RLS)**, not by remembering `WHERE client_id = …`.
7. **Typed at every boundary** (Pydantic v2); JSON Schemas generated from code.
8. **LangGraph is confined to `csa/graph/`.** Domain code is framework-free so you can swap the engine later.
9. **Config over code per client:** persona, policy, thresholds, ops chat, enabled agents, runtime mode.
10. **Log every decision with the model version** so thresholds can be recalibrated.

---

## 4. Target architecture

```
 Telegram ─┐                                              ┌─► Telegram adapter ──► Bot API
 WhatsApp ─┤  FastAPI Gateway                             ├─► WhatsApp adapter ──► Cloud API
 Gmail ────┤  verify • normalize • tenant-resolve         ├─► Gmail adapter ─────► Gmail API
 Webhooks ─┘  • dedupe • persist • ack (<200ms)           └─► Webhook adapter ───► client URL
                  │                                                ▲
                  ▼                                                │ retries • attempts • DLQ
           ingress_events (queue)                          Delivery worker (outbox)
                  │ SKIP LOCKED + per-conversation lock            ▲
                  ▼                                                │
   ┌──────────── Turn worker: LangGraph (one run per event) ───────┴───┐
   │ load_ctx → guard → signals (Jev batch) → route                      │
   │   ├─ fast paths (callback, empty, voice-fail, hard-escalate, chitchat)
   │   └─ plan_args (small LLM, ONLY chosen agents' schemas)             │
   │        → fan-out Send → spokes in parallel → collect                │
   │ handoff → compose (LLM or verbatim) → verify → gate                 │
   │        └─ interrupt() → ops approves/edits/rejects → resume         │
   │ emit → outbound_deliveries (same tx) → finalize                     │
   └──────────────────────────────────────────────────────────────────────┘
   Postgres: domain tables • lg.* checkpoints • decision_log • RLS
   Side services: TEI embeddings • FastMCP tools • n8n (peripheral only)
```
Google Calendar is a **booking-agent/tool integration**, not an ingress
channel: the booking spoke uses the Google Calendar API through a scoped OAuth
connection, with availability checks and idempotent event writes. Gmail and
Calendar may share a Google Cloud project and OAuth client, but request
separate least-privilege scopes and store each mailbox/calendar connection
token encrypted per tenant.

---

## 5. What stays / goes / changes

| Thing | Fate |
|---|---|
| Postgres schema, SQL, seeds, migrations | **Keep.** Add: RLS, `decision_log`, small registry columns, `wa_templates` |
| n8n `01/10/30` workflows | **Replace** (keep running until cutover, then retire) |
| n8n spokes `21/22/…` | **Port to Python** (order/ticket are deterministic SQL — plain functions) |
| `csa_mcp` FastMCP server | **Keep**, wrap as `mcp:` agent kind |
| TEI embeddings + pgvector | **Keep** (kb_agent) |
| Groq models | **Keep** behind a provider interface; planner moves to a smaller model |
| Prompt Guard 22M + regex | Keep as a *second opinion*; primary = Jev Noul (LG06) |
| n8n | Peripheral: client-specific automations, SaaS glue, low-risk crons |

---

## 6. Key technology decisions

| Decision | Choice | Rejected (why) |
|---|---|---|
| Language | Python 3.12 | TypeScript (your MCP server + ML tooling are Python; LangGraph Python is the primary SDK) |
| Orchestration | LangGraph 1.x, Postgres checkpointer | Temporal (new infra for your scale); hand-rolled FSM (re-builds interrupt/checkpoint/fan-out) |
| DB driver | psycopg 3 async everywhere | asyncpg (checkpointer needs psycopg; two drivers = two pools) |
| Data access | Raw `.sql` files + Pydantic row models | ORM (you have excellent SQL; ORMs would hide it) |
| Queue | Your existing `ingress_events` + `FOR UPDATE SKIP LOCKED` | Kafka/RabbitMQ/Redis (one more thing to run; add an abstraction, swap to SQS on cloud only if needed) |
| Web | FastAPI + uvicorn | — |
| Observability | OpenTelemetry spans + your Postgres tables first; add a backend later (LangSmith/Langfuse cloud with redacted data, or Jaeger/Tempo) | Self-hosted Langfuse (ClickHouse/Redis/object storage — too heavy for one box at this volume) |
| Decisions | `DecisionProvider` interface; **LLM provider first**, Jev added in shadow when you have access | Jev as a hard dependency (early access, no GA date) |
| Deploy | Docker Compose now | Kubernetes (premature for a handful of clients) |

---

## 7. Phase map

| Phase | File | Outcome | Rough effort* |
|---|---|---|---|
| 0 | (below) | Stop the bleeding | 1 evening |
| 1 | LG01 | Repo, compose, DB layer, RLS, tracing, CI | 3–5 days |
| 1b | LG11 | **Schema change inventory** — ordered, additive migrations (RLS staged so n8n keeps working) | 2–3 days |
| 1c | LG12 | **Dev environment**: server-hosted isolated dev, local DB from a schema-only dump, Telegram webhook/polling options, CI, deploy to the home server | 0.5–1 day |
| 2 | LG02 | Contracts, state, registry, callback codec | 3–4 days |
| 2b | LG10 | **Scheduled jobs: remake the expiry sweep**, reapers, reconciliation — isolated, so it is the best first pilot of the new stack and fixes a stock-drift bug | 3–5 days |
| 3 | LG03 | Gateway + Telegram adapter + outbox "echo bot"; then separate increments for official WhatsApp Cloud API, Gmail API/Pub/Sub, and generic webhooks | ~1 week for Telegram; budget 1–2 weeks/channel plus external approval lead time |
| 4 | LG04 | Coordinator graph with fake deciders | ~1 week |
| 5 | LG05 | Spoke framework + order agent port + callbacks; booking agent uses Google Calendar API | ~1 week plus OAuth/API integration |
| 6 | LG06 | Decision layer: LLM provider first; Jev added in shadow when your access arrives | ~1 week |
| 7 | LG07 | Compose/verify/gate/HITL/outbox delivery | 1–1.5 weeks |
| 8 | LG08 | Tests, seed, evals (runs alongside 3–7) | continuous |
| 9 | LG09 | Cutover, ops, cloud path | 3–5 days |
| 13 | LG13 | Agent catalog (kb, invoice, booking, email, web research, subscription, teach-a-task) and web-console API contract; starts after LG07 | per agent; console API runs in parallel |

\*Part-time (~10–15 h/week), single developer, **estimates with wide error bars**.
Telegram parity + order agent + HITL ≈ 7–9 weeks; WhatsApp, Gmail, Google
Calendar, and the remaining spokes add implementation and external-review
time. Start Meta and Google Cloud/OAuth onboarding early: business verification,
number registration, OAuth verification, and any required security assessment
are calendar-time dependencies, not coding tasks. If time is tight, ship
phases 1–9 on Telegram only and add channels and integrations one at a time.

**Phase 0 (do today):**
1. Rotate the Telegram bot token (it is inside the `Send Telegram` URL in `30_egress.json`); move to an n8n credential/env var. Treat any place that JSON was shared as exposed.
2. Optional 1-line fix so the current bot survives the rebuild: rename the reference in `Merge Handoff Result` to `$('Handoff Decisions')`.
3. Freeze new n8n features. Bugs only.
4. Rotate the **voice-callback bearer token** hard-coded in `02_livekit_transcript_callback`.
5. Run the read-only reservation reconciliation (LG10 §4) to see how much stock is currently stuck in `reserved_qty`.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| LangGraph API churn (many 1.x releases in 2026) | Pin exact versions; only `csa/graph/` imports it; upgrade deliberately |
| Checkpoint volume / state bloat | Small state (ids, not histories); retention job (LG01); `durability` chosen per run |
| At-least-once replays double-fire side effects | Idempotency keys on every write + outbox (LG05/07); `interrupt()` re-runs its node from the top — keep pre-interrupt code side-effect-free |
| Jev is early access, no GA date, vendor-reported benchmarks, documented weak spots (dates, numbers, literal reading) | Provider interface + LLM fallback; Jev never extracts args, dates, or numbers (LG06) |
| Rebuild stalls half-done | Strangler cutover per client (LG09); n8n keeps serving un-migrated clients |
| Solo-dev bus factor | Tests + seed + evals (LG08) are part of "done", not extras |

---

## 9. What I still can't see

**Verified from your `db_output.txt`:** extensions are `pg_trgm, plpgsql, postgis 3.4.3, uuid-ossp, vector 0.8.6` — **no `pg_cron`, no `pgcrypto`** (so scheduling lives in the worker, LG10, and credential encryption is app-level, LG01 §6); exactly six user triggers (`trg_keep_escalated`, `trg_webhook_delivery`, `tickets_set_updated_at`, `trg_sync_order_items`, `trg_webhook_endpoints_updated`, `trg_contact_identity_tenant`); and four functions nobody has shown me: **`csa_expire_drafts`, `csa_expire_pending_approvals`, `csa_retention`, `purge_ingress_raw`**.

Still missing, none of it blocking:
1. **One schema-only dump** replaces every remaining `\d+`/`\sf` request and contains those four function bodies:
   `docker exec csa_db pg_dump -U csa -d csa --schema-only --no-owner --no-privileges > csa_schema.sql`
2. **Who calls those functions?** An export of *all* n8n workflows, searched for `csa_expire`, `csa_retention`, `purge_ingress_raw` and any Schedule trigger (commands in LG10 §0). If you really have no staff-approval or cron workflow, LG05 now defines the staff-approval flow as new work and LG10 owns the schedule.
3. `clients.config` and the registry rows (queries in LG12 §7), and a few redacted sample messages (synthetic is fine).
4. Which STT model/provider sits behind the voice gateway on `:8082` (it decides which languages work), and whether you have its source.

Where a file says *(verify)* it depends on those. The Jev key is arriving soon; LG06 has a day-one checklist.

---

## 10. How to use these files with a coding agent

Open a session in the repo and paste one phase file at a time:

> "Read LG00_master_plan.md for principles and decisions, then execute LG0N. Do the tasks in order, run the acceptance checks, and stop and report if an assumption in the file is false (especially anything marked *verify*). Do not start the next phase."

See **LG12** for the dev ↔ production workflow on the home server, the exact session prompts and the definition of done for each agent session.
