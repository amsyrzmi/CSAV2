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
| LG13 Agents/Console & E-Commerce draft | Done: Wave 1 agents (KB semantic vector search, gaps logger, ingestion; Invoices contact-scoped lookup & signed expiring download receipts), E-Commerce agent (TikTok Shop Open API HMAC signer + Shopee/Lazada adapters), Console API admin subscription toggles (`is_enabled`/`client_enabled`), specialized tabs catalog (LHDN, E-Com, KB, Invoices, Bookings) | `tests/test_ecommerce_agent.py` (4 passed), `tests/test_kb_agent.py` (7 passed), `tests/test_invoice_agent.py` (5 passed), `tests/test_console_capabilities.py` (3 passed), `csa/agents/kb/`, `csa/agents/invoices/`, `csa/console/` |
| Marketplace Ingress (TikTok Shop & Multi-Marketplace) | Done: TikTok Shop webhook verification (HMAC-SHA256, deduplication, ingress_events persistence), seller OAuth flow (signed state, code exchange, AES-GCM credential persistence, auto endpoint provisioning), Shopee/Lazada draft endpoints | `tests/test_marketplace_ingress.py` (17 passed), `csa/channels/marketplace/`, `apps/gateway/main.py` |
| Langfuse Observability Integration | Done: Direct async batch ingestion (`LangfuseSink`), redaction & privacy preservation, integrated into worker `PostgresEventSink`, initial activation trace verified live on `jp.cloud.langfuse.com` | `tests/test_langfuse.py` (4 passed), `csa/core/langfuse.py`, `csa/graph/events.py`, `apps/worker/main.py` |
| KB console upload ingestion | Done (gated by `KB_INGEST_ENABLED`): console writes ≤3 PDF/DOCX/CSV files to `KB_UPLOAD_ROOT/<client_id>/` + `kb_ingest_jobs` row; worker validates, confines paths, extracts, chunks, embeds (strict TEI, no silent fallback), stores versioned/deduplicated docs, records per-file `meta.results`, and retries with backoff. Model-filtered search. Also fixed LG13 regressions: `EcommerceAgent.ACTION_RISK` class attribute, stale capabilities test, JEV answers fixture for new agents, and `job_runs` (owner-written ops log) excluded from the RLS set-equality test. Full suite: 442 passed; the 6 failures were rerun in isolation and pass (the 3 latency thresholds failed only under host load). | `tests/test_kb_ingest.py` (25 passed), `tests/test_kb_agent.py` (7 passed), live dev TEI e5-small semantic check; not tested: real console writing jobs, worker loop running live in dev |
| LG13 Waves 2/3 preparation | Implemented provider-neutral slice, **not live integrations**: disabled-by-default booking/subscription agents provide contact-scoped local reads and unexecuted requests; staff-only email/research/invoice-draft libraries, strict inputs and Decimal totals. Immutable preparation ledger with RLS, contact-owner checks, actor/trace and concurrent idempotency. No external calls, executable approvals or billing/event/invoice mutations. | `csa/agents/staged/`, migration `0260_staged_agent_requests.sql`, `tests/test_staged_agents.py` (51 passed); `docs/handoffs/2026-10-15-wave23-preparation.md`. |
| LG15 Console turnover (n8n → CSA) | M0–M1 implemented and reviewed locally (2026-10-15): reviewed Next.js adoption, CSAV2 sessions/membership RBAC, invitation/reset/manual delivery, deliberate bootstrap, audited tenant/member onboarding and capability provisioning. Identity pool lifecycle/readiness explicit; human sessions independent of machine token. FourStacks UI guide applied; deferred and unmigrated pages inaccessible. M2 groundwork exists; remaining M2–M8, live browser acceptance and deployment remain pending. | Migration `0270_console_identity.sql`, `csa/console/`, typed frontend identity client, `docs/console_openapi.json`; focused checks/lint pass. Full run: 564 passed, one TCP latency failure; standalone rerun also fails. See evidence below; no clean full-suite pass claimed. |
| LG14 Teach-a-Task Virtual Desktop | Planned: Dedicated desktop container sandbox, user action recording, playbook compiler & scheduled replay | `docs/langgraph-rebuild/LG14_teach_task_desktop_agent.md` |

Console M0–M1 review/verification (2026-10-15):

- Fixed Unicode password changes raising `TypeError`: constant-time equality now compares
  UTF-8 bytes. Fixed resets bypassing an unaccepted invitation's membership creation:
  reset issuance returns 409 without invalidating the invitation; accept/renew it instead.
