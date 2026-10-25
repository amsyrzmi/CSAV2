# Progress (archived entries before 2026-10-20)

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
  [startup instructions](../CONSOLE_API.md#isolated-dev-rollout-prerequisites).
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
failed the 200 ms target. See [validation report](evals/lg08_validation_report.md) for scope.

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
[RUNBOOK.md](../RUNBOOK.md).
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
