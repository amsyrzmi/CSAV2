# CSA WebConsole → CSAV2: source-based migration plan

Date: 2026-10-15. Status: **M0–M1 implemented and reviewed locally; M2 groundwork present, remaining M2–M8 pending. Full-suite performance acceptance is not green; no deployment or runtime cutover.**

## M0–M1 implementation record (2026-10-15)

- Adopted the reviewed Next.js 16.3.8 / React 19.2.8 frontend with exact lockfile
  versions and lifecycle scripts disabled during install. Installed Next documentation
  was read before frontend edits. The allowlisted temporary build excludes env files,
  nested Git metadata, archives, the copied API and unused PM runtime dependencies.
  The original checkout remains reference; no index changes or commit were made by Codex.
- Active pages are authentication/password/invitation, admin overview/tenants/members,
  and portal overview/team. Both navigation and direct routing block deferred pages;
  their route modules are non-importing 404 stubs. The only API rewrite is same-origin
  `/console/v1/*` to explicit server-only `CSA_CONSOLE_API_ORIGIN`; the fixed :8100
  upstream is removed. Supplied FourStacks tokens, light theme, focus/control boundaries
  and brand mark are adopted; font files and browser visual verification remain separate.
- Added `0270_console_identity.sql`: users, hashed opaque sessions, memberships,
  invitation/reset tokens, persistent throttle buckets, immutable audit, and narrow
  `csa_identity` permissions. Identity readiness rejects elevated/misgranted roles and
  exposed domain/definer access; extension-owned read-only metadata is distinguished
  from CSA data. RLS is staged without FORCE. Readiness expectations now require **0270**.
- Implemented login/me/logout, idle/absolute expiry, revocation/rotation, forced password
  change, salted scrypt offloaded with bounded concurrency, session-bound CSRF and exact
  Origin validation. Disabled users and stale memberships cannot authenticate. Identity
  pool startup, shutdown/error cleanup and readiness are explicit; human routes do not
  require `CONSOLE_API_TOKEN`. Production runtime docs/OpenAPI are disabled.
- Added deliberate first-admin bootstrap, tenant/member onboarding, invite inspect/accept/
  inventory/revoke/renew, admin-issued resets and session revocation, last-admin guards,
  and audited user/member administration. Invitation/reset links are manually delivered;
  the public reset route is generic and issues no retrievable credential. Browser tokens
  use fragments cleared before POST-body submission. No existing accounts are imported.
- Tenant creation atomically provisions nine disabled capabilities with current Pydantic
  schemas plus audit. Capability mutations enforce platform subscription vs tenant-admin
  switches and atomically audit before/after; actor fields are rejected. Domain access
  derives tenancy from the verified membership; platform users explicitly select an
  existing tenant. Machine tokens are separate read-only principals with a tenant allowlist.
- Published `docs/console_openapi.json`, typed frontend identity adapters and a snapshot
  drift test. No Python dependency changed, so `uv.lock` is unchanged. Frontend dependencies
  use their existing resolved versions, now exact pins; no unreviewed version upgrade.
- Removed unsafe invoice-signing fallbacks by making the unsupported download-link route
  explicitly unavailable (501) until M6. Existing domain read APIs remain session-scoped;
  M2+ business UI and provider execution remain deferred.

Implementation verification recorded isolated frontend transport checks **5 passed**,
ESLint, typegen, TypeScript and production build, plus actual `make db-reset` and migration
reapply inside a newly created disposable Postgres container. No schema change was needed
during this review, so no additional reset was performed.

### Review outcome and remaining acceptance

- Documentation/source cleanup: removed superseded console handoffs, duplicate PM
  Markdown, legacy setup/chat briefs, old console launch Makefiles/compose file,
  backup archives, OS/Python caches and standalone debug scripts. Current plans,
  runbooks and all `ui-guide/` contents remain. The canonical deferred PM pack and
  historical source-state evidence remain explicitly reference-only. Nested Git,
  local env files, frontend dependencies and backend/PM reference code are retained;
  this is not a completed removal of the legacy application or gitlink conversion.
- Fixed password changes raising `TypeError` for non-ASCII passwords: constant-time equality
  now compares UTF-8 bytes. Regression exercises change to Unicode, rejection of reusing it,
  and changing away from it.
- Fixed admin resets of unaccepted invitation accounts: previously reset acceptance could
  create credentials without creating the invited tenant membership. Such resets now return
  409 before any mutation; the invitation remains valid and normal acceptance creates its
  membership. Accepted-account resets retain their existing behavior.