- Session integration selection **33 passed**. Follow-up review regressions plus
  config/contract/lifecycle/password selection **30 passed** (overlapping selections,
  not additive). This includes the pending-invitation regression added after the first
  selection started. Final `ENV=ci NO_LOCAL_ENV=1 make lint` passed.
  The existing accepted-account invite/reset single-use/session-rotation test was also
  rerun after the reset guard change: **1 passed**.
- Frontend transport/routing checks **5 passed again during review**. The implementation
  run also passed isolated ESLint, Next type generation, `tsc --noEmit` and Next 16.3.8
  production webpack build; no frontend source was changed during this review.
- The already-running full `ENV=ci NO_LOCAL_ENV=1 make lint test` completed before the
  review fixes: **564 passed, 1 failed** (19m44s). TCP ingress ack p95 **1021.433 ms**
  exceeded **200 ms**. The exact standalone rerun also failed: p95 **451.844 ms**,
  150 completed turns and 150 pending deliveries, 2.084 turns/s. Cause is not established;
  do not dismiss this as full-suite contention or claim performance acceptance is green.
  No threshold relaxation or unrelated performance workaround was made.
- Previously recorded `make db-reset` plus migration reapply passed in a new disposable
  Postgres container (0270, LangGraph checkpoint setup and synthetic seeds); it was removed.
  This review changed no schema and did not reset/migrate any existing database.
- `NO_LOCAL_ENV=1` prevents Make and development settings from reading `.env.local`;
  Make uses its declared webhook default rather than exporting empty `CHANNEL_MODE`.

Next bounded slice: finish **M2** (truthful readiness/role/entitlement metadata, tenant
switch UI, specialised navigation, scoped runtime visibility/pagination, and execution/cache
freshness evidence). Mutation RBAC, transactional audit, safety guards and the initial
platform entitlement UI are already implemented. **M3** authenticated three-file upload,
download/delete safety and job-progress UI can proceed independently after M1. No additional
provider keys are needed for these provider-neutral packages. Diagnose the unresolved TCP
latency acceptance failure before treating the release as verified.

Safety correction: the first use of the pre-existing test-image fixture sent the
repository build context to the local Docker daemon, potentially including ignored
env/reference files. No secret values were inspected/displayed, and the Dockerfile
copies no application files. The fixture now uses `deploy/` only. Adopted frontend
verification copies an explicit source allowlist to `/tmp`; build traces are checked
for env files, nested Git, backups and the copied API. No deployment, live browser or
provider validation, existing-process/port/webhook/proxy change, or commit was performed.

Required human-console settings: `IDENTITY_DATABASE_URL` (same database, narrow
`csa_identity` role), `CONSOLE_ALLOWED_ORIGIN`, and frontend `CSA_CONSOLE_API_ORIGIN`.
Provision the role using `CSA_IDENTITY_PASSWORD` only in the migration/provisioning
process, then run the deliberate interactive first-admin bootstrap. Cookie/expiry
and optional machine allowlist settings are documented in `CONSOLE_API.md` and
`.env.example`; neither real env file was edited. Invitations/resets use manual
delivery. Membership switching/account attachment, browser visual checks, M2+ business
UI, web chat, KB upload, approvals, provider execution and runtime turnover remain open.
The unsupported invoice download-link endpoint now returns 501 without signing fallbacks.

Wave 2/3 verification (2026-10-15): `make lint` passed (Ruff, format, Pyright and
import-linter). Final preparation/identifier selection: **52 passed** (51 preparation
cases). Earlier planner/coordinator/worker selection: 117 passed. Actual `make db-reset`
passed in a separate disposable `wave_reset` database; no existing dev/production database
was reset or migrated. Gateway readiness and migration expectations now require `0260`.
Full `ENV=ci make lint test`: **499 passed, 1 failed** (16m19s): TCP ingress p95
440.25 ms exceeded the unchanged 200 ms acceptance limit. This is not a clean full-suite
pass. The exact failing TCP benchmark passed when rerun alone
(`test_real_ingress_50rps_and_durable_queue[tcp]`, 1 passed in 135s).

Earlier verification (2026-10-14, after the KB ingest slice): `make lint` passed.
`ENV=ci make test` ran 448 tests: 442 passed and 6 failed. Three failures were test drift
and were fixed (2 in the JEV fixture, 1 in the RLS set test). Three were latency thresholds
missed under host load. All 6 passed when rerun in isolation.
Earlier verification (2026-10-14): `ENV=ci make test` **363 passed** (14m19s).
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
