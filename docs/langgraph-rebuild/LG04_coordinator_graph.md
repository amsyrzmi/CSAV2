# LG04 — Coordinator graph (the LangGraph part)

## CONTEXT FOR CODING AGENT
Implement the per-turn graph in `csa/graph/` (the only package allowed to import `langgraph`). It replaces `10_coordinator`, `11_context`'s orchestration role, and the planner/validation/handoff nodes. In this phase all *deciders* (Jev, LLM planner, composer) are **fakes behind interfaces** so the graph can be tested deterministically; real ones arrive in LG06/LG07. Port logic from the n8n Code nodes into pure, unit-tested functions under `csa/graph/policy/`. Read `LG02` first.

## Goal
`await run_turn(envelope)` executes one inbound event end-to-end with: parallel spokes, deterministic policy overrides, per-node timeouts, a fail-safe path (never silence), and checkpointed durability.

## Implementation status
The coordinator and policy modules are implemented as injectable, fake-backed LG04 scaffolding. The graph is not wired into the running worker; that remains on the verified LG03 path until the LG07 persistence, approval, and egress boundaries are ready. Focused tests cover routing precedence, order-status resolution, plan validation, parallel fan-out, human handoff, muted conversations, one regeneration, fail-safe routing, and in-memory synchronous checkpointing.

The acceptance checklist below remains authoritative. In particular, database-backed restart/idempotency, full golden-case coverage, failure recovery across every side-effect boundary, the 50 rps gateway load test, orchestration p95, and provider-backed completion are not established by the focused LG04 tests. Do not mark those criteria complete until their specified integration evidence exists. The isolated dev database was reset with user approval and now contains the sandbox tenant seed rows; live Telegram credentials and prior verification events were cleared.

## Graph

```
START → load_ctx → guard → signals → route
route ─┬─ fast_plan ──────────────┐   (callback, empty msg, voice-failed, hard-escalate, chitchat)
       ├─ force_escalate ─────────┤   (guard blocked / unsupported language)
       └─ plan_args → validate ───┤   (LLM fills args for the chosen agents only)
validate ─ fan_out (Send per task) → run_spoke ∥ → collect
no tasks ──────────────────────────→ collect
collect → handoff → compose → verify ─┬─ (fail, regen_count<1) → compose
                                      └─ gate ─┬─ auto → emit → finalize → END
                                               └─ review → approval (interrupt) → emit → finalize → END
any node error → fail_safe → emit → finalize → END
```

## Node responsibilities (inputs → outputs)

| Node | Does | Port from n8n |
|---|---|---|
| `load_ctx` | Replaces `11_context` (8 sequential queries today). Run the independent reads concurrently (`asyncio.gather`): contact + conversation upsert (**never reset `status`/`owned_by` blindly** — see port notes), client config (languages, timezone, policy), last 6 turns via `COALESCE(redacted_body, body)` — **models only ever read redacted text** —, open tickets, **active** orders (`status NOT IN ('draft','cancelled')`, `ORDER BY placed_at DESC NULLS LAST`, limit 10), upcoming bookings, pending confirmations. Persist the inbound `messages` row early (before any model call). Detect language (LG06). Budget check (`clients.tokens_per_day`, overridable by `config.budget.tokens_per_day`) → `ctx.degraded=true`. Client row also supplies `timezone`, `languages`, `brand_name/brand_tone`, `core_object` (which spokes make sense) and `deployment_mode` (default `shadow`) | `11_context`, `Load Client` |
| `guard` | Primary: Jev `injection` Noul (LG06). Secondary: Prompt-Guard model + `OBVIOUS` regex. Output `GuardResult{blocked, score, source}` | `Injection Guard`, `Guard Result` |
| `signals` | One batched decision call (LG06): intent, language, sentiment, urgency, wants_human, per-agent `needs:*`, chitchat, typed_confirmation | (new — replaces LLM sentiment/intent) |
| `route` | Pure function → next node. Precedence: **conversation muted by a human** → callback → empty → voice-failed → hard-escalate regex → guard blocked → unsupported language → chitchat → agents needed → clarify | `Is Callback Turn?`, `Has Guard Text?`, … |
| `fast_plan` | Builds a `Plan` without any LLM (`source="fast_path"`). Callback → direct `order_agent` task from the codec payload | `Build Callback Plan`, `Handle Empty Message`, `Force Voice Escalate` |
| `plan_args` | LLM structured output using **only the selected agents' `Input` schemas**; deterministic **resolvers** pre-fill from context (e.g. "where is my order": 1 active order → `status` with that id; several → `list` + option buttons; none → fact "no orders"). Dates resolved in code (`dateparser`/own util, client timezone) — **not** by Jev, not by the LLM | `Planner`, `Prep Planner Input` |
| `validate` | Pure `policy/plan_rules.py`: ≤3 tasks, valid agents, required inputs present; HARD_ESCALATE regex (extend with Malay/Chinese/Tamil terms); clarify cap (`clarify_count>=1` ⇒ escalate); confidence floor; account-intent-without-task ⇒ clarify; ticket intent normalization | `Validate Plan` |
| `fan_out` | Conditional edge returning `[Send("run_spoke", {...task, env}) ...]` | `Loop Over Items` (serial today) |
| `run_spoke` | LG05 wrapper: registry resolve → timeout → retries (only if `retryable` and idempotency key present) → circuit breaker → `agent_runs` log. Returns `{"results":[AgentResult]}` | `Execute Workflow`, `Ai Not Implement` |
| `collect` | Merge results: namespace fact ids `task_id:fact_id`, merge `ui`, aggregate failures, `requires_human`; if `ask_clarifying` on account intents with no tasks, inject recent-order facts (your `Empty Facts` logic) | `Collect Results`, `Empty Facts` |
| `handoff` | Pure `policy/handoff.py`: reasons from plan, agent `requires_human`, hard failures (non-benign categories), `human_request`, angry sentiment (config-driven). If needed and not yet escalated → call `escalation_agent` through the same spoke wrapper, merge facts/failure | `Handoff Decisions`, `Build Escalation Input`, `Merge Handoff Result` |
| `compose` … `finalize` | LG07 | `30_composer_egress` |
| `fail_safe` | Any unhandled error → deterministic bilingual-safe apology (per-client template), create escalation ticket, mark turn `failed` for ops | (new) |