- Review checks: `ENV=ci NO_LOCAL_ENV=1 make lint` passed. Session integration selection
  **33 passed**; follow-up fixes/config/lifecycle/contract/password selection **30 passed**.
  These selections overlap and must not be summed. All database fixtures were disposable
  and synthetic; the second selection includes the pending-invitation regression added
  after the session selection started.
  The existing accepted-account invite/reset single-use/session-rotation test also
  passed separately after the reset guard change.
- The already-running full `make lint test` completed with **564 passed, 1 failed**
  before the two review fixes: TCP ingress p95 **1021.433 ms** exceeded the unchanged
  **200 ms** limit. This is not a clean full-suite pass. No unrelated latency workaround
  or threshold relaxation was added.
  The exact standalone rerun also failed at **451.844 ms** p95 (150 completed turns,
  150 pending deliveries, 2.084 turns/s). The cause remains undiagnosed; full-suite
  contention alone does not explain away the outstanding acceptance failure.
- Frontend transport/routing tests were rerun during review: **5 passed**. No frontend
  source was changed; the earlier isolated typecheck/build evidence remains separately
  recorded above.
- M0 and M1 code are implemented, not deployed/live-browser accepted. Tenant membership
  switching/account attachment remain explicitly deferred; invitation/reset delivery is
  manual. Capability RBAC/audit/safety guards and initial platform subscriptions are M2
  groundwork already present, not evidence that all of M2 is complete.
- Next bounded implementation: finish M2's truthful entitlement/tenant-switch/role/provider
  readiness contracts, runtime queries/pagination and tenant switch/specialised UI. Verify
  registry-cache freshness across gateway and worker processes rather than assuming that a
  successful toggle is immediately reflected in execution. M3 can proceed independently
  after M1: authenticated three-file HTTP uploads and safe download/delete/job lifecycle.
- Browser visual/session acceptance, deployed console-to-gateway routing, provider operations,
  M3–M8 and runtime cutover remain unverified/pending. No additional keys are needed for
  provider-neutral M2/M3 implementation.

