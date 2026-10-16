# LG15 — Console turnover (n8n → CSA)

## Source-based revision (2026-10-15)

The console source is now available at `CSA_WebConsole/csa-console/`.
The user approved replacing its backend logic, retaining the Next.js UI and using
**one CSAV2 backend**, with project-management/terminal/Hermes/workroom features
disabled for a separate phase.

The authoritative implementation sequence and source-to-target route inventory are in
[the detailed migration plan](../handoffs/2026-10-15-console-source-migration-plan.md).
It supersedes earlier assumptions about a separate console backend, actor headers,
unknown console endpoints and source availability in this document and older handoffs.
M0–M3 source adoption, identity/onboarding, controls/activity and KB lifecycle are
implemented. Guided M1/M2 isolated-dev acceptance and agent-operated platform-admin M3
browser checks pass; M3 tenant-role browser replay remains pending. Public deployment and
runtime cutover have not been performed. See the implementation record and review evidence
in the detailed plan; this phase as a whole is not complete.

### Runtime follow-up (2026-10-16)

After the explicitly approved clean-dev recovery, fresh synthetic KB ingestion and
Telegram delivery passed. Subsequent isolated investigation reproduced a gateway
stdout-backpressure stall and implemented user-approved bounded background logging:
liveness remains responsive, readiness exposes stalls/overflow/writer errors and lost
event counts. A real Next.js proxy socket-reset test verifies HTTP 500 can follow a
committed upload and that an explicit same-key retry reuses the job.

Final offline lint/full suite: **655 passed**; final focused logging/gateway/poller
selection: **29 passed** (overlapping, including an additional exact threshold test
added after full-run collection). Both unchanged 50-rps latency benchmarks pass in the
full run. Frontend transport tests: **9 passed**, changed-test ESLint passes.
The historical hang/proxy-error cause is not established. No new live rollout, production
or infrastructure changes were made. Dev gateway/frontend were not listening at the
final check; guarded startup and fresh authenticated acceptance remain pending.
See [detailed investigation and recovery evidence](../PROGRESS.md).

## M3 KB lifecycle implementation (2026-10-15)

- Added authenticated bounded multipart uploads, server UUID storage, durable atomic
  publication, tenant idempotency, typed original-source metadata/download and audited
  document/source deletion. Public contracts omit raw storage paths/internal job metadata.
- Migration 0280 uses staged tenant RLS for batches/files/hash tombstones. Shared tenant
  locks and source/hash checks before deduplication and after embedding fence deletion
  races and retries, including legacy jobs. Explicit audited reconciliation removes only
  UUID-pattern orphan/partial/deleted files, preserving active and unknown legacy files.
- `/knowledge-base` and `/portal/faq` provide uploads, 5-second job/per-file progress,
  duplicates, versions/model labels, original downloads, confirmed deletion and gaps.
  Reader controls are absent; backend role/CSRF/tenant checks remain authoritative.
  An accepted queue job does not mean the worker is running or the source is searchable.
- Focused synthetic upload/worker/agent/streaming/contract checks **57 passed**.
  Final three-format retrieval/binary-contract/projection **3 passed** (overlap).
  Python lint, isolated frontend
  eight transport checks/lint/types/build and disposable-only `make db-reset` pass;
  disposable reset verifies 0280 and RLS on all three new tables.
- Authorized persistent-dev rollout forward-applied 0280 without reset, restarted only
  gateway/frontend and started a guarded KB-only loop for the explicit synthetic M1 tenant.
  Task-local storage uses `$HOME/.local/share/csav2-dev/kb-uploads`, not the configured
  repository-root value. No environment-file edit, full runtime or production access.
  Platform browser PDF/DOCX/CSV ingestion via loopback TEI, byte-verified downloads,
  duplicate suppression, confirmed source/document deletion, 404 after deletion and
  active-source reconciliation pass; audit events are corroborated. One synthetic DOCX
  remains. Both synthetic-tenant KB switches stay enabled for testing.
- Live acceptance found/fixed gap-list argument drift in console/agent callers; the
  rollout regression/upload/agent/launcher/contract selection **34 passed**, with final
  lint passing. M3 tenant-admin/reader browser replay, populated gap UI, live changed-
  content versions, crash races and customer chat retrieval remain unverified.
  No Gemini failover or public deployment claim.
