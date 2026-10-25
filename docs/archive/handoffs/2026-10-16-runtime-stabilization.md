# Runtime stabilization and dev-recovery handoff

Updated 2026-10-16. Historical recovery/stabilization record; this is not a production rollout.
For the next session start with [M5 guarded dev and current evidence](2026-10-17-m5-telegram-inbox.md).
The [M4 closeout](2026-10-16-m4-closeout-m5-start.md) retains prior acceptance decisions.

## Read first

- [Repository guidance](../../../AGENTS.md)
- [Current evidence and incident record](../../PROGRESS.md)
- [Console API and guarded dev tasks](../../CONSOLE_API.md)
- [Console migration plan](2026-10-15-console-source-migration-plan.md)
- Relevant [LG03 ingress](../../langgraph-rebuild/LG03_ingress_gateway_channels.md),
  [LG10 jobs](../../langgraph-rebuild/LG10_scheduled_jobs_expiry_sweep.md) and
  [LG15 console](../../langgraph-rebuild/LG15_console_turnover.md) plans.

## Current state and safety

- M4 continuation implemented the explicitly selected read-only **Sandbox assistant**
  and rolled it out after explicit approval despite the disclosed latency failure; see
  [progress](../../PROGRESS.md) and [the M4 API contract](../../CONSOLE_API.md).
  Migration **0290** was applied forward-only to isolated dev, without reset, followed
  by guarded gateway/worker/delivery/frontend replacement. Health/readiness pass.
  User-operated KB and archive checks are metadata-corroborated. Invoice clarification
  reproduced an empty `billing_invoice` / `ask_clarifying` plan with no agent execution.
  The scope fix is now live after a separately approved worker-only replacement.
  Fresh user-operated refusal, sandbox KB and Telegram checks are metadata-corroborated;
  user-operated history reload and browser-tab offline/online recovery pass.
  Reader-role controls/ownership browser replay now passes. With separate explicit
  approval, the user privately registered one synthetic reader through the normal
  invitation flow; no invite token, password or cookie was inspected. Creation is
  disabled, send/archive absent, other-owner active/archived history 404 and cross-tenant
  selection 403. The new account has no own history; existing-history reads remain
  automated-test evidence. The shared browser is left signed in as Synthetic M4 Reader.
  Pre-rollout full validation was **674 passed / 1 failed**: TCP ingress p95 **202.643 ms**
  versus the unchanged `<200 ms` bound. An exact isolated repeat passed, but that does
  not make that full run green. Post-scope-fix full rerun: **674 passed / 2 failed**,
  ASGI p95 **508.717 ms**, TCP **298.288 ms**, unchanged `<200 ms`; lint and disposable
  reset pass. The user explicitly approved worker replacement despite both failures.
  No cause for the latency failures is established. The user subsequently accepted
  functional M4 closeout with a **laptop-dev performance exception**, carrying performance
  work forward rather than declaring tests green. M5 is next, a separate staff surface.
  Latest uninstrumented focused run: **2 passed / 1 failed** (reader backend
  denial and ASGI p95 **93.234 ms** pass; TCP **261.451 ms** fails). Metadata-only
  profiling on disposable databases captured passing runs, not the failure cause.
  Do not increase pools, relax durability, suppress tracing or lower thresholds without
  measured evidence. See detailed metrics and fresh traces in progress.

- Branch `main`; extensive pre-existing modified/untracked work. No commit was made.
  Preserve and integrate the working tree; do not reset, clean, stash or overwrite it.
- The original no-listener state is superseded by the explicitly approved dev rollout.
  At M4 closeout gateway readiness and frontend login return **200**, on loopback 18081
  and 13000. Before any future start/replacement, verify exact processes/ports and obtain
  approval; do not start duplicate workers.
- Use only isolated dev `csa_dev_db`, Compose project `csa-dev`, loopback **5434**,
  database `csa`, roles `csa_app` / `csa_identity` / owner `csa`. Production is on
  **5433: do not connect**. Never change production tokens, n8n, Caddy, Cloudflare,
  hostnames or webhook settings as part of this continuation.
- Do not read/print environment files or attached snapshots, credentials, encrypted
  credentials or session cookies. Existing settings loaders may consume configuration
  internally after sanitized exact-target checks. No environment file was edited.
