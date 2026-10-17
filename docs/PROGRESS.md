# Progress

Latest continuation: [M5 Telegram inbox scope and evidence](./handoffs/2026-10-17-m5-telegram-inbox.md).
M4 history starts at [the prior closeout](./handoffs/2026-10-16-m4-closeout-m5-start.md).
M4 is **functionally closed with an explicitly accepted laptop-dev performance exception**;
M5's Telegram-only staff slice was reviewed and rolled out on explicitly approved guarded
dev at **0300**; platform-admin UI and synthetic Telegram acceptance are verified.
Live reader denial and representative tenant-admin/staff browser replay pass. Per-role
fresh approval-decision coverage remains automated evidence, not a repeated live matrix.
M5 full validation was **692 passed**, including both unchanged ingress latency checks.
Latest M6B validation is **732 passed / 1 TCP latency failure**; see the offline record below.
M4's historical **674 passed / 2 latency failures** remain recorded;
one current pass is not a laptop capacity guarantee. See the closeout decision below.
Documentation cleanup consolidates stale
status/navigation and replaces the obsolete kickoff prompt; plans, historical evidence,
user-owned review reports and UI-guide material remain preserved.

## M6 Business management foundation (2026-10-17; offline)

- M6B continuation: offline TikTok order-read contract groundwork is implemented.
  Detail/search now reject provider errors, incomplete/malformed orders, mismatched
  detail IDs and invalid pages rather than fabricate successful default data.
  Typed one-page reads preserve continuation tokens, Decimal money and carrier references;
  legacy first-page list shape remains. Read errors expose safe codes, not raw provider/
  transport/validation contents; reads do not place access tokens in URLs or follow redirects.
  Targeted synthetic adapter/ecommerce checks: **41 passed**, 2.51 s.
  Final `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: Ruff/format/Pyright/
  import-linter pass; **732 passed / 1 failed**, 136 warnings, **2701.96 s**.
  Only failure: `test_real_ingress_50rps_and_durable_queue[tcp]`, ack p95
  **608.321 ms** against unchanged **<200 ms**; scheduled ack **627.508 ms**.
  ASGI ack/scheduled p95 **185.855/198.599 ms** pass. Each transport completed150
  requests with150 durable deliveries, not a latency waiver. Simultaneous TCP
  ack/scheduled p95 **1337.291/1362.941 ms**, graph-only p95 **96.771 ms** are
  diagnostic evidence, not a passing TCP latency claim. Disposable-only reset checks pass.
  One initial full run was stopped before completion while finalizing the redirect guard;
  no result is claimed for it. No latency retry/threshold change was made.
  Full validation remains **not green**; no new M6 performance waiver is implied.
  No console/provider wiring, live credentials, store claims or product/write changes.
  Current official API compatibility and tenant/store credential binding remain gates,
  not assumed complete. See the [M6B groundwork and verification gates](./handoffs/2026-10-15-console-source-migration-plan.md#m6b-order-read-contract-groundwork-offline-not-provider-verification).
- User requested a multi-platform seller workspace, TikTok-first, and approved **staff-only
  business reads** plus **read-only local stock with no synchronization authority**.
  Expanded [M6 roadmap](./handoffs/2026-10-15-console-source-migration-plan.md#m6--business-tabs-integrations-and-staff-preparation)
  stages local reads, verified seller reads/connections, separately authorized TikTok
  operations, multi-platform inventory decisions, and original invoice/staff preparation work.
- Implemented separate `/business` and `/portal/business` UI plus typed local
  `/console/v1/business/orders` and `/products` lists/scoped summary-detail reads.
  Bounded keyset pagination; decimal monetary fields; orders include persisted channel,
  store/order identifiers and tracking/carrier references. Products show local
  stock/reserved/available counts. No address/contact/credential/arbitrary attributes in
  these projections. Readers/machines/anonymous denied; staff tenant scoping preserved.
- Explicit UI labels distinguish local update from sync freshness, local catalogue from
  published listings, tracking references from live carrier tracking, and missing local
  rows from a marketplace with no data. Marketplace sync, connections, listing/stock/
  fulfilment/refund/payment writes and later tickets/invoices/preparation are unavailable,
  not fake working controls. Existing TikTok adapter is not wired by the empty
  `EcommerceAgent()` runtime constructors; no live connection was verified.
- Targeted backend/contract selection: **6 passed**, 1 warning, 77.60 s. Isolated
  frontend: **13 transport + 2 state tests**, ESLint, Next type generation, TypeScript,
  production webpack build and trace-boundary checks pass. Initial frontend lint caught
  synchronous effect state resets; moved resets to explicit navigation/refresh actions,
  then reran checks.
- M6A foundation full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: Ruff/format/Pyright/
  import-linter pass; **695 passed / 1 failed**, 136 warnings, **1795.08 s**. Only failure:
  `test_real_ingress_50rps_and_durable_queue[tcp]`, ack p95 **788.334 ms** against unchanged
  **<200 ms** (scheduled ack **1034.766 ms**). ASGI ack/scheduled p95
  **118.552/119.057 ms** pass. All150 TCP requests completed with150 durable deliveries;
  this does not satisfy latency acceptance. Simultaneous TCP ack/scheduled p95
  **514.905/540.197 ms**, graph-only p95 **100.350 ms** are retained diagnostic evidence,
  not a threshold waiver. Guarded disposable-only `make db-reset` regressions pass.
  No latency retry or threshold change was made. Full validation is **not green**;
  M4's laptop-dev exception is not an M6 performance waiver, and laptop causality
  remains unproven. M6A live acceptance/performance sign-off remain open.
- No persistent migration/reset, service replacement, provider calls, password/token
  inspection, production/infrastructure/webhook change, dependency change or UI-guide edit.
  Existing dirty M5 implementation preserved. Last approved dev build is M5, not M6.
- Current read-only environment probe (2026-10-17 about 07:04 UTC) found no listeners
  on18081/13000; recorded prior M5 PIDs are no longer present. This continuation did
  not stop or restart them. Prior live acceptance is historical evidence; do not claim
  the console is currently available. Service restoration/replacement and live M6
  acceptance remain separately approval-gated.

## M5 Telegram customer inbox (2026-10-17 implementation record; guarded dev rollout 2026-10-16 UTC)

- User explicitly selected Telegram customer conversations and outbound-reply approvals
  for platform admins (explicit tenant), tenant admins and tenant staff. Readers and
  machines are denied, including message reads. Customer-web and order decisions are
  deferred. Private sandbox histories, contacts and held turn fields remain excluded.
- Added separate queue/detail UI and typed `/console/v1/inbox` reads, takeover, release,
  human replies and structured shared approval transitions. Decisions use verified
  console user UUIDs, CSRF/RBAC/tenant checks, one shared domain implementation and
  atomic console/domain audit and outbox. Telegram edit/action binding remains intact;
  acknowledgement text now says queued, not delivered.
- Indefinite ownership and model bypass match LG04. Locked egress/holds/fallback and
  delivery checks prevent stale automatic sends across takeover/release. A matching
  escalation permits the intentional code-selected handoff acknowledgement; explicit
  staff replies retain their own authority. Delivery claiming orders each conversation
  through retries. A provider-accepted send still cannot be recalled or made exactly once.
- Additive 0300 introduces the suppression/version fields, message provenance, replay
  receipts and indexes with staged RLS, no FORCE. After disposable validation and separate
  explicit approval, persistent dev advanced **0290 → 0300** without reset and guarded
  services were replaced. No environment-file, webhook, upload or production change.
- The checkout was clean at `d8f7149` on entry. Existing user review files and UI-guide
  state are preserved. No commit was requested or made.
- Post-review offline validation passes. Final exact results belong in
  [the M5 evidence record](./handoffs/2026-10-17-m5-telegram-inbox.md).
  M4's accepted laptop-dev exception remains; no threshold or release waiver changed.
- Implementation review fixed three coupled gaps before rollout:
  - Takeover of an existing timed escalation must clear its expiry and establish
    indefinite ownership, not return already-handled. The detail UI now offers
    **Take over indefinitely** for a temporary pause.
  - Escalation now checks current automation authority under the conversation lock
    before tickets/notifications/mute effects; stale in-flight work cannot replace
    indefinite takeover with a 24-hour mute or re-mute an explicitly released turn.
  - Existing Telegram order callbacks now commit metadata-only completed ingress
    provenance with their customer message/outbox. The new strict delivery route
    binding otherwise rejected these replies after a successful order decision.
    Console order decisions stay deferred; no transport/binding guard was relaxed.
- Review targeted tests: **59 passed**, 23 warnings, 142.23 s (inbox, staff, coordinator),
  plus **2 passed**, 3 warnings, 22.56 s (actual order approve/reject callback-to-delivery
  path with mocked Telegram). Five new regression cases in total; selections overlap
  the full suite. Final isolated frontend **12 transport + 2 state tests**, lint/types/
  production build and safe traces pass. Post-review full result is recorded below.
- The prior completed readiness/load follow-up was **7 passed / 1 ASGI latency failure**,
  not an in-progress run. Its corrected expected schema 0300 fixes the earlier four
  readiness failures; it does not establish green performance acceptance.
- Review changes were offline. At that review stage no persistent migration/reset, service replacement,
  credentials inspection, live external send, production/infrastructure/webhook change
  or UI-guide modification. Keep rollout and live acceptance separately approval-gated.
- Final post-review `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **692 passed**, 136 warnings, 1645.86 s. Ruff/format/Pyright/import-linter pass;
  actual fingerprint-guarded disposable-only `make db-reset`/0300 and staged RLS pass.
  Unchanged ingress-only 50-rps acknowledgement/scheduled p95: ASGI
  **108.284 / 109.555 ms**, TCP **174.623 / 176.324 ms**, both `<200 ms`, each
  150 completed turns and 150 pending deliveries. Separate simultaneous-capacity test
  passes with 150 unique replies and worker-turn p95 **2690.479 ms** (ack **382.791 ms**,
  distinct criteria); graph-only p95 **88.396 ms** passes. No thresholds, pool sizes,
  tracing or durability were relaxed. Historical failures and the laptop-dev exception
  remain context, not an established cause or production-capacity guarantee.