- Full suite **607 passed / 2 ingress latency failures** (ASGI
  266.718 ms, TCP 887.834 ms against unchanged 200 ms). Final resource follow-ups pass
  focused tests/lint. Both unchanged benchmarks then pass in isolation (ASGI 123.840 ms,
  TCP 137.444 ms), but full acceptance is not green; no cause for the variability is
  claimed. Detailed evidence is recorded in [progress](../PROGRESS.md).

The review snapshots below predate M2 browser follow-up and M3. With explicit approval
the stale isolated gateway was restarted for M2; authenticated platform views loaded
and the user later confirmed the guided tenant-admin/reader checks and cleanup.
Populated-run/provider acceptance remains unverified.

## Implementation review (2026-10-15)

- Implemented: isolated identity pool and migration 0270, sessions/CSRF/RBAC, invite/reset/
  password lifecycle, audited tenant/member onboarding, disabled capability provisioning,
  adopted authentication/admin/portal shell, static OpenAPI contract and deferred-route gates.
- M2 now implemented locally: role-derived catalogue/readiness, independent subscriptions
  and tenant switches, safety guards, specialised inspection navigation, scoped run metadata/
  24-hour overview and ID-keyset pagination. Agents/activity screens reuse trace-event polling.
  Default-provider limitations and KB embedding compatibility are explicit, not healthy metrics.
  Production attempts freshly enforce switches across processes, callbacks and retries;
  console KB execution and background upload claims are gated too. In-flight work is not cancelled.
- M2 focused checks: catalogue/API/identity/registry/runner/contract selection **74 passed**;
  runtime/callback/KB-queue/timeline/worker selection **77 passed** (overlapping selections).
  Python lint and isolated frontend **6 transport checks**, ESLint, types and production
  build pass. No schema/dependency change or reset. Authenticated M2 browser checks remain
  pending; the existing loopback gateway needs a deliberate restart to load the new routes.
- M2 full run: **559 passed, 32 failed**. Lifecycle mocks and golden fixtures were
  corrected for the new settings/registry requirements; a focused gateway/golden/
  runtime/contract rerun **45 passed**, with final Python lint passing. Full-run ASGI
  ack p95 was **853.178 ms** (>200). Subsequent unchanged isolated load checks both
  pass: ASGI **92.637 ms**, TCP **126.684 ms**, 150 completed turns/pending deliveries
  each. No clean full-suite rerun or explanation for performance variability is claimed.
- Review fixes: UTF-8-safe password equality and rejection of password resets for pending
  invitations, preserving invitation membership creation. Synthetic regression tests pass.
- Final review lint passed. Session selection: 33 passed; fix/config/lifecycle/contract/
  password selection: 30 passed (overlapping selections, not additive).
- The full run before these two fixes completed with 564 passed and one failed TCP ingress
  benchmark (p95 1021.433 ms versus the unchanged 200 ms limit). Do not mark full validation
  green or infer a cause from that result alone. The exact standalone rerun also failed
  at p95 451.844 ms; diagnosis remains open. The five frontend transport/routing tests
  were rerun successfully during review.
- Next: authenticated M2 browser acceptance; M3 authenticated KB upload/lifecycle can follow
  once its storage/deletion semantics are tested. Provider validation and public
  deployment remain pending.

## Isolated dev smoke check (2026-10-15)

Applied missing 0250–0270 migrations forward-only to verified `csa_dev_db` on port
5434, with no reset. User provisioned identity credentials and the first admin
interactively. Gateway 18081 and frontend 13000 are running on loopback only.
Private VS Code forwarding exposes `http://localhost:13000` to the user's browser.
Gateway readiness, anonymous route gates and same-origin CSRF checks pass.
User confirms login, required password change, admin home, tenant list and team
screen all load; audit corroborates login/password change. Authenticated browser
checks are user-reported because the shared browser has a separate session.
Guided tenant creation and two invitation acceptances are corroborated by scoped
memberships, consumed tokens and disabled capabilities. User reports role gates and
used-invitation rejection pass. Reader logout subsequently returned HTTP 204 as
observed by the user; DB audit and revoked sessions corroborate it. Post-logout
portal access redirects to login according to the user. These guided lifecycle
checks pass; authenticated M2 browser acceptance and full-suite repeatability remain open.
No public proxy, worker, delivery, provider or runtime cutover changes.

## CONTEXT FOR CODING AGENT
The web console is currently built against n8n: its webhooks and workflows, and possibly direct
database reads. This phase moves every console feature onto the CSA console API (`/console/v1`,
`csa/console/`) and moves each client's runtime from n8n to LangGraph, using the LG09 strangler
flag. n8n core workflows are then retired. Read `AGENTS.md`, `LG09`, `LG13` (console and KB upload
contract), `LG07` (approval semantics), `docs/CONSOLE_API.md`, and
the [current source-based migration plan](../handoffs/2026-10-15-console-source-migration-plan.md)
first.