- Do not reset persistent dev. The original stabilization fixes need no schema change;
  the later M4 sandbox's separately approved forward-only 0290 is now applied.
  Reset verification already passed on disposable containers.
- Preserve old upload files: do not invoke reconciliation or cleanup to remove them.
- `ui-guide/` is excluded from Markdown cleanup and must remain untouched.
- Current managed detached services: `m4-dev-gateway`, `m4-dev-delivery`,
  `m4-dev-frontend`, `m4-dev-worker-scope-fix`. The old `m4-dev-worker` was stopped after
  verifying its exact PID/UID/cwd/command marker. The replacement's job loop is responsive.
  Verify known services before any future replacement; do not start duplicate workers.

## Important incident: clean rebuild, not restoration

During earlier validation the assistant unintentionally dropped persistent-dev
`public`/`lg` schemas: an old Makefile fallback silently selected `csa_dev_db` when
host `psql` was absent, despite Python targeting a disposable database. This was
disclosed. The user explicitly chose and approved a clean dev rebuild.

Recovery completed migrations through **0280** on the already-recreated baseline,
without another drop, and recreated checkpoints. App/identity authentication passed.
The existing read-only role password verifier was preserved. This did **not** restore
old accounts, sessions, documents, history or checkpoint contents.

Reset safety is now fail-closed: no automatic Docker fallback; Python DSN and explicit
`PSQL` command must produce the same PostgreSQL system identifier/database before DDL.
Makefile/Compose defaults are 5434. See `scripts/verify_reset_target.py`,
`db/sql/jobs/reset_target.sql`, `tests/test_reset_target.py`. Host `psql` was unavailable;
disposable reset tests explicitly select their test container.

The user privately created `admin@example.test` and completed the mandatory password
change. Never ask them to share that password.

Recreated synthetic tenants:

| Tenant | UUID | Last verified state |
|---|---|---|
| Local Telegram Development | `76946f31-2b21-4e48-9383-65d1a78029c2` | `pilot`; KB subscribed/enabled; gate policy `auto` explicitly approved |
| M1 Browser Acceptance | `7f797233-684a-47ab-8222-6e887b6480db` | `onboarding`; capabilities disabled |

Each has nine capability entries. DB-only dev-bot mapping was recovered after read-only
identity verification of `@CSAV2_bot`, using its existing webhook public ID.
**The webhook was not changed.** Do not blindly run `setup_dev_telegram`: a new random
endpoint would not match the preserved webhook.

Two old upload files were present before fresh uploads; their metadata was lost and
their integrity was not recovered. The dedicated root is
`$HOME/.local/share/csav2-dev/kb-uploads`. A fresh synthetic FAQ subsequently ingested
two chunks on attempt 1 (job `5f066252-1c96-45c0-af90-1952bb8096d5`).
Telegram trace `202ac6db-acf8-4a4a-a03f-105512bbe71d` completed, KB succeeded,
verification passed, auto/send emitted one message, delivered on attempt 1.
User confirmed: "Synthetic returns are accepted within seventeen days."
This acceptance predates the latest logging/ingress changes.

## Latest implemented changes

1. **Ingress latency:** joined client runtime metadata into the existing tenant-scoped
   credential lookup, removing one sequential round trip (six normal first-contact
   fetches instead of seven). No cache or relaxed authorization/RLS. Removed unused
   `db/sql/ingress/get_client_runtime.sql`; updated `get_channel_credentials.sql` and
   `csa/channels/ingress.py`. New test covers metadata, policy override, wrong-tenant
   isolation and lookup count; existing cutover/callback/dedupe tests pass.
2. **Gateway logging:** an isolated actual gateway with deliberately undrained stdout
   reproduced health timeouts, stack blocked in synchronous structlog output.
   This proves a failure mode, not the historical incident's cause.
   User approved a bounded background writer in `csa/core/logging.py`:
   1,024 queued events plus one active; producer-side formatting preserves context;
   no output I/O on event loop. More than five seconds of backlog makes readiness 503.
   Overflow/writer errors latch secret-free failure/dropped-event count until
   reconfiguration/restart. Mere stall recovers after draining if nothing was lost.
   Shutdown drains with a bounded deadline and raises on failure.
   Gateway checks logging health before DB readiness; `/healthz` stays responsive.
   Poller entrypoint also drains configured logging at shutdown.
