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
M0 source adoption and M1 identity/onboarding are now implemented locally. Deployment and
runtime cutover have not been performed. See the implementation record and review evidence
in the detailed plan; this phase as a whole is not complete.

## Implementation review (2026-10-15)

- Implemented: isolated identity pool and migration 0270, sessions/CSRF/RBAC, invite/reset/
  password lifecycle, audited tenant/member onboarding, disabled capability provisioning,
  adopted authentication/admin/portal shell, static OpenAPI contract and deferred-route gates.
- M2 groundwork already implemented: role-checked capability switches, atomic before/after
  audit, required-safety capability guards, and initial platform entitlement UI. Runtime
  readiness metadata, tenant switch UI, specialised navigation and run visibility remain open.
- Review fixes: UTF-8-safe password equality and rejection of password resets for pending
  invitations, preserving invitation membership creation. Synthetic regression tests pass.
- Final review lint passed. Session selection: 33 passed; fix/config/lifecycle/contract/
  password selection: 30 passed (overlapping selections, not additive).
- The full run before these two fixes completed with 564 passed and one failed TCP ingress
  benchmark (p95 1021.433 ms versus the unchanged 200 ms limit). Do not mark full validation
  green or infer a cause from that result alone. The exact standalone rerun also failed
  at p95 451.844 ms; diagnosis remains open. The five frontend transport/routing tests
  were rerun successfully during review.
- Next: finish M2; M3 authenticated KB upload/lifecycle can follow or run independently
  once its storage/deletion semantics are tested. No live browser/provider validation or
  deployment is claimed.

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
- [ ] Live browser acceptance of M1; finish remaining M2 runtime/readiness/UI work.
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