- Subsequent explicit user approval authorized forward-only 0300, coordinated dev
  replacement and authenticated/synthetic Telegram acceptance. Exact dev target/role
  guards passed; only pending 0300 applied atomically, without reset or password
  provisioning. Gateway/worker/delivery and isolated frontend replaced; health/readiness/
  login 200 and dedicated heartbeats fresh. Initial frontend test-origin rewrite caused
  500; rebuilding isolated source with actual **18081 at build time** fixed it.
- Authenticated Synthetic Admin acceptance: scoped reads, indefinite takeover, three
  completed real Telegram turns without automatic output while muted; shared-service
  synthetic reply approvals; human reply commit/response loss/same-key retry with
  **one message/delivery/receipt/attempt**; approve/edit delivered once each; reject audited
  without fallback while paused. User confirmed the three labeled receipts exactly once.
  Stale version409, other-tenant404, private-sandbox404, distinct bounded message pages,
  and reload recovery of the same delivered message/settled cards pass.
- Deliberate release left the synthetic Telegram conversation AI-owned at version4.
  Fresh post-boundary trace `b9b49cba-5e4d-4fd6-9576-2c050fdad52b` produced the delivered
  seventeen-day KB answer, user-confirmed; it is the only new automatic delivery since
  takeover. No stale paused turn emitted after release.
