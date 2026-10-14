# Progress

| Phase | Status | Evidence |
|---|---|---|
| LG01 Foundation | Done | lint/tests green |
| LG02 Contracts, state, registry | Done | `tests/test_contracts.py`, `test_registry.py` |
| LG03 Ingress, worker, delivery | Done | ordered worker, outbox, reaper + DLQ, load test |
| LG04 Coordinator graph | Done | `test_coordinator*.py` |
| LG05 Spokes & tools | Done | order, approval, escalation, ticket; verified live on Telegram |
| LG06 Decision layer | Groq + Jev classifier modes implemented; wider threshold calibration pending | `test_llm_provider.py`, `test_decide_providers.py`, live synthetic comparison; dev cascade row verified |
| LG07 Egress / HITL gate | Policy gate + staff approval (approve/reject/expire), semantic verify, Gemini composer failover, delivery crash recovery, brand voice & verbatim callbacks | `test_gate_policy.py`, `test_staff_path.py`, `test_ingress.py` (Edit), `test_worker.py`, `test_llm_provider.py`, `test_coordinator.py`; staff Edit (button + text reply, audited) |
| Console visibility (agent timeline + read API) | Done (polling; see CONSOLE_API.md) | `test_console_timeline.py` |
| Agent contract + bounded agent loop | Done: risk classes per action, read-only `observe` loop | `test_coordinator.py` (loop), `test_llm_provider.py` (next_step, risk) |
| LG08 Evals/golden cases | Done: 60 decision evals, 33 whole-turn golden cases, 100% policy coverage | `test_decision_evals.py`, `test_golden.py`, `test_load_acceptance.py`, `docs/evals/lg08_validation_report.md`; edge-case matrix expansion (hard legal, sentiment, human handoff, duplicate SKUs, cancel multi-line, button confirmation requirement, clarify bounds, never_auto) |
| LG10 Scheduled jobs & expiry sweep | Done: Advisory-locked JobRunner, per-client RLS execution, aggregated per-product stock release, reservation reconcile & repair, reapers, property test (zero drift) | `tests/test_jobs.py` (6 passed), `csa/jobs/`, migration 0250 `job_runs` |
| LG09 Cutover & operations | Done: Strangler pattern gateway proxy, instant rollback, shadow mode, 8 operational runbooks, ops procedures | `tests/test_cutover_ops.py` (4 passed), `docs/runbooks/` (01-08), `csa/ops/runbooks.py`, `csa/channels/ingress.py` |
| LG13 Agents/Console & E-Commerce draft | In progress: Admin subscription toggles & specialized tabs contract updated, E-Commerce agent (TikTok Shop adapter + Shopee/Lazada stubs) drafted & tested | `tests/test_ecommerce_agent.py` (4 passed), `docs/agents/ecommerce_agent_draft.md`, `csa/agents/ecommerce/` |
| LG14 Teach-a-Task Virtual Desktop | Planned: Dedicated desktop container sandbox, user action recording, playbook compiler & scheduled replay | `docs/langgraph-rebuild/LG14_teach_task_desktop_agent.md` |

Verification on 2026-10-14: `make lint` passed (ruff, format, pyright, import-linter); `ENV=ci make test` **363 passed** (14m19s).
`make coverage`: 88 passed, 100% policy statement/branch coverage; CI enforces at least 90%.
`make db-reset` passed on a disposable isolated Postgres (0220, two seed tenants); the live dev
database was not reset. Gitleaks 8.24.2 source scan passed locally, network disabled, excluding
local secrets/dependencies/caches; no Git history scan was possible in this non-Git workspace.
Live: orders, Confirm, staff
alert, Approve, customer message, buttons cleared after tap, Resume AI.
Agent loop verified live (2026-10-12): a product
question ran `observe` step 1 and stopped with `done`; a draft with Confirm buttons skipped the loop;
the Confirm tap replied with the order facts verbatim (no greeting).

Dev schema 0220 and cascade policy are active. A live classification persisted
`cascade / jev / jev:jev-1.13.0` (1267 ms), then passed verify/gate and finalized.
Gateway `/readyz` returned ready after restarting gateway, worker and delivery.
The golden tests also exposed and fixed missing active-order context and the invalid multi-order
`list` action; status now uses the real agent's `status` action and excludes unplaced drafts.

The next LG08 slice added persisted approval checks (auto/suggest/first-contact/shadow), injection
followed by three muted turns and classifier timeout. Load evidence uses real ingress SQL and
the durable graph, with synthetic traffic and no external provider/delivery calls: 150 arrivals
at 50/s, pre-opened eight-connection ack p95 109.642 ms, schedule-to-ack p95 112.704 ms,
150 completed events and unique replies. Backlog drain was 2.051 turns/s; cold pool expansion
failed the 200 ms target. See [validation report](./evals/lg08_validation_report.md) for scope.

Latest slice adds wrong-contact/expired order confirmation, expired ticket cancellation,
cross-contact order lookup and multi-agent partial-result review. Two synthetic tenant scenarios
inserted in 0.238 s after migrations and proved product/order RLS isolation.

Real TCP now runs a separate Uvicorn gateway with runtime tracing and disposable database URLs.
Tracing batches compact server spans; contact resolution reduces SQL round trips; gateway-only
autocommit avoids implicit transactions for unscoped endpoint reads. Explicit tenant transactions
and eight-connection capacity remain intact. Tests cover concurrency, legacy repair, identity
conflict rollback and tenant GUC reset in both pool modes.