Two separate turnovers must not be confused:
1. **Console turnover:** the console UI and backend stop calling n8n and call `/console/v1` only.
2. **Runtime turnover:** a client's customer traffic moves from n8n to LangGraph
   (`clients.config.runtime`). A client can be on the new console while still on the n8n runtime,
   as long as the console endpoints it uses read tables that both runtimes write.

## Boundaries (unchanged from LG13)
- The browser never sees the CSA token, provider secrets, or the database. Next.js forwards
  same-origin requests to the CSAV2 gateway; session identity and tenant memberships are
  verified in CSAV2. There is no separate permanent console business backend.
- Every request resolves to `client_id` + actor + role. Every database call runs under
  `tx(pool, client_id)` (RLS GUC) and filters by `client_id`.
- Staff decisions from the console must reuse the same approval and pending-action code paths as
  Telegram callbacks. These have single-use, expiry, tenant scoping, `approval_audit`, and
  idempotent outbox delivery (LG07). Do not build a second approval implementation.
- Keep SQL in `db/sql/`, use Pydantic in/out models with `extra="forbid"`, use psycopg 3 async,
  make migrations additive and ordered, and use no silent fallbacks.

## Step 0 — Discovery (core inventory and M0 adoption complete)
- Source: copied Next.js UI plus a separate synchronous SQLAlchemy/FastAPI API, n8n
  chat/file/poll calls, direct CSA DB reads/writes, and a large optional PM runtime.
- Reviewed core routes and feature mapping are in the detailed migration plan. No copied
  envs, setup scripts, applications or production n8n resources were executed.
- Any deployed workflow inventory or real user/file import is a later explicitly approved
  operation, not a prerequisite for rewriting the dev console.
- Inventory every console → n8n call: webhook path, method, payload, response, and the screen that
  uses it. Core inventory is recorded; implementation should keep it current as routes change.
- Re-export the **current** n8n workflow list read-only (name, active, trigger and webhook paths
  only; never node credentials). The 2026-09-20 backup inventory is in the handoff and may be
  stale.
- Produce a mapping table: console feature → n8n source → CSA endpoint (exists / to build) →
  tables read/written. Store it in `docs/handoffs/` and keep it updated.

## Step 1 — P0 fixes before any console traffic
1. **Invoice download link.** Remove the hard-coded fallback key and the `CREDENTIALS_KEY`
   fallback in `csa/console/router.py`. Require `INVOICE_SIGNING_SECRET` and raise a configuration
   error if it is missing. Implement the served route `GET /api/v1/invoices/{id}/download`: verify
   the signature and expiry with `verify_invoice_signature`, run under the tenant scope, and return
   404 when the signature is invalid or expired. Decide where invoice files come from
   (storage path or generator).
2. **RBAC and sessions.** Implement cookie-backed sessions and explicit tenant memberships
   in CSAV2. Derive actor identity/role from verified sessions, never browser-supplied
   `X-Actor-Id` or `X-Actor-Role`. Roles:
   `platform_admin | tenant_admin | tenant_staff | read_only`.
   Per-endpoint role checks:
   - `is_enabled` (subscription) is `platform_admin` only.
   - `client_enabled` is `tenant_admin`+.
   - Approvals and takeover are `tenant_staff`+.
   - Read endpoints accept every role.
   Separate existing service-token machine principals from human users, and scope them
   explicitly. Do not let machine auth bypass browser-role restrictions.
3. **Audit.** Record capability toggles (who, when, before → after). This probably needs an additive
`capability_audit` migration: `subscription_events` covers customer subscriptions and
requires a `subscription_id`. Add the actor to KB, approval and takeover actions.
4. **OpenAPI exposure.** Serve `/docs` and `/openapi.json` only in dev, or behind the console
   token. Publish a versioned `docs/console_openapi.json` snapshot for the console developer.