- Live reader replay **blocked**: user forgot password and encountered login throttling.
  No reset/bypass/retries; reader/machine and tenant-staff/admin authority retain automated
  evidence only. See [exact rollout evidence and active dev handles](
  ./handoffs/2026-10-17-m5-telegram-inbox.md#approved-guarded-dev-rollout-and-live-acceptance-2026-10-16-utc).
  Dirty worktree/UI-guide preserved; no production, infrastructure, webhook or commit changes.
- Subsequently the user explicitly approved recovery of both synthetic dev console logins.
  User-operated hidden terminal prompts set new passwords privately; exact-dev guarded
  transaction reused existing identity hashing/SQL, revoked sessions/links, cleared
  targeted throttling and audited both recoveries at **2026-10-16 20:54:24 UTC**.
  Metadata verified both enabled with zero active sessions/links immediately afterward.
  No password/hash was printed; database-role passwords, bot/API/encryption credentials
  and production were untouched. Live reader replay remains pending, no longer treated
  as requiring the forgotten old password. New low-count login attempts after recovery
  are not evidence that the prior lockout was retained.
- Reader live replay subsequently **passes (2026-10-16 21:02 UTC)**: verified normal
  `read_only` identity200; no inbox navigation; direct `/portal/inbox` permission denial
  without transcript/approval content. Conversation list/detail/messages/approvals reads
  and CSRF-valid takeover/release/reply/approval-decision probes all return **403**.
  No accepted mutation, send, credential inspection or role elevation. Tenant-admin/staff
  browser replay remains pending; the reader-specific gap is closed.
- Subsequent approved separate role fixtures completed normal private invitations:
  `m5-tenant-admin@example.test` and `m5-tenant-staff@example.test`. Both verified
  roles/tenant, inbox reads200, foreign-tenant403 and private-sandbox404. Each exercised
  UI takeover/human reply/release; one delivered message/attempt/receipt each, user-confirmed
  exactly once in dev Telegram. Staff replay of settled approval1 returns already_handled,
  queued=false. Reader remains enabled/read_only; final conversation AI-owned/version8.
  Fresh approval decisions were live-tested as platform admin, with the full per-role
  decision matrix still automated; separate M3 KB role replay is not closed by this.

## Historical incident and recovery: unintended isolated-dev schema reset (2026-10-16)

During runtime-stabilization validation, the assistant intended to run `make db-reset`
against a newly created disposable Postgres container. It passed that container's
host/port/credentials but failed to override `PSQL`. Host `psql` was unavailable, and
the Makefile's fixed fallback used `docker exec csa_dev_db psql`, ignoring the selected
disposable target for destructive baseline commands. The persistent isolated-dev
`public` and `lg` schemas were dropped and the baseline recreated there; the subsequent
Python migration step connected to the intended disposable database and failed because
its baseline was absent. This was an assistant execution/safety error, not an authorized
reset of persistent dev.

Initial read-only verification confirmed zero clients and no console users, KB upload
metadata, migration ledger or checkpoint schema. Earlier live acceptance records below
are historical, not evidence for the rebuilt stack. The initial full validation run was
stopped. No production database connection or change was performed.

The user subsequently chose and explicitly approved a **clean dev rebuild**, not backup
restoration. Recovery progress:

- Removed the fixed-container `psql` fallback. Reset preflight rejects unsafe targets
  and compares PostgreSQL system identifier/database through the selected `psql` command
  and Python DSN before DDL. Makefile and Compose now both default to dev port 5434.
- **13 focused reset-guard tests pass**; a fingerprint-verified `make db-reset` passed
  on an explicitly named disposable container. A separate two-container mismatch check
  refused the target and preserved both sentinel rows.
- Verified persistent `csa_dev_db` belongs to Compose project `csa-dev`, service
  `postgres`, with only loopback port 5434. Applied migrations through **0280** to the
  already-recreated baseline, **without another schema drop**, then recreated checkpoints.
  App and identity roles authenticated successfully; the existing read-only role
  password verifier was preserved.
- Recreated the two synthetic acceptance/development tenants with their prior UUIDs and
  nine disabled capability entries each, using maintenance audits with no impersonated
  human actor. Verified `@CSAV2_bot` via read-only API and recreated the DB mapping for
  its existing webhook. No webhook, Caddy, Cloudflare or n8n setting was changed.
- Two files remain in the dedicated upload root; none was deleted or adopted. Their
  lost metadata/content integrity has **not** been recovered. Historical records,
  accounts, KB documents, subscriptions and enabled switches have **not** been restored.
- Gateway readiness and frontend login HTTP 200 pass after restart. Frontend restarted
  after the exact old dev
  PID retained its Next lock following TERM and required KILL. Fresh administrator
  bootstrap completed privately; the user reports login and required password change
  completed, and a read-only DB check confirms one enabled platform administrator with
  no pending password change. Fresh KB/Telegram acceptance subsequently passed below. Guarded
  conversation and delivery tasks were restarted; their heartbeats are fresh. The
  conversation task enables KB ingestion against dedicated storage/loopback TEI (no
  separate duplicate KB worker). Scheduled jobs, including mute expiry, have successful
  tenant-scoped `job_runs`; this alone is not evidence of a live expired-row transition.
- Recovery acceptance exposed two safe onboarding defaults that required deliberate
  rollout: `onboarding` tenants are not eligible for the normal KB poller, and
  `config.policy.deployment_mode=shadow` suppresses sends. Only the recreated Telegram
  tenant was made `pilot`; the user then explicitly approved its gate policy `auto`,
  with maintenance audits. M1 remains onboarding/disabled. No historical turns replayed.
- Fresh FAQ job `5f066252-1c96-45c0-af90-1952bb8096d5` completed on attempt 1 with
  two chunks. The user reported a transient upload 500; frontend logs contain proxy
  socket-hang-up errors, but the precise cause is not established. No duplicate upload
  was needed. Do not claim that incident fixed.
- Fresh Telegram trace `202ac6db-acf8-4a4a-a03f-105512bbe71d` completed on attempt 1:
  `kb_agent` succeeded, one fact, verification true, gate `auto/send`, one message,
  outbox delivered on attempt 1. User confirmed the seventeen-day answer.
- Verified only the two requested environment values after user authorization:
  blank upload root/ingestion false remain in the environment file; the approved running
  worker explicitly overrides these with dedicated storage/ingestion true. No secret
  values were displayed and no environment file was edited.
- Fresh `NO_LOCAL_ENV=1 ENV=ci make lint test`: lint passed, **644 passed / 3 failed**.
  ASGI ingress p95 **203.61 ms**, TCP p95 **530.46 ms** exceeded unchanged **200 ms**
  thresholds. The third failure was the existing reset test's separate database name
  being correctly refused by the new strict target guard. That test now uses its own
  disposable container/database `csa`, without weakening the guard; an actual make-level
  mismatch regression verifies both databases retain sentinel rows.
- Combined targeted rerun: **17 passed / 2 failed** (reset fixture credential mismatch,
  subsequently corrected; unchanged TCP p95 **363.48 ms** still exceeds **200 ms**).
  ASGI passed in that rerun. Final reset-only selection after correction:
  **15 passed**, covering the actual make-level mismatch/sentinel regression and
  successful reset/migrations/RLS check in a dedicated disposable container.
  Final `make lint` passed at that stage. Validation was still non-green then;
  the subsequent investigation and full-suite result are recorded below. Do not
  repeatedly rerun tests or relax thresholds merely to manufacture an acceptance result.

Before this incident, the new mute-expiry/runner failure-health fixes and concurrency/
worker-recovery selection passed **40 tests**. The changes remain uncommitted.
Subsequent offline validation is green below; live rollout/acceptance of the logging
change remains pending, and the historical hang/500 causes are not proven.

## Ingress latency and gateway/proxy investigation (2026-10-16)

- Safety: no environment snapshots or secrets read, persistent-dev DB connections,
  resets, webhook changes or production/Caddy/Cloudflare/n8n changes. Tests use
  synthetic fixtures/disposable containers. At inspection neither dev gateway 18081
  nor frontend 13000 was listening; the earlier acceptance was historical, not a
  claim that these services are still available. Existing services were not restarted.
- Baseline TCP benchmark reproduced failure at **277.25 ms p95** versus unchanged
  **<200 ms** at 50 requests/s. A diagnostic TCP run passed at **147.00 ms**;
  diagnostic ASGI showed **492.27 ms ack p95**, **517.92 ms scheduled ack p95** and
  **148.45 ms start-lag p95**. Temporary SQL timing instrumentation showed delay
  distributed across sequential statements (roughly 13–18 ms mean per ingress
  lookup), not one uniquely slow query. Profiling and host load affect these runs:
  this does not prove a PostgreSQL bottleneck or justify relaxing thresholds.
- Consolidated credential/runtime reads into one tenant-scoped SQL lookup (six
  fetches instead of seven for the normal first-contact path), with no cache or
  weakened authentication/RLS. Regression checks joined metadata, policy override
  precedence, foreign-tenant isolation and query count. Existing n8n rollback/shadow,
  callback and idempotency behavior remain covered using mocks, not live n8n.
  Combined ingress/cutover/ambiguous-commit/both unchanged latency checks: **19 passed**.
  A full-suite repeat is required before a stable performance claim.
- Reproduced liveness failure in an isolated subprocess using the **actual gateway**
  and deliberately undrained stdout: health 200 before pressure, then health timeout,
  with the event-loop stack blocked in `structlog/_output.py`. No database connection
  or external request was used. This proves a failure mode, **not** the cause of the
  historical hang, whose live process stack is unavailable.
- User explicitly approved bounded background logging with observable failure.
  Structured/stdlib logging now formats on the producer (preserving context), then
  writes on a daemon thread: at most 1,024 waiting events plus one active write.
  A sink older than five seconds makes readiness 503; overflow or writer error
  latches an explicit, secret-free reason and `dropped_events` count until logging
  is reconfigured/restarted. Overflow cannot block a request or grow an unbounded
  backlog. Liveness remains 200. Shutdown drains with a bounded deadline and reports
  failure rather than waiting forever. Output still requires a functioning collector;
  full-queue events cannot be retained and are counted, not silently hidden.
- New real-subprocess regression fills stdout and verifies the pressure request
  completes, health stays 200 and readiness reports 503/queue-full/nonzero drops.
  Logging/gateway/identity-lifecycle/poller selection **29 passed**, including exact
  4.999-second/5.001-second stall threshold and recovery without event loss;
  `make lint` passed.
  This change has **not** been loaded into or accepted against the live dev stack.
- The installed Next.js proxy maps an upstream socket error to HTTP 500. A new
  loopback test reproduces that after a simulated durable upload, then verifies an
  **explicit** same-key multipart retry returns the existing job, preserving CSRF
  and file contents without automatically replaying mutations. Frontend transport
  selection **9 passed**. The real database lost-COMMIT-acknowledgement regression
  also passes. These explain how a queued job can survive a 500, **not why the original
  upstream connection closed**. No timeout increase or success-shaped fallback added.
- Final full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: **655 passed**,
  134 warnings, **1,742.50 s**; Ruff/format/Pyright/import boundary all pass. This includes
  the existing disposable-only `make db-reset` test; persistent dev was not reset.
  The additional exact stall-threshold/recovery test was added after full-run collection
  and passed in the final 29-test focused selection above (counts overlap; do not add them).
- Full-run 50-rps results, with **150 completed turns and 150 pending deliveries each**:

  | Transport | Ack p95 | Scheduled ack p95 | Start-lag p95 | Required |
  |---|---:|---:|---:|---|
  | ASGI | 114.186 ms | 120.390 ms | 5.869 ms | Both ack measures <200 ms |
  | TCP | 129.436 ms | 130.810 ms | 3.206 ms | Both ack measures <200 ms |

  This is a clean offline run, not proof of stable latency under every shared-host load,
  nor live browser/Telegram acceptance of the new logging implementation.
- Frontend changed-test ESLint and all nine transport tests pass. No dependencies,
  environment files, Caddy/Cloudflare/webhooks or production resources were changed.
  No dev listener was running at that offline validation's final check; the live
  rollout and acceptance below subsequently received explicit approval.

### Guarded dev rollout and synthetic acceptance (2026-10-16)

- After explicit user approval, started the isolated loopback gateway (18081), frontend
  (13000), standard conversation/KB worker and delivery worker. Before launch, neither
  app port was listening and no repository worker/delivery process had a connection to
  dev PostgreSQL. `csa-dev/postgres` remained on loopback 5434. The VS Code task runner
  returned “Task not found” for the declared labels, so the exact guarded command bodies
  in `.vscode/tasks.json` were run as scoped background processes; their database/role
  startup checks passed. No task file or environment setting was changed.
- Gateway `/healthz` and `/readyz` returned 200. Frontend loopback login redirected to
  the sign-in page; the user signed in privately. Authenticated KB polling loaded the
  selected synthetic tenant's agents, files, documents, ingestion jobs and gaps, with
  the UI refreshing every five seconds.
- Fresh synthetic return-policy upload returned 202 and completed on attempt 1 as job
  `ac7c91cd-0e15-4b5d-8294-0923f94bd20c` (one chunk, `tei-384`). Authenticated original
  download returned 200/138 bytes; its SHA-256 matched the local synthetic fixture
  (`4264d2e1a9bd25323211782b1b2bf63bd74ac210525d687ff6527034db7adee6`).
- Explicit retry acceptance used a second synthetic CSV: a client-side connection reset
  was injected before the request reached the server, the UI retained the selected file,
  and the explicit retry reused the same `Idempotency-Key`. The retry returned 202 as
  job `9521c0e3-d9a6-441f-83af-880d7eb72cae`, then completed on attempt 1 with one chunk.
  This validates the UI's retained-key retry path; it is not a live committed-response
  loss test (the latter remains covered by offline proxy/database regressions).
- The user sent the synthetic question “What is the sample return window for runtime
  acceptance?” and confirmed receiving the expected seventeen-day answer. Tenant
  `76946f31-2b21-4e48-9383-65d1a78029c2` trace
  `7a4d0622-74f0-431a-9907-6d2bf323ffe1` has one completed ingress (attempt 1), one
  inbound and one outbound message, and a successful `kb_agent` run. Its persisted
  result contains four fact IDs, including three retrieved KB-chunk facts; fact text
  was not selected. UI trace events for verify, gate and emit all reported `ok`.
  Exactly one matching outbox row is `delivered` (attempt 1, Telegram HTTP 200).
- Read-only, exact-target dev queries corroborated three recent tenant
  `kb.upload_queued` console-audit records and fresh successful tenant scheduled-job
  heartbeats with no errors. No old upload files were reconciled or deleted. No database
  reset, production connection, webhook/infrastructure change or production setting
  change occurred.
- This is one synthetic isolated-dev acceptance, not a general provider/reliability
  claim or proof of the historical hang's cause. The guarded services remain running
  as detached loopback processes after the acceptance checks; the earlier offline check
  remains historical evidence.

### M3 gap, version and crash-window follow-up (2026-10-16)

- Through the authenticated KB UI, a deliberately unmatched synthetic KB-agent query
  appeared under Unanswered questions and remained visible after the five-second polling
  refresh. Selecting the M1 tenant showed zero gaps; returning to Local Telegram
  Development showed the synthetic gap. Mark resolved removed it from the UI, and a
  read-only exact-target dev query confirmed `resolved=true` and exactly one
  `kb.gap_resolved` audit. Read-only resolution denial is verified by the disposable
  console integration test, not by a separate live reader login.
- Uploaded a new synthetic CSV twice under the same logical document title, changing
  its answer. The UI showed version 1 superseded and version 2 active, each with one
  `tei-384` chunk. Both source versions are retained; no reconciliation or cleanup ran.
- Disposable-Postgres acceptance selection: **15 passed**, covering populated gap
  resolution/audit, read-only role denial, tenant scoping, version progression,
  publication rollback, commit-ack replay, deletion/embedding/read races, partial retry,
  and abandoned-job requeue/single claim. An added
  `test_worker_crash_after_document_commit_retries_without_duplicate_chunks` injects
  worker failure after document/chunk commit but before job completion; retry reuses the
  one document/chunk set, completes the same job on attempt 2, and retains one upload
  batch, source record and audit. The strengthened lost-COMMIT-ack test also checks one
  batch, file, job and audit after explicit same-key replay. Focused rerun: **2 passed**.
- `make lint` passes (Ruff, formatting, Pyright and import-linter). These are isolated
  synthetic tests; no persistent-dev reset or old-upload cleanup was performed. M3
  tenant-role browser replay remains unverified; in particular, the read-only boundary
  is test-proven, not browser-replayed with a live reader account.

### M4 private read-only sandbox (2026-10-16; guarded dev acceptance)

- Implemented the user's explicit **Sandbox assistant** scope: only KB query and product
  search/detail may execute. Shared runner enforcement denies business writes, customer
  history, invoice/payment/marketplace, booking/subscription/ticket and staff escalation
  even when a model requests them. Callbacks are rejected before pending-action
  consumption; confirmation controls intentionally remain unavailable.
- Additive migration 0290 supplies staged tenant RLS and per-account/tenant dedicated
  sandbox contacts. Human identity, tenant selection, CSRF/write role and exact owner
  checks protect conversation creation, bounded redacted text ingress, stable cursor
  history and audited archive. The queue's required routing metadata is internal-only,
  disabled and token-free, without changing any existing Telegram endpoint.
- Web worker/graph dispatch and local outbox delivery validate conversation/contact/
  endpoint/trace/message binding. Availability and delivery attempt commit together;
  no external transport or Telegram credential decryption occurs. Held drafts/reasons
  remain private and held, without staff notification or customer-approval expiry release.
  Fail-safe status remains failed and does not promise staff follow-up.
- `/chat` and `/portal/chat` expose private, typed history and 2.5-second polling with
  older-page cursors, queued/processing/awaiting-delivery/held/available/failed/no-reply
  labels, 15-second HTTP deadlines versus 90-second turn delay warnings, explicit
  retained-key retry and reconnect without resend. Archive is confirmed hide, not purge.
  In-memory draft/key retention does not survive leaving the tenant or reloading:
  inspect persisted history before submitting again. Voice/files are unavailable.
- Runtime/runner/Telegram/contract/migration selection: **60 passed**. It includes real
  synthetic KB retrieval through the graph to a private reply, post-outbox-commit worker
  crash/reclaim without duplicate messages, local-delivery rollback/retry with no partial
  visibility, role/tenant/user/contact denial, replay conflict, archive and the actual
  `make db-reset` command pinned to a disposable container with fingerprint checks.
  The reset reached 0290 and verified all three tables have RLS enabled without FORCE.
- Final sandbox-focused rerun: **10 passed**, including expanded-redaction text bounds,
  signed-bigint cursor rejection, stronger routing/trace bindings and proof that only the
  fixed system refusal, not arbitrary template replies, bypasses model grounding.
  This selection overlaps the 60-test run; do not add the counts.
- Final isolated frontend checks pass: **11 transport/routing/cancellation tests plus
  2 state/deadline tests**, ESLint, route type generation, TypeScript and production
  webpack build. Traces exclude environment files, backups, nested Git and the legacy API.
  Final `make lint` passed (Ruff, formatting, Pyright and import-linter).
- Initial full run: **671 passed / 4 failed**. The new migration exposed the gateway's
  separately pinned expected schema version still at 0280. Updating the readiness
  constant/docstring to 0290 fixes that contract without changing thresholds or SQL.
  The health/load rerun then had **7 passed / 1 TCP latency failure**: ASGI
  **109.200 ms**, TCP **243.150 ms** against the unchanged `<200 ms` threshold.
  Exact standalone TCP repeat passed at **110.980 ms** (scheduled **111.606 ms**),
  with 150 completed turns and pending deliveries.
- Pre-rollout post-readiness-fix `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **674 passed / 1 failed**, 134 warnings, 1282.80 seconds. The remaining failure is
  `test_real_ingress_50rps_and_durable_queue[tcp]`: ack p95 **202.643 ms** and
  scheduled ack p95 **203.795 ms**, exceeding the unchanged 200 ms acceptance bound.
  ASGI passes at **79.734 / 81.872 ms** ack/scheduled; each transport completes
  150 turns with 150 pending deliveries. The separate simultaneous worker-capacity
  test passes with 150 unique replies, and graph-only p95 is **103.657 ms**.
  Full acceptance is **not green**. The standalone pass does not replace the full-run
  failure; no cause or M4-induced/non-M4-induced performance claim is established.
  No threshold was waived by the subsequent explicit guarded-dev risk approval.
- The user explicitly chose `approve_dev_with_known_failure`. Forward-only 0290 was
  applied on exact-target-guarded `127.0.0.1:5434/csa` with the existing migration runner;
  only that missing migration was applied. No reset, role-password provisioning or
  secret inspection. Guarded gateway/worker/delivery/frontend replacements are running.
  Gateway health/readiness and frontend login return 200; anonymous chat API is 401 and
  anonymous `/chat` redirects 307.
- Browser integration returned no connected client, including after the user reconnected.
  Acceptance below is **user-operated**, not an agent-operated browser replay.
  The user confirmed the seventeen-day KB answer. Trace
  `ba22451a-06f2-40b6-851b-f0a990bf6529` completed with one successful KB run, retrieved
  fact IDs and one delivered web reply. The dedicated sandbox contact has zero orders.
  Conversation `f10ac68b-9050-4cc8-9eed-e0a18313cfc1` is archived, both turns retained,
  with one `chat.created`, two `chat.queued` and one `chat.archived` audit records.
  Disappearance from the active list is intended hide behavior, not deletion.
- The invoice request did **not** pass refusal acceptance. Trace
  `82e937d1-d108-4975-8460-e6e2aa6ff79c` classified `billing_invoice`, planned no tasks
  with `ask_clarifying`, ran no agent and delivered a misleading request for invoice/date
  details. The compose guard only caught denied executions/escalations. The follow-up
  fixes this fact-free account-intent path and refuses confirmation plans before model
  composition; general KB-grounded policy answers remain allowed. Sandbox regression
  selection **11 passed**, with a final strengthened provider-noninvocation check
  **1 passed** (overlap). Full rerun and approved worker replacement are recorded below.
- The user initially reported receiving the seventeen-day Telegram answer, but then-current
  stored
  Telegram traces (`62f8b7bd-08f3-44ee-9daa-733ab8e4858f`, KB success/HTTP 200, and
  `16c43902-56b4-4a0b-99bf-e9289a8dabde`, HTTP 200) predate the M4 rollout.
  Do not treat those or the user's receipt alone as fresh post-rollout corroboration.
  Fresh Telegram/fixed refusal/reconnect were therefore still unverified at that point.
- Post-scope-fix `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **674 passed / 2 failed**, 134 warnings, 2113.83 seconds. Ruff, formatting, Pyright,
  import boundaries and disposable-only `make db-reset`/0290 acceptance pass.
  Both unchanged ingress benchmarks fail: ASGI ack p95 **508.717 ms** (scheduled
  **763.499 ms**) and TCP **298.288 ms** (scheduled **299.260 ms**) against `<200 ms`.
  Both complete 150 turns with 150 pending deliveries. This adds an ASGI failure to
  the previously disclosed TCP failure. No cause or M4 causality is established.
  Full acceptance remains **not green**; no threshold was changed.
- The updated two-failure evidence was explicitly disclosed before replacement.
  The user chose **Approve guarded dev worker replacement despite both latency failures**.
  Verified old worker PID 1632176/UID/exact cwd/guarded command marker, stopped only its
  managed service, and started `m4-dev-worker-scope-fix` with clean environment and
  exact dev host/port/database/role checks, rejecting `hostaddr`/service overrides.
  Startup confirmed the old PID absent; the replacement's job loop started and
  completed scheduled jobs at 16:40:52 UTC. Other services remained running unchanged;
  gateway readiness and frontend login remain 200.
- Fresh **user-operated**, read-only metadata-corroborated acceptance after replacement:
  - Invoice trace `f7b89539-d90f-411d-b030-0bdbd5b7b627` at 16:44:23 UTC reproduces
    `billing_invoice` / `ask_clarifying`, zero agent runs, and delivers the **exact**
    fixed sandbox-scope refusal. No invoice details are requested; sandbox orders remain 0.
  - Web KB trace `77fc0fee-1710-4631-89b4-286db125e085` at 16:45:23 UTC completes
    one successful `kb_agent` run and one delivered private reply. The user confirms
    the seventeen-day answer remains available.
  - Fresh Telegram trace `de8b3b0c-27ed-4297-8c8d-2f5bdb14445f` at 16:46:17 UTC
    completes one successful KB run and one HTTP-200 delivered reply; the user confirms
    receipt of the seventeen-day answer. This supersedes the earlier stale receipt.
  - User refreshes/reopens the new conversation: both questions/replies remain exactly
    once. User then tests browser-tab Network Offline -> No throttling: polling recovers,
    with the same history and no duplicate replies. These are user-operated checks,
    not agent-operated browser evidence.
- At that point reader-role browser replay remained unverified because the user had no
  reader account. The separately approved follow-up below closes controls/ownership
  browser evidence; performance acceptance remains open.
- Existing upload files, user-owned dirty changes, `ui-guide/` and the approved
  UI-guide deletion remain preserved. No production, infrastructure or webhook changes.
  M5 has not started. See [the M4 API contract](./CONSOLE_API.md#private-sandbox-assistant-m4).

### M4 reader replay and bounded latency investigation (2026-10-16)

- The shared browser became available. Agent-operated authenticated platform view of
  conversation `1e2140a9-4274-4fc5-a0d7-f98a50e5d19c` directly shows both the exact
  sandbox invoice refusal and seventeen-day KB answer, each once.
- The user explicitly approved **one synthetic dev-only account**, registering privately
  through the normal invitation flow. The prepared fixture was Synthetic M4 Reader,
  `m4-reader-20261016@example.test`, `read_only`, Local Telegram Development. The user
  created/accepted the invitation and signed in without sharing the password or link;
  the assistant did not inspect invitation tokens, passwords or cookies. No existing
  account's role was changed.
- Agent-operated reader `/portal/chat` checks: read-only status visible; New sandbox
  conversation disabled; Send to sandbox and Archive conversation controls absent.
  Same-origin authenticated GETs: own list **200**, **0** conversations; admin-owned
  active history `1e2140a9-4274-4fc5-a0d7-f98a50e5d19c` and archived history
  `f10ac68b-9050-4cc8-9eed-e0a18313cfc1` both **404**; selecting M1 tenant is **403**.
  No write was attempted through the browser or CSRF bypass. Reader write denial is
  separately reconfirmed by the real disposable backend test. The new reader has no
  own existing history, so that specific read path remains test-proven, not browser-replayed.
  Browser left signed in as Synthetic M4 Reader; no account cleanup/revocation performed.
- Bounded metadata-only diagnostics used disposable containers, synthetic fixtures and
  temporary session-local instrumentation; no live-dev load, production connection,
  persistent reset, service replacement or infrastructure change. The original
  150-request/50-rps/<200-ms assertions and queue/delivery-count checks were unchanged.
  Results, recorded in execution order (overlapping selections, not additive coverage):
  1. Instrumented ASGI/TCP selection: **1 passed / 1 failed**. ASGI ack/scheduled p95
     **132.946 / 134.701 ms**; TCP **460.769 / 461.766 ms**. Each completes 150 turns
     with 150 pending deliveries. ASGI pool wait p95 **0.114 ms**, event-loop lag
     **8.096 ms**, max 9 active receives. This passing ASGI sample does not profile
     the earlier full-run failure.
  2. TCP-only wrapper run: **1 passed**, ack/scheduled **141.116 / 143.003 ms**.
     Its shutdown-time profiling report was not produced; do not infer internal
     stage timings from that run.
  3. TCP-only profile emitted before shutdown: **1 passed**, ack/scheduled
     **118.645 / 118.939 ms**. Server receive p95 **68.684 ms**, pool wait
     **0.113 ms**, event-loop lag **6.477 ms**, max 5 active receives. Five named ingress
     query families each run 150 times; contact-resolution p95 **15.729 ms**. Again,
     this captures a passing run, not the TCP failure.
  4. Final **uninstrumented** reader-backend + original ASGI/TCP selection:
     **2 passed / 1 failed**, 3 warnings, 189.08 seconds. Reader denial passes;
     ASGI ack/scheduled **93.234 / 102.600 ms**; TCP **261.451 / 262.471 ms** fails
     unchanged `<200 ms`. Both transports complete 150 turns with 150 pending deliveries.
- Full-run acceptance remains **674 passed / 2 latency failures** as recorded above.
  The isolated passes are not a replacement for it. Shared-host CPU pressure was
  observable during investigation, but neither that nor any specific SQL/transport
  bottleneck is established as the cause. No speculative pool, tracing, transaction,
  durability or threshold changes were made. Further investigation must capture a
  failing request path with correlated stages/resource evidence, not rerun until green.
- Temporary profiling source/output/cache files were removed after recording the
  metadata above. Existing dirty changes (including later unrelated review files),
  old uploads, the UI-guide deletion and `ui-guide/` remain untouched. Readiness is 200.
  At that stage latency acceptance remained the M4 blocker; the subsequent explicit
  functional closeout/deferral below is the current decision.

### M4 functional closeout decision and M5 handoff (2026-10-16)

- The user reports that this server is a **laptop** and explicitly selected
  **Close functional M4 with documented laptop-dev performance exception; carry latency
  work forward**. The narrowed read-only Sandbox assistant milestone is functionally
  closed, with the implementation and acceptance limits above retained.
- This is an accepted local-development performance exception, **not** a latency fix,
  green full-suite result, production-readiness claim or changed acceptance threshold.
  Laptop/resource constraints are plausible context, not a proven failure cause.
  Full **674 passed / 2 latency failures** and final focused **2 passed / 1 failed**
  remain the actual test evidence. Carry failing-path profiling and representative-host
  validation into release acceptance (M7/M8); do not silently waive it for production.
- User explicitly chose conservative Markdown consolidation: remove stale active status
  and superseded kickoff directions, preserve plans/history/reviews/UI-guide. No Markdown
  files were identified as safe redundant deletions. Retained the old recovery handoff
  as incident history and updated it to point forward; replaced the old M2/M3 kickoff
  prompt with a canonical M5 handoff link.
- [M4 closeout / M5 kickoff](./handoffs/2026-10-16-m4-closeout-m5-start.md) contains the
  acceptance matrix, exact remaining performance debt, environment/service/fixture
  state, M5 authority question, implementation order and ready-to-paste session prompt.
  M5 must remain separate from owner-private sandbox history/holds; its original
  customer-web approval requirement needs an explicit authority/scope decision.
- This closeout changes documentation only. No new code/test/dependency/schema/runtime
  change, restart, reset, upload cleanup or production/infrastructure/webhook change.
  Closeout readiness **200**, frontend login **200**, `.env.local` remains ignored.
  No new test run was needed for documentation-only changes; prior failures are preserved.
  Dirty worktree, untracked reviews and UI-guide state remain intact. No commit made.

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
| LG15 Console turnover (n8n → CSA) | M0–M3 implemented; M4 functionally closed with accepted laptop-dev performance exception. M5 Telegram slice approved/running on guarded dev0300 with platform-admin UI, synthetic Telegram acceptance, live reader denial and representative tenant-admin/staff replay verified. M3 tenant-role replay and broader M5–M8 pending. | Latest full lint/tests **692 passed**, ASGI/TCP ack p95 **108.284 / 174.623 ms**, unchanged <200 ms. Frontend 12+2/lint/types/build pass; controlled live response-loss retry, ownership, approve/edit/reject, scoped denials and fresh post-release delivery verified. No production-capacity waiver or public deployment. |
| LG14 Teach-a-Task Virtual Desktop | Planned: Dedicated desktop container sandbox, user action recording, playbook compiler & scheduled replay | `docs/langgraph-rebuild/LG14_teach_task_desktop_agent.md` |

Console M0–M1 review/verification (2026-10-15):

### M3 authenticated KB upload and lifecycle (2026-10-15)

- Implemented CSRF/session-scoped multipart upload (1–3 PDF/DOCX/CSV, 10 MiB/file,
  31 MiB aggregate, 8 KiB per-part headers), server-only UUID storage names, signature/
  extraction validation, tenant-key idempotency and durable atomic publication before
  queue/file/batch/audit commit. Machine and reader writes are denied.
- Added typed source metadata and authorized original downloads, safe public job DTOs
  without storage paths/requesters/arbitrary metadata, document/source deletion and
  explicit audited orphan/partial/deleted-file reconciliation. Already-open downloads
  survive unlink and close on disconnect. Gap resolution and audit are atomic.
- Tenant locks, managed file checks and queued-at/hash tombstones prevent in-flight
  embedding, retry and legacy-path jobs from resurrecting deleted content. Deletion
  between path resolution/read is a per-file error, not a whole-job abort. Deleting
  linked content also removes its duplicate source aliases; deliberate new uploads
  after deletion are allowed. No legacy source import or automatic tombstone purge.
- Adopted platform `/knowledge-base` and tenant `/portal/faq` with stable retry keys,
  5-second job/source/version/model/gap polling, original downloads, confirmed deletion,
  role-derived controls, explicit failures and truthful queued/worker setup labels.
- Synthetic disposable-DB follow-up: **57 passed** across upload/worker/streaming/
  agent/contract checks. Includes all three types through HTTP → queue → hash worker →
  tenant search; concurrent idempotency/conflict; roles/CSRF/machine/tenant scoping;
  invalid/empty/type/path/four-file rejection; exact 10 MiB and 31 MiB bounds; partial/
  retry/delete/legacy embedding races; original downloads/disconnect; publication
  rollback/lost COMMIT acknowledgement and storage reconciliation/audits.
  Final three-format search/binary-contract/projection selection **3 passed**
  (overlaps the 57 selection); each format was queried and retrieved, not just stored.
  Earlier broader identity/migration/RLS selection **103 passed / 1 failed**;
  the sole immutable-context fixture error was fixed and its upload test passes.
- `make lint` passes; isolated frontend **8 transport checks**, ESLint, types,
  production webpack build and safe build traces pass. Disposable-only `make db-reset`
  succeeded at **0280**, verifying all three new tables have RLS; its container was
  removed. Persistent-dev activation is recorded separately below.
  Full `NO_LOCAL_ENV=1 ENV=ci make lint test`: **607 passed, 2 failed** in 25m37s.
  Both failures are the existing 200 ms ingress acknowledgement thresholds:
  ASGI p95 **266.718 ms**, TCP **887.834 ms**. Both completed 150 turns and
  recorded 150 pending deliveries. No threshold was relaxed or cause inferred.
  This full run began before the final source-read/download-resource follow-ups,
  which are covered by the 57-pass and final 3-pass focused selections plus final lint.
  Both unchanged benchmarks subsequently pass together in isolation: **ASGI 123.840 ms**,
  **TCP 137.444 ms**, 150 completed turns/pending deliveries each (**2 passed**).
  No cause for the variability or clean full-suite acceptance is claimed.
- **Authorized isolated-dev activation:** verified `csa-dev/postgres` on loopback 5434
  and forward-applied only **0280**, transactionally and without a reset. Restarted only
  console gateway 18081/frontend 13000; added the guarded `scripts.dev_kb_ingest` task.
  It requires `csa_app` on the exact dev database, a dedicated absolute storage root,
  and credential-free loopback HTTP TEI (or explicit dev hash embeddings). It claims
  only the explicit synthetic tenant, including its onboarding state; it does not start
  the full worker, delivery, bots or other agents.
- Gateway and KB tasks override `KB_UPLOAD_ROOT` to
  `$HOME/.local/share/csav2-dev/kb-uploads`; the configured value resolved to the
  repository root and was not used. No environment-file contents or credentials were
  displayed, and the file was not edited.
  This override is task-local, not a global configuration repair. See
  [startup instructions](./CONSOLE_API.md#isolated-dev-rollout-prerequisites).
- **Live platform-admin browser evidence:** user signed in privately; enabled only the
  synthetic M1 tenant's two KB switches. Four-file selection was rejected. A malformed
  DOCX fixture was rejected with 422; corrected PDF/DOCX/CSV upload returned 202 and
  reached `done`, one chunk each through the configured loopback TEI (`tei-384`).
  All three authenticated original downloads returned 200 with matching SHA-256,
  attachment headers and `no-store`. Reuploading the CSV reported duplicate/version 1
  without adding chunks. Confirmed source deletion removed both CSV aliases/shared
  document; confirmed PDF document deletion also removed its source. Both returned 204,
  deleted-source downloads returned 404 and compatible chunks fell from 3 to 1.
  Reconciliation returned 200/zero removals and preserved the remaining DOCX.
  Tenant-scoped identity reads corroborated upload, source/document deletion and
  reconciliation audit events; exactly one source file remains.
- Browser acceptance exposed a four-placeholder/two-argument `list_gaps.sql` regression.
  Fixed both console and KB-agent callers, typed nullable SQL booleans, and added real-DB
  HTTP coverage for unresolved listing, reader denial, resolve disappearance and audit.
  Rollout upload/agent/launcher/contract selection **34 passed** (includes **11** launcher
  guard tests); final `make lint` passes. Selections overlap; do not sum them.
- **Not tested live:** M3 tenant-admin/reader browser replay (roles/tenant isolation are
  covered by HTTP tests), populated/resolved gap UI, changed-content version progression,
  crash/retry races, customer conversation retrieval, Gemini/other providers or public
  deployment. Loopback TEI ingestion success is not a claim about Gemini failover.
  The KB switches remain enabled for synthetic tenant `m1-browser-20261015`; one
  `m3-smoke-shipping.docx` document/chunk remains as a clearly synthetic dashboard demo.

### M3 direct retrieval follow-up (2026-10-16)

- User reports successful additional PDF ingestion (10 chunks) and synthetic
  `demo-faq.csv` ingestion (2 chunks), each done in one attempt. Those job results
  alone do not establish download, changed-version or deletion acceptance.
- With permission, ran a read-only local check using existing `DefaultKbEmbedder`,
  `kb/search_chunks.sql` and `KbInput` defaults (threshold **0.65**, limit **3**).
  Guards verified dev mode, exact `csa_app` database target on loopback 5434 and
  credential-free loopback TEI; hash mode was explicitly refused. No attached
  environment-file snapshot or credentials were inspected.
- For the synthetic M1 tenant, **“What is the demo return window?”** retrieved the
  expected `demo-faq` return passage at cosine similarity **0.860727**. The paraphrase
  **“How many days do I have to return a demo item?”** retrieved the same passage at
  **0.898810**. Both returned “Synthetic returns are accepted within seventeen days.”
  Model label was **tei-384**; no threshold adjustment or fixture re-ingestion.
- Each query repeated with a random foreign tenant RLS context while still requesting
  M1 returned **zero rows**. All database transactions were read-only; no gap records,
  document changes or full agent/coordinator invocations. Non-demo passage contents
  were not printed.
- This verifies the existing embedding/search/threshold retrieval path, not complete
  customer answering. Telegram tenant mapping, conversation orchestration, policy/
  approval, composition and delivery remain untested in this follow-up. No services,
  bot/webhook settings or production resources were changed.

### Telegram acceptance preflight (2026-10-16)

- Metadata-only isolated-dev inspection found one enabled Telegram endpoint on
  `local-dev-telegram` (pilot), and none on the M1 browser tenant (onboarding).
  Existing M1 uploads are therefore not available through that bot's tenant scope.
- User selected synthetic-only acceptance on the existing dev Telegram tenant,
  confirmed the identified bot is dedicated to development, and requested that its
  webhook remain unchanged. Read-only Telegram `getWebhookInfo` reported a registered
  URL matching the configured dev base and existing endpoint, zero pending updates
  and no reported delivery error. Read-only `getMe` identified the public bot for
  the user; no tokens, webhook URLs or credential contents were displayed.
- User confirmed sending the exact synthetic routing probe. A subsequent read-only
  query found no matching ingress event in the isolated dev database within the last
  15 minutes. **Routing is not verified; Telegram end-to-end acceptance is blocked.**
  A registered webhook alone does not establish that this gateway received the probe.
- No polling, webhook changes, full conversation/delivery workers, fixture copying,
  tenant remapping or external message sends were performed. The existing poller
  removes the webhook on startup; the standard worker/delivery claims are not
  tenant-scoped. Neither was started as a shortcut. Diagnose the dev routing before
  enabling any conversation processing or delivery.
- Subsequent user-operated Caddy correction restricted the dev site to webhook POSTs
  forwarded to gateway 18081; user reports non-webhook `/readyz` now returns 404.
  A fresh exact synthetic probe was corroborated in isolated-dev ingress as
  **completed, one attempt** (2026-10-16 02:19:09 UTC); user also reports a bot reply.
  This resolves the earlier missing-ingress blocker, not KB-answer acceptance.
- Read-only host process inspection then found existing `apps.worker.main` and
  `apps.delivery.main` processes (including wrapper/child pairs). They were not started
  or stopped during this acceptance check, and their effective configuration/launch
  provenance is unverified. VS Code forwarded ports do not identify background workers.
  Do not start duplicate runtime processes or assume the current reply used KB;
  verify the existing runtime's dev-only scope before further live processing.
- Further preflight: existing runtime processes report `ENV=dev` and this repository
  as their working directory. At socket inspection time the worker had five and
  delivery two established connections to loopback 5434, with none to other standard
  PostgreSQL ports. Their DSNs were absent from process environment, so this is
  connection evidence, not complete effective-configuration attestation; a later
  socket-port/DB-session correlation returned no matching rows.
- The Telegram tenant has only two capability registry rows, no KB/knowledge entry,
  and zero active KB documents. Its generic clarification reply therefore does not
  demonstrate KB use. Tenant KB provisioning is a prerequisite to uploading the
  synthetic FAQ and testing a knowledge question. Browser automation was unavailable
  (connection timeout); no fallback direct DB writes, capability bypass, process
  restart, shadow-mode change or synthetic document copy was performed.
- User authorized an isolated-dev catalogue backfill for `local-dev-telegram`.
  Reused the console onboarding `_AGENTS` definitions, Pydantic input schemas and
  `console/provision_capabilities.sql` under application-role tenant transactions.
  Catalogue increased **2 -> 9**: added ecommerce, escalation, invoice, KB, order,
  order approval and ticket entries, all **unsubscribed/off**. Existing booking and
  subscription rows were compared and preserved unchanged. Repeated provisioning
  produced identical rows; a fresh post-commit read confirmed persisted state.
  Recorded `capability.catalogue_backfilled` with null actor and explicit authorized
  maintenance metadata rather than impersonating a console user. No schema/code change,
  reset, enablement, source copy, service restart or routing change. This repairs this
  older dev tenant only; no general existing-tenant backfill endpoint was added.
- Subsequent user enabled the Telegram tenant's KB subscription/switch and uploaded
  synthetic `demo-faq.csv`. Job `f0412a56-82a7-4232-88ff-844e40b93d7f`
  was durably queued (zero attempts), but the frontend reported a runtime timeout.
  Both gateway `/healthz` (no DB I/O) and `/readyz` timed out. Process inspection
  showed a blocked main thread, a terminal-I/O waiter and open app/identity transactions
  with DB lock waiters; this is not evidence of a TEI or file extraction failure.
- User tried Ctrl+Q without recovery and explicitly authorized gateway-only restart.
  Graceful termination did not exit; the exact verified gateway PID was force-stopped
  and the guarded VS Code gateway task relaunched. No frontend, worker, delivery,
  proxy or database reset/change. Replacement `/healthz` returned 200 in ~6 ms,
  `/readyz` 200 in ~32 ms and frontend login 200 in ~192 ms; DB lock waiters fell to
  zero. User then confirmed the KB page loads and shows the original queued CSV job.
  No duplicate upload was needed. Exact blocking cause remains undetermined; terminal
  output backpressure is only a lead, not a proven root cause or permanent code fix.
  Telegram-tenant ingestion remains pending; zero attempts means it has not been
  claimed, and the dedicated KB-only task still targets M1.
- With further authorization, claimed exactly the known queued Telegram-tenant FAQ
  job in a tenant-scoped transaction (unexpected job identity would roll back) and
  processed it through existing `KbIngestProcessor`, exact isolated `csa_app` guards,
  dedicated upload root and loopback TEI. Job finished **done**, one file, zero errors.
  This was a bounded run, not a new persistent worker. Existing M1 task unchanged.
- Telegram-tenant direct retrieval now passes at unchanged threshold 0.65/limit 3:
  exact return-window question **0.860727**, paraphrase **0.898810**, both retrieving
  the synthetic seventeen-day passage. No cross-tenant copy or threshold adjustment.
- User then asked the exact question through Telegram and received handoff text.
  Trace `59a3d8ac-0adf-4d9d-acfd-c1dd145a5056` corroborates completed ingress:
  JEV classified `refund_returns`; planner produced **zero tasks**, strategy
  `ask_clarifying`, confidence 0.6; validation changed strategy to `escalate`.
  Only escalation agent execution is recorded, composition had zero facts and the
  gate sent the system reply. **KB was not invoked**. The policy has an existing
  clarification-count escalation bound; do not remove it or infer an embedding failure.
  Telegram KB-answer acceptance remains blocked at planning/routing, despite successful
  ingestion and direct retrieval. No live policy/provider/runtime change was made.
- Planner follow-up: clarified the shared LLM planning prompt to route general policy/
  return-window/warranty/shipping/documentation questions to read-only `kb_agent.query`,
  even when classification says `refund_returns`. Explicitly distinguishes these from
  customer-specific refund/cancellation/eligibility/mutation requests and retains
  required inputs, button/human safeguards and existing clarification bounds.
  No deterministic keyword override or gate/validation relaxation was added.
- Provider/plan regression selection **35 passed**, including exact/paraphrased KB task
  validation after prior clarification, missing-task refund escalation bounds and existing
  invalid-agent/button-action rejection. `NO_LOCAL_ENV=1 ENV=ci make lint` passed.
  Synthetic-only live configured Groq planner checks selected KB query for both questions
  in two runs. Actual refund request selected `ticket_agent.create`, not KB lookup or
  button-only order mutation; no tasks were executed. Initial live harness incorrectly
  required an empty escalation plan for refunds, and was corrected to accept legitimate
  support-ticket planning; that initial assertion failure is not a clean pass.
  Mock tests verify prompt/contracts, not model reliability guarantees.
- Existing conversation worker has not been restarted to load this prompt change.
  Telegram KB-answer/composition/gate acceptance remains pending. No schema change,
  database reset, Telegram message send or full-suite rerun in this prompt-only follow-up.
- User subsequently authorized replacement of the existing dev conversation worker.
  Added a guarded VS Code conversation-worker task (all dev tenants/scheduled jobs,
  not tenant-scoped). Initial `runpy` launcher caused a Pydantic forward-reference
  error after claiming the synthetic turn; corrected it to normal module import and
  `asyncio.run(main())`. Standard stale-lease recovery completed that turn on attempt 2,
  but it intentionally followed the muted route from the prior handoff, with no delivery.
  User then authorized releasing only this synthetic conversation; existing tenant-scoped
  release SQL and an explicit maintenance audit restored AI ownership. No gate or mute
  safeguard bypass and no other conversation changes.
- **Telegram KB answer acceptance passed:** user resent the question and received
  “Synthetic returns are accepted within seventeen days.” Trace
  `973cf19a-e31e-4c18-978d-342d5e03f300` corroborates completed ingress, JEV
  `refund_returns`, planner/validation **kb_agent, auto_reply, confidence 0.88**,
  successful KB execution, composition with one fact, verification true, policy gate
  **send/auto**, one emitted message and delivery **delivered, one attempt**.
  No Telegram API call was made by the assistant to send the customer test message.
- Restart also surfaced a pre-existing scheduled `mute_expiry` SQL error referencing
  missing `conversations.updated_at`; not changed as part of this planner fix.
  Final acceptance does not establish general planner reliability, real refund
  execution, Gemini failover or a clean full-suite result.

### M2 agent controls and runtime visibility (2026-10-15)

- User subsequently confirmed the guided tenant-admin/read-only checks and cleanup.
  Those actions are user-confirmed, not independently replayed; populated-run and
  live-provider browser acceptance remain unverified.
- Follow-up dev smoke (15:49 UTC): user reported `Not Found` on all M2 views.
  Served OpenAPI confirmed the old gateway lacked `/agents`, `/overview` and `/runs`.
  With explicit user approval, restarted only the verified loopback gateway task on
  18081; readiness and all three routes then passed. The shared browser now has the
  user's authenticated platform-admin session: selected synthetic M1 tenant and
  verified tenant subscriptions, Agents catalogue and Runtime activity load without
  `Not Found`. Activity correctly shows zero recorded runs. No switches/data changed;
  tenant-admin/read-only mutation checks and populated-run browser acceptance remain open.
  This supersedes the no-restart/backend-not-loaded observation below.
- Implemented `/console/v1/agents` and specialised metadata with independent subscription,
  tenant switch, implementation stage, setup/circuit and role permissions. Unknown agents
  and MyInvois are not implemented; booking/subscription writes are prepared-only; invoice
  reads do not imply PDFs/issuance. Default marketplace runtime has no adapters. TEI is
  configured-but-unverified, with actual matching/incompatible active KB chunk counts;
  hash embeddings are explicitly lexical. Removed hard-coded healthy/compliance metrics.
- Added tenant-scoped `/overview`, ID-keyset `/runs` and `/runs/{id}` execution metadata.
  Raw inputs/outputs/customer text are excluded; query errors propagate. Platform and
  tenant agent/activity screens share session transport and reuse incremental trace polling.
  Platform users select a tenant, tenant admins cannot alter subscriptions, and staff/
  readers have read-only controls. Required safety switches cannot be turned off.
- Closed the runtime enforcement gap left by static planner descriptions: each production
  attempt rechecks current switches without using registry discovery caches. Both disabled
  switches block drafts and existing callbacks in separate dependency instances. Retry
  disable stops the next attempt. Console KB execution is gated; queued uploads are not
  claimed while disabled and spend no attempts. Already-started/claimed work may complete;
  this is not cancellation or distributed breaker management.
- Focused catalogue/API/identity/registry/runner/contract selection: **74 passed**.
  Follow-up order-callback/runner/timeline/KB-queue/worker selection: **77 passed**.
  These overlap and are not additive. `NO_LOCAL_ENV=1 ENV=ci make lint` passed.
  Final isolated frontend check passed **6 transport/routing checks**, ESLint, Next
  type generation, TypeScript, production webpack build and safe build-trace checks.
- Full `NO_LOCAL_ENV=1 ENV=ci make lint test`: **559 passed, 32 failed** (23m17s).
  It exposed lifecycle mocks missing the newly used TEI settings and golden fixtures
  missing registry provisioning. Corrected those fixtures without weakening enforcement,
  added PostgreSQL-bigint cursor bounds and equal-timestamp pagination evidence, and
  reran gateway lifecycle + all golden cases + runtime pagination + snapshot:
  **45 passed**. Final Python lint passes. These selections overlap earlier evidence.
- The full run's remaining load failure was ASGI ack p95 **853.178 ms** versus the
  unchanged **200 ms** limit. Subsequently reran both transport benchmarks alone:
  **2 passed, 2 deselected**; ASGI p95 **92.637 ms**, TCP p95 **126.684 ms**,
  each with **150 completed turns and 150 pending deliveries**. No threshold changed.
  This is actual passing isolated performance evidence, but does not establish the
  cause of earlier failures or a clean full-suite pass. The complete suite was not
  rerun after fixture corrections; release-level full-suite repeatability remains open.
- No schema/dependency change, reset, persistent-dev data mutation, provider call or
  service restart in this M2 implementation. Tests use synthetic disposable Postgres.
  Existing loopback login/health respond, and anonymous `/agents` redirects to login.
  The existing gateway must be deliberately restarted to load new backend routes;
  authenticated M2 browser acceptance, live provider checks and deployment remain pending.

### Isolated dev setup and browser smoke check (later the same day)

- Verified Compose project/service `csa-dev/postgres`, container `csa_dev_db`,
  dedicated volume and host port **5434** before connecting. Applied missing
  migrations **0250, 0260, 0270** transactionally with ledger stamps, without
  resetting data. No production database or existing service was changed.
- Enabled `csa_identity` login; the user set its password with interactive psql,
  configured the ignored local env file and bootstrapped `admin@example.test`.
  A malformed identity URI was diagnosed without printing secret values; the user
  repaired it with a local password prompt and percent-encoding. Both database
  roles now connect to the approved dev endpoint; identity privilege readiness passes.
- Started only the loopback gateway **18081** and Next frontend **13000** using
  VS Code tasks. Frontend API origin is `http://127.0.0.1:18081`; browser Origin
  is `http://localhost:13000`, privately forwarded through VS Code. Cookies are
  non-Secure for this loopback HTTP test only. No worker/delivery/bot/provider
  processes, public proxy changes or runtime cutover.
- Gateway `/healthz` and `/readyz` return 200. Live same-origin checks pass:
  login 200; protected anonymous pages redirect; legacy API/deferred projects
  return 404; anonymous identity returns 401; CSRF cookie/no-store and rejected
  unauthenticated mutation verified without displaying tokens.
- Browser login page inspected directly. User confirms login, forced password change,
  administration home, tenant list and member screen all work. DB audit confirms
  login and password-change events. The agent-shared browser did not inherit the
  user's signed-in session, so authenticated screen acceptance is user-reported,
  not an agent-operated end-to-end test. Subsequent guided lifecycle results are
  recorded below; disposable integration evidence is separate.
- Isolated frontend build/typecheck/lint and five transport checks passed again
  before launch. No additional full test suite or reset was needed for this setup.
  Existing TCP performance acceptance failure remains unresolved.

### Guided M1 browser lifecycle checks (2026-10-15)

- User created synthetic tenant `m1-browser-20261015` and accepted invitations for
  `m1-admin@example.test` (tenant admin) and `m1-reader@example.test` (read only).
  Database checks corroborate tenant creation, both scoped memberships and consumed
  invitation tokens; all nine capability switches remain disabled.
- User reports tenant-admin access to platform administration is blocked, reader
  management controls are unavailable, direct team access is blocked and used
  invitation acceptance is rejected. These are user-operated browser results;
  the agent-shared browser still has a separate unauthenticated session.
- Logout acceptance subsequently passed: user inspected the reader's incognito-window
  Network request and reported `POST /console/v1/auth/logout` returned **204**.
  A follow-up DB check corroborated two reader logout audit events and two revoked
  reader sessions (latest logout 14:19:35 UTC). Earlier redirect-only checks had
  no matching audit; the explicit reader-window request resolves that evidence gap.
  The user also previously confirmed direct portal access after logout redirects to login.
- Login troubleshooting: the user privately recovered the dev platform-admin password
  with an audited local operation. An attempted targeted throttle cleanup deleted zero
  rows; the remaining buckets did not match the intended admin email. Explicitly entering
  the intended email and recovered password restored login. No credential values logged.
- Services were restarted using the existing loopback tasks after both were found
  stopped. Gateway readiness and frontend login returned 200 afterward. No worker,
  delivery process, production connection or reset was involved.

### Earlier implementation review

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

Next bounded slice: complete authenticated **M2 browser acceptance** using the new agent/
activity screens, then **M3** authenticated three-file upload,
download/delete safety and job-progress UI can proceed independently after M1. No additional
provider keys are needed for these provider-neutral packages. Both load transports now
pass in isolation, but obtain a clean full-suite rerun and investigate repeatability
before treating the release as verified.

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
delivery. Membership switching/account attachment, browser visual checks, M3+ business
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