`make load-test` passed all three checks on unchanged repetition: TCP ack/scheduled p95
**108.808/110.330 ms**, ASGI **187.828/190.964 ms**, graph-only **97.839 ms**; both ingress paths
completed 150 turns with unique replies. Earlier failed experiments, including a post-fix ASGI
scheduling failure, are retained in the report. Only the isolated dev gateway was restarted;
`/readyz` is ready on 0220. Worker/delivery, live dev data, webhook and production were preserved.

Additional simultaneous diagnostic: four independent test-only worker runtimes completed
150 events, but only about 2.34 turns/s; latest ack p95 was 782.528 ms under database
contention. Checkpoint operations dominate worker time. This is not the production worker
topology and does not establish 50 turns/s. User decision: preserve synchronous per-node
checkpoints and plan cloud scaling against measured DB/worker capacity; no checkpoint semantics
or runtime worker concurrency were changed. Capacity notes and queue monitoring guidance are in
[RUNBOOK.md](./RUNBOOK.md).
Two spawned-worker integration tests pass on disposable Postgres: two processes process one
conversation in order exactly once, and a killed claimant releases its session lock for reaper
recovery. These are echo-path correctness checks, not graph-worker capacity evidence.

After adding the process tests: full lint/test **328 passed**, policy coverage **88 passed / 100%**,
disposable reset and isolated Gitleaks passed. The dev gateway remained ready; no runtime worker
configuration was changed.

LG07 compose failover slice: Gemini is now an optional, compose-only secondary provider; all
provider failures produce a localized no-claim template. Mocked primary→fallback→template tests
and deterministic template grounding pass. Latest full suite: **333 passed**; after the final
pending-confirmation template safeguard, the focused composer/settings/contracts run passed
**56 tests**. `make lint` is clean. No live Gemini request was made.

LG07 staff Edit slice: approval alerts gain an **Edit** button (private staff chats); the next
private message from that chat becomes the customer reply, validated (non-empty, ≤2000 chars, no
control characters or `/commands`), delivered once through the outbox and audited as `edited`
(`approval_audit.old_values` draft / `new_values` final_text). Migration **0230** (additive:
`edit_reply` action, `awaiting` status); gateway `EXPECTED_SCHEMA_VERSION` is now 0230, so a
restarted dev gateway reports not-ready until the dev DB is migrated. Evidence: `make lint` clean;
full `make test` **347 tests** (342 passed + 5 failures that were solely the old schema-version
pin, fixed; those three files rerun: 14 passed). Edit coverage: 8 new tests in
`tests/test_staff_path.py` plus 1 webhook-level test in `tests/test_ingress.py`, all on throwaway
testcontainers Postgres with mocked Telegram transport. **Not done:** `make db-reset` on the
persistent dev DB (migration was applied from scratch by the test containers instead); no live
Telegram Edit round trip.

LG07 closing slice: Edit now binds to the alert message (migration **0240**, `staff_actions.source_message_id`
recorded on the tap) and works in staff groups by replying to the alert (non-matching group text is
rejected 422, never a customer message); Edit button shown for group targets too. Also: outbound
`redacted_body` is redacted at all four insert sites; finalize persists `contacts.locale`
(en|ms|zh|ta); `has_attachment` is an `always_review_if` flag; delivery 429 `retry_after` test added
(dead-letter/alert already tested); DB-backed shadow-policy test; default `GOOGLE_AI_MODEL` is
`gemini-3.1-flash-lite`. Migrations 0230 and 0240 were applied (additive, no reset) to dev DB
`csa_dev_db`. Evidence: `make lint` clean; full `make test` **358 passed**. **Not tested live:**
Telegram Edit round trip (private or group) on the dev bot, real Groq outage, and a crash-kill of the
delivery worker mid-send; live Telegram Edit round trip (group alert reply) succeeded on the dev bot.

## Known gaps

- LG07 private-chat Edit binds to the chat's newest armed Edit (group Edit binds to the replied alert). Gemini failover: key and `GOOGLE_AI_MODEL=gemini-3.1-flash-lite` work for a direct client call, and live compose failover with a mocked-failing Groq primary and real Gemini succeeded 3/3 (~1 s) after the single-attempt budget fix (fallback timeout 5.0 s); a real Groq outage was not exercised; the old default `gemini-2.5-flash` returned 404 for new keys, so the default is now `gemini-3.1-flash-lite`. Migrations 0230/0240 were applied to the dev DB `csa_dev_db` (not a reset).
- Circuit-breaker and dead-letter alert producers already enqueue staff notifications and are covered by circuit and ingress/delivery reaper tests; delivery remains Telegram-only.
- No staff-target admin APIs; staff authorised by chat id, not per person.
- Ticket SLA not read from `clients.config.sla`.
- Jev cascade currently gates on intent confidence only; broader per-question calibration, vendor
  retention/SLA review and real-client rollout remain pending. Synthetic results are not production guarantees.
- WhatsApp, Gmail, Calendar channels and extra agents are planned only (see LG13).
- LG08 full golden matrix, broad empty-database seed timing and longer simultaneous socket-level
  ingress/worker load remain open. The separate four-consumer diagnostic completed every event,
  but ack p95 was 782.528 ms and drain throughput about 2.34 turns/s; this is far below 50
  turns/s. Four independent graph savers are only a benchmark topology, not production runtime.
  Do not scale concurrency or weaken checkpoint durability until a workload and connection
  budget is selected. ASGI timing on the shared generator/server loop remains variable.
  Invalid/expired callbacks currently escalate rather than returning a dedicated rejection acknowledgement.