Safety correction: an initial invocation of the pre-existing test image fixture used
the repository as local Docker build context, potentially sending ignored environment
and copied-reference files to the local daemon. No secret values were inspected/displayed,
and the Dockerfile copies no application files. The fixture now builds from **deploy/**
only. All subsequent tests/builds use the restricted context. No persistent dev/production
database was reset/migrated; no production/provider verification, existing-process change,
host/proxy/webhook change, account migration or deployment is claimed.

## 1. Decisions and scope

The user supplied `CSA_WebConsole/` as a copied GitHub checkout and explicitly permits
replacement of its logic to fit CSAV2. During discovery the user selected:

1. **Retain the Next.js UI, use one CSAV2 backend.** Replace the copied Python API;
   do not add another permanent backend or a forwarding layer around its SQL.
2. **Core CSA console first.** Keep project management, notes, diagrams, AI planner,
   terminal, Hermes investigations and multi-agent workrooms disabled for a separate phase.

This supersedes LG15's earlier recommendation for an independent console backend forwarding
actor headers. Browser users will authenticate directly to CSAV2 through same-origin session
endpoints. An authenticated dependency, not a request field/header, derives identity and roles.

No requirement to preserve old URL shapes, n8n envelopes, SQLAlchemy models, workflow IDs or
copied test expectations. Preserve useful UI/UX, business intent and explicit user requirements:
platform-controlled agent subscriptions, tenant-owned switches, specialised agent tabs, up to
three tenant-scoped KB files, approvals, conversations and observability.

The copied source is sufficient to begin implementation. No further console code is blocking.
Google/payment/MyInvois execution remains separately gated; prepared requests are not actions.

## 2. Source findings and evidence

| Surface | Observed implementation | Migration consequence |
|---|---|---|
| Frontend | `CSA_WebConsole/csa-console/web/`: Next 16.3.8, React 19.2.8, React Query, Tailwind; lockfile present, dependencies not installed here | Keep presentation and components selectively; read the installed Next version's docs before edits. |
| API | `.../api/app/main.py`: separate FastAPI app, synchronous SQLAlchemy engines, many imported routers | Do not mount/import this application in CSAV2. Rebuild selected endpoints under `csa/console/`. |
| Data connections | `.../api/app/db.py`: app DB, read-only domain DB, optional domain-write DB, optional PM checkpoint DB | Remove these legacy connection paths from the active application. All CSA domain access uses psycopg async + `tx`. |
| Identity | `.../core/deps.py`, `sessions.py`, `security.py`: Argon2, opaque session tokens stored as hashes, idle/absolute expiry, forced password change | Retain these design concepts, rewrite persistence/authorization with async APIs and typed contracts. |
| Roles | Copied migrations allow `platform_admin`, `client_owner`, `client_member`; UI checks old role strings | Standardize platform/tenant roles and server-side membership authorization; update all frontend checks. |
| Routing | Next rewrites `/api/*` to fixed `127.0.0.1:8100`; server layouts default there too | Replace with one server-only CSAV2 upstream; browser routes resolve same-origin. |
| Agent visibility | `modules/agents/n8n.py` reads n8n workflows/executions and builds n8n browser links | Replace with CSA registry, agent runs, events, queues and trace links. |
| Agent switches | `modules/client/router.py` updates `capability_registry` through its own write connection; audit is separate | One CSAV2 mutation service, role checks, atomic mutation+audit, registry-cache invalidation. |
| Chat | `modules/chat/router.py`: schema `2.0`, `web_console`, user UUID used as contact UUID; text/poll sent to n8n | Build real `Channel.WEB`, Envelope 3.0 ingress and explicit user-to-contact mapping; do not forward the old payload. |
| Delivery | CSAV2 delivery runner currently rejects non-Telegram delivery channels | Web delivery/outbox support is a prerequisite for working portal chat, not just a frontend change. |
| Chat files | Chat sends PDF/DOCX base64 to n8n; up to 40 MB total | Remove this file-ingestion shortcut; KB uses the existing three-file/10 MB pipeline. Do not turn every chat attachment into permanent KB content. |
| FAQ | `modules/client/router.py`: local `data/faq`, n8n upload/delete, `app.faq_files` metadata | Replace with KB document IDs, storage metadata and ingest jobs. `app.faq_files` is referenced but no CREATE TABLE was found in the supplied SQL migration set. |
| Voice | Copied app starts a second Uvicorn listener and exposes signed audio for an n8n flow | Do not reproduce this at gateway startup; hide voice until an isolated CSAV2 voice pipeline exists. |
| Projects | Simple client/channel-grouping projects coexist with the much larger PM subsystem | Defer both project UIs initially. Tenant is the authorization boundary; do not infer tenancy from a project binding or thread-prefix match. |
| Schema | Copied `api/sql/0001`–`0012` cover auth, projects and PM | Do not run these migrations against CSAV2. Design additive CSAV2-owned migrations instead. |
| Checkout | `CSA_WebConsole/.git`, API `.env`, `.backups/*.tgz`, scratch files and duplicated planning docs exist | Treat as an uninitialized reference checkout, not a deployment artifact. Do not open its env or extract archives; no bulk git-add or automatic execution. |

Discovery was read-only. No copied install/setup script, application, container, external
endpoint or production database was executed/contacted. This is architecture discovery, not
an exhaustive security audit or a claim that the copied source builds successfully.

## 3. Target architecture

```text
Browser
  │ same-origin HTTPS; HttpOnly session cookie; CSRF protection
  ▼
Next.js presentation (existing admin + portal visual shell)
  │ rewrite/proxy to one explicitly configured internal CSAV2 origin
  ▼
CSAV2 gateway
  ├─ /console/v1/auth/*             session lifecycle
  ├─ /console/v1/admin/*            platform administration
  ├─ /console/v1/*                  tenant-aware console services
  └─ provider callbacks            OAuth / verified channel events
       │
       ├─ narrowly scoped identity storage
       ├─ tx(app_pool, client_id) → domain services → SQL in db/sql/
       ├─ ingress_events → existing worker → csa/graph/
       ├─ approval services → approval_audit → outbound_deliveries
       └─ KB ingest jobs → existing KB worker → documents/chunks
```

- Keep worker/delivery responsibilities separate from the gateway.
- Only `csa/graph/` imports LangGraph. Do not import the copied PM graph or its worker.
- No browser-visible DB DSN, provider token, `CONSOLE_API_TOKEN`, actor-role header or
  arbitrary destination URL.
- Customer messages, staff inbox replies and staff/admin operations are different actions.
  A portal login is not proof of customer identity for every contact in the tenant.
- Keep legacy service-token access only as an explicitly identified machine principal with
  bounded scopes. It must not bypass new browser RBAC or accept arbitrary actor claims.
- Preserve external channel routes. Same-origin web routing must not consume
  `/hooks/*`, OAuth callbacks or marketplace webhooks.

### Code layout

- Keep the existing frontend path for the first implementation slice; moving it is not a
  prerequisite. If adopted into a monorepo later, move reviewed UI source only, not `.git`,
  old envs, archives or the old Python backend.
- Add cohesive console modules for auth/actors, clients, users, capabilities, inbox,
  approvals, KB, channels and ops under `csa/console/`; split the current router as needed.
- Add SQL under `db/sql/console/` and domain-specific SQL folders; no inline SQLAlchemy.
- Use the next free migration number after `0260` at implementation time. Keep migration
  and readiness expectations synchronized. Do not prescribe unverified schema numbers.
- Keep exact Python pins and update `uv.lock` only when adding necessary dependencies.
  Use the existing npm lockfile and package scripts; no unreviewed dependency upgrades.

## 4. Identity, tenancy and permissions

### Identity model

Use global users, hashed opaque sessions and hashed single-use invitations/reset tokens,
plus explicit tenant memberships. Keep global login/session lookups in a narrowly scoped
identity role/pool rather than running the entire gateway as owner or granting unscoped
domain access. All membership/domain records carrying `client_id` need staged RLS and
tenant-bound queries. Enforce user/tenant/contact references in code and DB constraints.

An initial user may have one active tenant membership; an explicit switch may select among
memberships later. Do not trust `X-Client-Id` alone. Platform admins select a tenant via a
validated route/query, and each scoped operation enters that tenant's transaction.
Record actor identity and target tenant for every administrative action.

Do not automatically import copied accounts, sessions or passwords. Start with synthetic
users and a deliberate platform-admin bootstrap CLI. If existing accounts actually need
transfer, use a reviewed export/import and forced password reset; sessions never transfer.

### Role mapping

| Old role | Target |
|---|---|
| `platform_admin` | `platform_admin` |
| `client_owner` | `tenant_admin` |
| `client_member` | `tenant_staff` by default; a reviewed import may choose `read_only` |

| Action | Platform admin | Tenant admin | Tenant staff | Read-only |
|---|---|---|---|---|
| List tenants / manage subscribed agents (`is_enabled`) | Yes, audited | No | No | No |
| Tenant-scoped reads / timeline / invoice status | Explicit selected tenant | Own tenant | Own tenant | Own tenant |
| Tenant self-service agent switch (`client_enabled`) | Selected tenant | Own tenant | No | No |
| Invite/manage tenant members | Selected tenant | Own tenant, no role escalation | No | No |
| KB upload/delete; staff draft preparation | Selected tenant | Own tenant | Own tenant | No |
| Inbox takeover/release/reply; approve/reject/edit | Selected tenant, explicit staff privilege | Own tenant | Own tenant | No |
| Runtime cutover / DLQ redrive / credential administration | Explicit scoped admin operation | Limited approved connection setup | No | No |

Required controls: HttpOnly cookies, Secure outside loopback dev, SameSite policy, bounded
idle/absolute expiry, session rotation/revocation, forced-password-change enforcement,
login throttling, Origin/CSRF validation, no-store auth responses, single-use invitations,
generic login errors, and no token-bearing URLs in logs. Trust forwarding headers only
from configured proxies. Password operations must not block the event loop.

## 5. Source-to-target feature mapping

The target paths below are proposals unless marked **exists**. They are not shipped
endpoints or promises that copied UI payloads already work.

| Copied route / screen | New contract | Data/services | Disposition |
|---|---|---|---|
| `/api/auth/login`, `/logout`, `/me`, `/password/change` | `/console/v1/auth/...` | New users, sessions, memberships, attempts/audit | Rebuild; retain login/password UI. |
| `/api/invites/{token}`, `/{token}/accept` | `/console/v1/invites/...` | New hashed invites/reset lifecycle | Rebuild; no token logging. |
| `/api/admin/invites`, `/users`, user enable/disable/revoke/reset, `/audit` | `/console/v1/admin/users`, `/invites`, `/audit` + explicit commands | Identity service + audited admin transactions | Rebuild. |
| `/api/admin/clients`, tenant users/invites | `/console/v1/admin/clients` and `/{client_id}/...` | `clients`, memberships, credentials metadata | Add explicit tenant selection and onboarding. |
| `/api/client/profile` | `/console/v1/profile` | Verified membership + `clients` | Rebuild without fallback business names hiding missing tenants. |
| Admin `/agents/overview`, `/{name}`, `/{name}/runs`, `/agent-runs/{id}` | `/console/v1/capabilities` (**exists**), `/runs`, `/runs/{id}` | `capability_registry`, `agent_runs`, `agent_events`, trace summaries | Replace n8n liveness/workflow identifiers. |
| `/api/client/agents`, `PUT /agents/{name}/enabled` | Existing capabilities read/patch, extended with verified RBAC and richer status | `is_enabled`, `client_enabled`, circuit state, config schema | Platform subscription and client switch stay distinct. |
| `/api/client/activity`, admin dashboard/system executions | Existing `/traces`, `/events`; new `/overview`, `/runs`, `/ops/health` | CSA events, runs, ingress/outbox/DLQ metrics | No n8n workflow/execution API calls. |
| `/api/client/team`, invites, member disable | `/console/v1/team` and explicit member/invite commands | Memberships/invitations and sessions | Bound role grants; prevent last-admin removal. |
| `/api/client/faq` list/upload/view/delete | Existing `/kb/documents`, `/kb/ingest-jobs`, `/kb/gaps`; new `/kb/uploads`, `/kb/files/{id}` | KB storage metadata + `kb_ingest_jobs` | Replace filenames/base64/n8n with stable IDs and streamed bounded multipart. |
| `/api/chat/send`, `/poll`, history/detail/delete | New `/chat/conversations`, `/{id}/messages`, `/chat/turns/{trace_id}` | Web-channel identity, `ingress_events`, conversations/messages/outbox | Async 202 acceptance, typed durable status, pagination and archive command. |
| No copied staff-inbox/approval equivalent | `/conversations`, `/conversations/{id}/...`, `/approvals`, `/{id}/decide` | Existing LG07/LG05 services and audit | Build UI and API; not just a route rename. |
| No specialised tab implementation matching CSA catalogue | Existing `/tabs/specialized` plus feature-specific reads/actions | Backend readiness + entitlement + role | KB/invoices first; preparation-only actions labelled accurately. |
| `/api/voice` | Future bounded audio ingress into CSAV2 voice pipeline | Voice jobs, private media storage, worker/transcription | Hidden/disabled initially; no second Uvicorn listener. |
| Admin/client projects + channel bindings | Separate future project/domain decision | Do not infer tenant ownership via text prefix | Hide projects navigation and direct routes during core migration. |
| PM notes/planner/diagrams/terminal/Hermes/workroom/shared-projects | Separate phase | No imported PM DB, runner, SSH or checkpoint worker | Not mounted, not started, no frontend actions. |

### Agent and specialised-tab truthfulness

Return independent fields for entitlement, tenant enablement, role access, implementation
stage and connection readiness. Missing providers must not look like functioning agents.
Circuit state is not the same as subscription status.

- KB: ready only with compatible embedding model and ingest configuration.
- Invoices: lookup is available; downloads remain gated by a real served artifact route.
- Booking/subscriptions: local reads and **prepared requests only**, not provider execution.
- Email/research/invoice writes: staff-only preparation libraries; no customer planner access.
- LHDN: show unavailable/setup-required until a tested MyInvois adapter exists.
- E-commerce: distinguish TikTok implemented components from live-account verification and
  Shopee/Lazada drafts. A catalogue entry is not proof that a tab has a working backend.
- Teach-a-task remains LG14, not a reused PM terminal/workroom.

## 6. Implementation work packages

### M0 — Adopt the checkout safely and establish the contract baseline

Dependencies: none.
Status: implemented; reviewed adoption/build boundary and static contracts are recorded above.

1. Keep the copied checkout as reference. Review its own scripts and lockfiles before running
   any commands; do not source copied envs or use its DSNs.
2. Inventory active UI route dependencies (core list above) and record deliberate removals.
3. Define a frontend build boundary excluding old API, PM runtime, archives and nested Git.
   Do not delete the user's copy or run a broad `git add`/commit of the checkout.
4. Document isolated web dev port, internal CSAV2 origin and synthetic seed identities.
   Do not start old `make`/compose targets or change existing processes.
5. Publish the proposed OpenAPI contract and typed client generation strategy; distinguish
   current endpoints from proposed contracts and mock only explicitly labelled fixtures.

Exit: reviewed file adoption list, build/test commands and contract inventory; no
production/n8n connection or package setup side effect.

### M1 — Session authentication, RBAC and tenant onboarding

Dependencies: M0.
Status: implemented with synthetic integration evidence; live browser acceptance remains open.

1. Add reviewed identity/membership/audit migrations and narrowly scoped permissions.
2. Implement auth/me/password/invite/session lifecycle and bootstrap CLI.
   Add identity-pool startup/shutdown/readiness explicitly; session routes must not depend
   on `CONSOLE_API_TOKEN` being set, while machine-token routes remain separately gated.
3. Resolve a typed actor from the session, selected tenant and membership; reject spoofed
   client/actor/role headers. Separate machine-principal authentication.
4. Add tenant list/onboarding, member lifecycle, agent entitlement controls and transactional
   audit. Provision registry entries and sync Pydantic schemas on tenant creation.
5. Restrict machine-only docs/OpenAPI or publish a safe static spec; cookie-auth mutations
   require CSRF protection even if a bearer-token path existed previously.
6. Wire login, invite, forced-password change, admin/portal layouts, navigation and logout
   to CSAV2. Replace hard-coded `:8100` and old role strings everywhere.

Exit: synthetic admin/tenant-admin/staff/read-only users can log in, see only authorized
screens/data, and cannot elevate roles or select arbitrary tenants. No permanent BFF.

### M2 — Agent management, specialised navigation and runtime visibility

Dependencies: M1.
Status: partial groundwork only. Mutation authorization/audit/safety guards and initial platform
entitlements UI exist; finish truthful readiness, tenant UI, runtime visibility and cache evidence.

1. Harden current capabilities mutations; remove client-supplied `changed_by` as authority.
   Atomic before/after audit and cache invalidation; protect required system capabilities.
2. Add scoped run/overview queries and stable pagination. Replace workflow activity with
   registry/execution/readiness status; never fabricate a zero when a query failed.
3. Build platform tenant selection + subscription matrix and tenant self-service controls.
4. Drive specialised tabs from backend entitlement/readiness/role metadata, with independent
   setup-required/prepared-only/not-implemented states.
5. Reuse traces/events polling first; add SSE later with replay cursor/reconnect semantics.

Exit: admin subscription and tenant switch enforce the same policy through API and UI;
disabled agents cannot execute through chat or alternate paths.

### M3 — KB upload and document lifecycle

Dependencies: M1. Can run alongside M2.

1. Implement authenticated multipart upload: at most 3 PDF/DOCX/CSV files, 10 MB/file;
   enforce streaming/aggregate request bounds before large allocations.
2. Resolve storage names on the server under `KB_UPLOAD_ROOT/<client_id>/`; do not accept
   client-selected absolute paths, filenames as authorization, or raw base64 envelopes.
3. Reuse extraction/size/signature validators, enqueue once with an idempotency key, and
   return 202 + job ID. Coordinate atomic rename/transaction commit and explicit orphan
   cleanup for filesystem/DB failures; no processing before durable file publication.
4. Add typed storage metadata, authorized original-file download and document delete.
   Prevent in-flight ingestion from resurrecting a deleted document/file: cancellation,
   locking or tombstone semantics must be designed and tested before delete is exposed.
5. Replace FAQ screen with job progress, per-file errors, duplicates, document versions,
   gaps and source metadata. Keep raw storage paths out of browser contracts.
6. No imported `app.faq_files` state or legacy files by default. Any later transfer requires
   an approved inventory and embedding compatibility/re-index plan.

Exit: 3-file UI → queue → worker → searchable tenant KB; duplicate/retry/partial/delete
scenarios pass; 4 files, invalid paths/types, empty and oversized files fail explicitly.

### M4 — Web chat ingress and web delivery

Dependencies: M1; text chat does not depend on M3.

1. Define authenticated actor-to-contact bindings. Portal test chat must use a dedicated
   tenant-scoped contact; never assume the session user UUID exists in `contacts`.
2. Decide the portal chat label explicitly: a tenant sandbox assistant, not a customer
   inbox. Use current policies and a safe default mode; it must not inherit unrestricted
   financial/booking/email permissions from a staff login.
3. Build server-generated Envelope 3.0 with `Channel.WEB`, verified tenant/contact/conversation,
   redaction and bounded text. Client supplies a request idempotency key, not authority.
4. Factor/reuse durable ingress primitives without weakening Telegram webhook verification.
   Persist/deduplicate atomically and return 202; no 100-second n8n HTTP forwarding.
5. Implement worker/delivery channel dispatch for web replies: outbox messages become
   available to the verified conversation participant exactly once. Preserve retries,
   leases, audit and terminal status; do not report a merely queued message as delivered.
6. Rework polling/history UI for queued/processing/held/available/failed with stable cursors,
   typed errors, deadline vs retry distinctions and reconnect recovery. Add web confirmation
   controls using existing pending-action codecs/context binding.
7. Replace destructive-looking history deletion with audited archive/hide unless an explicit
   retention-approved purge is implemented. Do not invent `status='deleted'` semantics.
8. Keep voice and arbitrary attachments unavailable until their own end-to-end support.
   KB uploads remain a distinct action, not an automatic side effect of chat.

Exit: text → durable ingress → worker/graph → held approval or web outbox → visible reply;
duplicates, wrong contact, crash/retry and confirmation single-use tests pass. Existing
Telegram delivery remains unchanged.

### M5 — Staff conversation inbox and approvals

Dependencies: M1; web-channel approval round trip depends on M4.

1. Add scoped conversation/message reads with pagination, channel filters and safe source
   links. Restrict message content by membership/role; do not expose credential payloads.
2. Add audited takeover/release/human reply through shared domain services and outbox.
3. Extract reusable approval transition results from LG07 services where necessary.
   Current functions may return Telegram-oriented text and `edit_reply` requires a bound
   staff action; provide a structured authenticated-console adapter, not a fake Telegram
   identity/action or a second SQL approval implementation.
4. Distinguish order approvals from outbound-reply approvals; route each to its existing
   domain handler. Enforce tenant scope, expiration, single-use, bounded edited text,
   concurrency locks, customer delivery idempotency and `approval_audit`.
5. Build queue/detail/edit UI with stale-state refresh and explicit conflict/already-handled
   results. Approval API returns structured state, not success inferred from a text message.

Exit: authorized approve/reject/edit works across Telegram/web channels; wrong tenant,
read-only user, expired/used action and duplicate submissions cannot deliver twice.

### M6 — Business tabs, integrations and staff preparation

Dependencies: M1 and M2; financial delivery depends on M5 and a real artifact/provider.

1. Expose read-only order/ticket/invoice views and preparation request list/detail with
   actor/trace/status; future approval/execution is not implied by `prepared`.
2. Fix invoice signing fallbacks and implement real authorized PDF storage/serving before
   exposing download buttons. Missing signing configuration is an explicit error.
3. Add role-gated connection metadata/start/revoke. Preserve tenant-bound OAuth state and
   encrypted credentials; Google OAuth/watch/Calendar execution is a separate integration
   slice, not completed by env values or UI forms.
4. Adapt `StaffActor` from verified sessions for email/research/invoice draft preparation.
   Never accept trusted actor fields from browser JSON.
5. LHDN and payment execution stay disabled until sandbox verification. No production
   payment/API keys are required merely to migrate these screens.

Exit: every visible business action has a real backend or an explicit unavailable state;
no stub/fake-provider success in operational UI.

### M7 — Ops, regression checks and release candidate

Dependencies: M2–M6.

1. Add scoped queue/worker/outbox/DLQ metrics, controlled admin redrive/breaker operations,
   audit views and notification target management.
2. Remove active n8n env dependencies, workflow links, old API connections and second voice
   listener. Deferred PM routes must return unavailable and must not initialize services.
3. Run backend lint/tests; frontend lint/typecheck/build; generated-contract drift checks;
   browser tests of core workflows and role boundaries.
4. Verify backup/restore and additive migrations on disposable databases, readiness version,
   clean startup/shutdown, storage permissions and no secret exposure in UI/build output.
5. Record performance targets, test environment and failures honestly. Existing baseline:
   499 passed/1 TCP latency failure in the last full run; exact benchmark passed alone.
   Do not relax the 200 ms acceptance target to make migration appear green.

Exit: an isolated dev release candidate with synthetic tenants and complete evidence.

### M8 — Operator-approved deployment and independent runtime cutover

Dependencies: M7 and explicit deployment approval.

- Do not alter Caddy/Cloudflare, domains, ports, webhooks, production containers, credentials
  or running processes during implementation. Prepare deployment instructions first.
- Console release rollback and channel runtime rollback are separate controls.
- Console rollback restores a compatible release/session configuration; do not blindly
  reactivate the old n8n API after new KB/auth writes without a tested compatibility plan.
- Channel rollout remains LG09: per-tenant shadow → suggest → bounded auto with metrics,
  rollback drill and backup evidence. Disabling a console UI does not stop worker execution.
- Keep n8n for clients still requiring unported runtime capabilities; console migration
  completion does not establish booking/voice/refund/warranty parity.
- Retire workflows only after explicit per-client acceptance and the agreed observation
  window. FORCE RLS and owner-role retirement remain a later coordinated change.
- Address the separately documented Langfuse ingestion migration before its recorded
  2026-11-16 deadline; it is not a prerequisite for authoring the console migration.

## 7. Validation matrix

| Layer | Required evidence |
|---|---|
| Auth | Correct/incorrect/inactive login, generic errors, lockout/throttle, password change, expiry/revocation, single-use invite/reset, no credentials in responses/logs |
| Authorization | Every mutating/read endpoint exercised for four roles; tenant A cannot read/mutate tenant B; forged actor/tenant headers and stale memberships rejected |
| Persistence | Disposable migration apply/reapply and `make db-reset`; RLS policies and minimal grants; atomic audit; new-tenant capability/schema provisioning |
| Agent controls | Platform switch vs tenant switch vs circuit vs readiness tested independently; actual execution obeys entitlements |
| KB | 3-file success, 4-file rejection, size/type/OCR errors, traversal/symlink controls, retry/duplicate/versioning, delete-vs-ingest race, storage/transaction failures |
| Chat | Envelope 3.0, correct web-contact binding, idempotent 202 acceptance, worker/outbox restart recovery, duplicate-safe delivery, held/failed state and reconnect |
| HITL | Approve/reject/edit, order vs reply approval, expired/used action, invalid text, concurrent decisions, one delivery and one outcome audit |
| Frontend | `npm ci` after script review, package lint/build, `tsc --noEmit`, generated contract check; browser tests for login, toggles, KB, inbox and approval |
| Disabled surfaces | No accessible PM terminal/Hermes/workroom/voice routes or workers; no misleading LHDN/payment/Calendar “completed” controls |
| Isolation | No network calls to production/n8n during dev tests; no provider keys required for synthetic transport fixtures |

The original discovery was documentation-only. M0–M1 implementation and its synthetic
verification are recorded at the top of this document; later-package checks remain pending.

## 8. Configuration and remaining decisions

### Implemented M0–M1 configuration

Set `IDENTITY_DATABASE_URL` with the new `csa_identity` login, targeting the same literal
host/port/database as `DATABASE_URL`; `CONSOLE_ALLOWED_ORIGIN` to the exact web origin; and
server-only frontend `CSA_CONSOLE_API_ORIGIN` to the isolated gateway origin. Provision the
identity role deliberately with `CSA_IDENTITY_PASSWORD` (at least 12 characters) passed
only to the migration/provisioning command. Bootstrap uses its explicit environment DSN
and an interactive password prompt. No local env file was edited; required names and
safe examples are in `.env.example` and `docs/CONSOLE_API.md`.

Defaults: `CONSOLE_COOKIE_SECURE=true`, `CONSOLE_SESSION_IDLE_SECONDS=1800`,
`CONSOLE_SESSION_ABSOLUTE_SECONDS=43200`. Insecure HTTP/cookies are accepted only on
loopback dev/CI. Optional machine reads require both `CONSOLE_API_TOKEN` and
`CONSOLE_MACHINE_CLIENT_IDS` (JSON UUID list). Human sessions never require this token.
Use `ENV=ci NO_LOCAL_ENV=1 make ...` for checks without dotenv loading. No persistent
application listener was configured/started; test-only ephemeral listeners are disposable.
Copied `APP_DATABASE_URL`/`DOMAIN_DATABASE_URL` are unused.

Keep existing `KB_UPLOAD_ROOT`, `TEI_ENDPOINT`, `KB_INGEST_ENABLED`,
`INVOICE_SIGNING_SECRET` and encrypted-credential requirements. Google OAuth application
values are already prepared but adapters remain unimplemented. Never copy the console's
old `.env` into CSAV2 or assume its hostnames/keys are valid.

### Decisions to confirm when the relevant package starts

1. Final console hostname and dev web port (implementation can start on loopback without
   changing public infrastructure).
2. Whether real console users/files need migration; default is a fresh synthetic dev system.
3. Initial portal chat is a sandbox assistant; any additional staff/customer chat product
   must have an explicit permission/contact model.
4. Invoice PDF origin: existing storage versus deterministic generation.
5. Staff KB gap answers: immediate publication versus approval.
6. Optional project/PM scope and real provider/accounting/payment execution remain separate.

## 9. Definition of done and next-session prompt

- [ ] M0–M7 evidence complete; frontend uses CSAV2 exclusively for active CSA features.
- [ ] No active legacy API/n8n/direct-domain-SQL path remains in adopted console code.
- [ ] Platform subscriptions, tenant switches and specialised-tab readiness are truthful.
- [ ] Authenticated KB/chat/approval flows work end to end with synthetic fixtures.
- [ ] Provider-dependent and deferred features stay explicitly unavailable.
- [ ] All deployment actions remain separately approved; runtime cutover is not implied.

```text
Continue from docs/handoffs/2026-10-15-console-source-migration-plan.md.
Read AGENTS.md, LG15, LG13, LG07 and the source findings before editing.

The user approved retaining the Next.js UI and replacing the copied Python API with
one CSAV2 backend. Core CSA console only; PM/terminal/Hermes/workroom/projects remain
disabled. The copied checkout is at CSA_WebConsole/csa-console/.

M0 and M1 are implemented and reviewed locally. Read the review evidence and preserve
the Unicode-password and pending-invitation-reset regression fixes. Do not redo auth.
Implement remaining M2 as the next bounded slice: truthful entitlement/tenant-switch/
role/provider readiness contracts, scoped runtime queries/pagination, tenant controls
and specialised navigation. Reuse the existing capability mutation RBAC/audit/safety
guards and platform entitlement UI. Prove execution/cache freshness after toggles.
Do not mount/import the copied API, source its env, run its setup scripts or connect
through its database URLs. M3 KB uploads are a separate bounded package, not implicit
scope for this session.

Keep SQL in db/sql/, psycopg async, exact dependencies, additive staged-RLS migrations
and current readiness expectations. Only csa/graph/ imports LangGraph. All actors
come from verified sessions; no trusted actor/role fields from browser input.

Do not read secrets or touch production/n8n/Caddy/Cloudflare/webhooks/existing processes.
Use synthetic fixtures and disposable databases. Inspect Next's installed version docs
before frontend edits. Run focused auth/role tests, make lint test, frontend validation
and disposable migration/reset checks if a schema change requires them. The previous
full suite had 564 passes and one TCP latency failure; do not call it green. Update
this plan and PROGRESS with actual evidence, keeping live browser/deployment separate.
Ask one focused question only when a decision blocks the implementation.
```