## Step 2 — Console API parity (build in this order unless discovery says otherwise)
| # | Feature | Endpoint(s) | Notes |
|---|---|---|---|
| 1 | Conversations inbox | `GET /conversations`, `GET /conversations/{id}/messages` | paginate by time; redact nothing for staff, but no secrets |
| 2 | Takeover / release / human reply | `POST /conversations/{id}/takeover`, `/release`, `/reply` | uses `owned_by` / `ai_muted_until`; reply goes through the outbox with an idempotency key |
| 3 | Approvals queue | `GET /approvals`, `POST /approvals/{id}/decide` (`approve`/`reject`/`edit`+text) | reuse the LG07 service; same bounds (edited text validation), expiry, single-use, `approval_audit` |
| 4 | KB upload | `POST /kb/uploads` (multipart, ≤3 files) → writes `KB_UPLOAD_ROOT/<client_id>/` + `kb_ingest_jobs` row | replaces console-side DB writes; reuse `extract.py` validators before accepting |
| 5 | KB gap answer | `POST /kb/gaps/{id}/answer` (text → FAQ doc + resolve) | needs approval policy decision |
| 6 | Capability config | `GET/PUT /capabilities/{name}/config` validated by `config_schema` | |
| 7 | Live updates | `GET /stream` (SSE: timeline events, new approvals) | polling remains supported |
| 8 | Channels & integrations | status, OAuth start/callback (TikTok exists under `/api/v1/channels`), revoke | secrets write-only |
| 9 | Notification targets | CRUD on `staff_notification_targets`, quiet hours | |
| 10 | Ops | `GET /runs`, `GET /dlq`, `POST /dlq/{id}/redrive`, breaker reset (`csa.ops.runbooks`) | `platform_admin` only |
| 11 | Domain reads | orders, tickets, bookings, invoices (exists), ecommerce orders | per specialised tab |

Each endpoint needs tests for: the authorised role, a forbidden role, the wrong tenant (empty or
404, never another tenant's data), validation errors, and idempotency where it writes.

## Step 3 — Agent parity (runtime)
A client moves to `runtime=langgraph` only after every n8n workflow it uses has a CSA equivalent,
or has been deliberately moved to the signed `n8n_webhook` agent kind (non-hot-path automations).
See the parity table in the handoff. Not ported yet: bookings, refund status, warranty,
subscription, store, quote, account, voice (LiveKit transcript → internal HMAC endpoint), and
KB sheet ingest.

Preparation update (2026-10-15): booking/subscription now have local customer reads and
unexecuted request agents; staff email/research/invoice-write preparation libraries also
exist. These do not satisfy provider-execution parity. See
`docs/handoffs/2026-10-15-wave23-preparation.md` before estimating the remaining work.

## Step 4 — Per-client cutover (LG09 procedure)
`shadow` (compare for at least a week) → `suggest` → `auto`, one client at a time, with the console
switched to `/console/v1` for that client. Rollback means flipping `clients.config.runtime`; no
webhook changes. Set `KB_INGEST_ENABLED=true`, `KB_UPLOAD_ROOT` and `TEI_ENDPOINT` on the server
at the console switch, and confirm the legacy `kb_documents.embed_model` label (re-index if it
differs).

## Step 5 — Retire n8n core
After 2 clean weeks per client: deactivate (do not delete) the n8n core workflows, revoke tokens
hard-coded in workflow JSON (LG09), migrate the Langfuse sink to OTLP before **2026-11-16**, then
enable `FORCE ROW LEVEL SECURITY` and move n8n off the owner role (LG11 M14).

## Acceptance criteria
- [x] Core copied-source discovery and proposed route mapping recorded in the detailed plan.
- [x] M0 reviewed frontend adoption and static contract baseline implemented.
- [x] M1 identity/RBAC/onboarding implemented with synthetic integration evidence.
- [x] Guided M1 browser lifecycle/RBAC/logout acceptance, user-operated and DB-corroborated.
- [x] M2 runtime/readiness/UI implementation with focused disposable integration evidence.
- [ ] Authenticated M2 browser acceptance and full-suite performance acceptance.
- [ ] Every active frontend feature actually uses an implemented CSAV2 endpoint, not n8n;
      the old Python API and deferred PM/voice runtimes are not mounted or started.
- [ ] P0 fixes merged with tests (download route and strict signing secret, RBAC, audit, OpenAPI
      restriction).
- [ ] Parity endpoints implemented with role, tenant, validation and idempotency tests; LG07
      approval semantics proven through the console path (approve/reject/edit/expired/used).
- [ ] Console running against the dev stack (`csa-dev.nexusmandiri.com`) with synthetic tenants;
      end-to-end UI check recorded.
- [ ] Per-client shadow → auto completed with recorded rollback drill; n8n core deactivated.
- [ ] `make lint test` green; `make db-reset` verified on a disposable database for any migration.