3. **Proxy/upload errors:** real Next.js loopback proxy test reproduces HTTP 500 after
   a simulated committed upload when upstream closes its socket. Explicit same-key
   multipart retry returns the existing job; CSRF/content preserved; no automatic
   mutation retry. Existing real-DB lost-COMMIT-ack test passes.
   This explains how a persisted job survives a 500, **not why the old socket closed**.
4. Earlier stabilization: mute expiry restores AI ownership/open status without
   nonexistent `updated_at`; runner failures do not advance success heartbeat, continue
   remaining tenants and become unhealthy after repeated failures.
5. Delivery remains **at least once**: committed-success restart does not resend, but
   remote acceptance followed by crash before durable ack can duplicate the send.

At handoff time these production-code changes had not been loaded into live dev. The
guarded isolated-dev rollout and current live acceptance are now recorded below and in
`docs/PROGRESS.md`; this does not constitute a production rollout.

## Verified evidence

- Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
  **655 passed**, 134 warnings, 1,742.50 seconds. Ruff, formatting, Pyright and import
  boundary pass. Includes disposable-only make reset/migrations/RLS verification.
- Full-run 50 requests/s, 150 completed turns and 150 pending deliveries per transport:
  ASGI ack/scheduled p95 **114.186/120.390 ms**; TCP **129.436/130.810 ms**.
  Both original limits remain **<200 ms**. No threshold relaxation.
- Final focused logging/gateway/identity/poller selection: **29 passed**. Includes an
  extra exact 4.999/5.001-second stall/recovery test added after full-suite collection;
  do not add overlapping counts or claim it ran in that full run.
- Ingress/cutover/ambiguous-commit/both latency selection: **19 passed**.
- Frontend transport: **9 passed**, changed-test ESLint passes.
- Earlier baseline TCP failed at 277.25 ms; profiling showed variable scheduling and
  distributed statement delay. One green full run does not guarantee all shared-host loads.
- No live Gemini failover claim, fresh public acceptance or new production access.
- Temporary diagnostic scripts/logs were removed after evidence was recorded.

## Post-handoff update: guarded dev rollout and acceptance

- The user approved starting only the guarded isolated-dev services. No schema reset,
  old-upload cleanup, production connection, webhook change or infrastructure change
  was performed. The exact guarded command bodies in `.vscode/tasks.json` were started
  as background processes after the VS Code task runner returned “Task not found” for
  those labels. After acceptance, the exact guarded services were relaunched detached
  to remain available. Final checks confirmed gateway/frontend listeners on loopback
  18081/13000 and worker/delivery connections only to dev 5434. Verify current
  listeners/processes before any restart; do not start duplicates.
- Gateway readiness/liveness, authenticated console polling/upload/download, explicit
  same-key retry, completed synthetic KB ingestion, fresh scheduled-job heartbeats and
  one synthetic Telegram return-policy answer are verified in `docs/PROGRESS.md`. The
  user confirmed the expected seventeen-day reply. The committed-response-loss retry
  remains an offline regression, not a live injection.
- Follow-up M3 acceptance also populated/resolved a synthetic gap in the authenticated
  UI, verified cross-tenant gap isolation, and confirmed same-document version 1
  superseded/version 2 active. Disposable race tests passed; a new worker-crash-after-
  chunk-commit test verifies idempotent retry without duplicate chunks. The read-only
  resolve denial is integration-test evidence, not a live reader browser replay. See
  `docs/PROGRESS.md` and the console migration plan for details.
- These rollout records predate the later M4 closeout. Continue from the
  [current M5 handoff](2026-10-16-m4-closeout-m5-start.md); preserve the dirty worktree,
  verify existing services and ask before replacements. Never promote dev or change
  `ENV` to production as an implicit continuation.

## Documentation cleanup performed

Conservative cleanup was explicitly selected. Removed only the redundant, stale
`docs/versions.md` snapshot; `pyproject.toml` and `uv.lock` remain authoritative.
Image provenance/caveat was preserved in the dev runbook. Architecture/overview,
guidance, phase plans, reports, runbooks, previous handoffs and all UI-guide content
were retained. The attached read-only version snapshot was not changed.

## Current next-session prompt

The older stabilization kickoff is superseded by the single
[M5 kickoff prompt](2026-10-16-m4-closeout-m5-start.md#paste-into-the-new-session).
Recovery evidence and safety instructions above remain applicable.