## Port notes from `11_context`, `22`, `23` (findings that change the design)
1. **Escalation mute is written but never read.** `23_agent_escalation` sets `conversations.status='escalated'`, `owned_by='human_support'`, `ai_muted_until = now() + 24h`. Nothing in the ten workflows reads `owned_by`, `ai_muted_until` or `status` to suppress a reply, so the bot keeps answering a conversation it handed to a human. *(Confirmed from `\sf keep_escalated_status`: your trigger already stops `11_context`'s `SET status='open'` from reopening an escalated conversation while `owned_by='human_support'` and the mute is unset or unexpired. Keep it as defense in depth.)* Fix: `route` step 0 — if `owned_by='human_support' AND (ai_muted_until IS NULL OR ai_muted_until > now())` (the same condition the trigger uses): store the inbound message, notify the human owner (outbox; at most once per N minutes), send **at most one** templated acknowledgement per mute window, and **do not run planner, spokes or composer**. Release is deliberate: `UPDATE conversations SET status='open', owned_by=NULL` (allowed by the trigger because `owned_by` changes) from an ops "Return to AI" button or the `mute_expiry` job (LG10); `ai_muted_until IS NULL` means indefinite human ownership and is never auto-released.
2. **`clarify_count`, `conversations.summary`, `sentiment` and `last_intent` are never written** (egress only increments `turn_count`; `clarify_count` and `summary` are read). Write `sentiment` and `last_intent` from the plan in `finalize`. In `finalize`: increment `clarify_count` when the strategy was `ask_clarifying`, reset on a resolved turn — otherwise "clarify once, then escalate" never fires. Add a summarizer job (every N turns, small model, redacted text only) that writes `summary`.
3. **Recent orders are unfiltered** and `ORDER BY placed_at DESC` sorts NULL `placed_at` (drafts) **first** in Postgres, with cancelled orders included. Use the same filter as the order agent's `list` and cap at 10.
4. **Raw PII leaks back into model context.** `messages.body` holds raw text; egress `Log Outbound` stores the *rehydrated* reply in `body` with no `redacted_body`; `Recent Turns` reads `COALESCE(redacted_body, body)` → the next turn's planner/composer sees raw PII from your own replies. New rule: every message row is written with both `body` (raw; retention/encryption policy) and `redacted_body` (the only text models may read).
5. **`Save Locale` is dead:** it reads `plan.language`, a field the planner never emits. Persist the detected language explicitly (LG07 `finalize`); low-confidence detections fall back to the stored `contacts.locale` (LG06).
6. The early inbound-message insert in `11_context` is correct (persisted before any LLM) — keep that property.

## Implementation notes

```python
# csa/graph/build.py  (only place importing langgraph)
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send


def build_graph(deps: Deps, checkpointer):
    g = StateGraph(TurnState)
    g.add_node("load_ctx", with_timeout(load_ctx, 2.0, deps))
    g.add_node("guard", with_timeout(guard, 3.0, deps))
    ...
    g.add_edge(START, "load_ctx")
    g.add_edge("load_ctx", "guard")
    g.add_edge("guard", "signals")
    g.add_conditional_edges(
        "signals", route, ["fast_plan", "force_escalate", "plan_args", "collect"]
    )
    g.add_conditional_edges("validate", fan_out, ["run_spoke", "collect"])
    g.add_edge("run_spoke", "collect")
    return g.compile(checkpointer=checkpointer)


def fan_out(state: TurnState):
    tasks = state.plan.tasks
    return [
        Send("run_spoke", {"task": t, "envelope": state.envelope, "ctx": state.ctx}) for t in tasks
    ] or "collect"
```
- Run: `await graph.ainvoke(state, config={"configurable":{"thread_id": str(trace_id)}}, durability="sync")` — confirm the `durability` parameter name/values against your pinned LangGraph version. Use `sync` for turns with side effects; the extra write latency on local Postgres is small relative to model calls.
- **Idempotency is your responsibility.** LangGraph replays from the last checkpoint after a crash. Every spoke write already uses idempotency keys (LG05); `emit` writes the outbox with a unique key `{trace_id}:{seq}`.
- **Timeouts:** wrap each node with `asyncio.wait_for`; budgets (starting points): ctx 2 s, signals 2.5 s, plan_args 6 s, spoke per registry `timeout_ms`, compose 8 s, whole turn from `turn_deadline_ms`.
- **Retries:** LangGraph `RetryPolicy` for LLM/Jev nodes only. Never auto-retry a node with side effects unless it carries an idempotency key.
- **Pure policy modules** (`policy/*.py`) take plain Pydantic in/out and have exhaustive unit tests — this is where the "policy beats the model" behavior now lives.
- **Observability:** one OTel span per node; attach `plan.source` so you can see how often each path (fast / Jev / LLM) is used.
- Keep `route()` a pure function so the full routing table can be tested without running anything.

## Acceptance criteria
- [x] With fake deciders, golden cases (LG08) pass for: greeting, order status with 1 order / many / none, callback confirm, empty message, voice failure, prompt injection, lawyer keyword, angry customer, agent failure, unknown agent. _(verified: all listed golden cases in tests/test_coordinator_acceptance.py)_
- [x] Two spokes in one plan run in parallel (assert wall time ≈ max, not sum, using sleeping fake agents). _(verified: tests/test_coordinator_acceptance.py::test_parallel_wall_time_is_max_not_sum)_
- [x] Kill the worker between `collect` and `compose` → restart → turn completes once; no duplicate outbox rows. _(verified: tests/test_worker_coordinator.py::test_kill_between_collect_and_compose_resumes_without_duplicates on real Postgres saver + outbox)_
- [x] Forced exception in any node ends in `fail_safe` with an outbound message and an escalation record. _(verified: tests/test_worker_coordinator.py parametrized over load_context/guard/signals/plan_args/compose/verify/gate/emit with real escalations + outbox rows; finalize raises and retries from checkpoint instead, so the customer is never messaged twice)_
- [x] A handoff turn (angry customer) completes (this is the path that breaks today). _(verified: test_golden_angry_customer_hands_off)_
- [x] Orchestration overhead (excluding fake model latency) p95 < 150 ms locally — treat as a regression budget, not a promise. _(verified: 200 fake turns, graph compiled once; compile per turn costs ~100 ms so the worker must build once)_

## Pitfalls
- `interrupt()` (LG07) re-executes its node from the start on resume: keep everything before it side-effect-free, and put the interrupt in its own node.
- Don't stash big objects in state — checkpoints serialize it every step.
- Reducers: only `results` needs `operator.add`; if you add more parallel-written keys, define reducers explicitly or Send branches will clobber each other.
- Don't let a prompt decide something a regex/enum/policy function can.


### LG04 completion notes

- Worker wiring: set `TURN_ENGINE=coordinator` to run turns through the graph (default `echo` keeps the LG03 path). The graph is compiled once at worker start (`build_runtime`); `process_turn` starts, resumes, or returns the finished turn by `trace_id`.
- `csa/turn/deps.py` has real Postgres persistence (context, emit, finalize, fail-safe, escalation). Guard, signals, plan, compose, verify and gate are deterministic placeholders until LG06/LG07; the only spoke is `escalation_agent` until LG05.
- Migration `0150_escalations.sql` adds an idempotent, RLS-protected `escalations` table (one row per trace).
- Finalize failures re-raise (post-emit); fail-safe handles failures before emit.
