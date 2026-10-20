# CSA WebConsole → CSAV2: source-based migration plan

Date: 2026-10-15; guarded M5 dev rollout 2026-10-16. Status: **M0–M3 implemented with isolated-dev acceptance; M4 functionally closed with accepted laptop-dev performance exception. M5 Telegram slice reviewed/validated: 692 full tests pass; approved forward-only dev0300 and coordinated replacement complete, platform-admin UI/synthetic Telegram acceptance, live reader denial and representative tenant-admin/staff replay verified. M3 tenant-role and broader M5–M8 acceptance pending. Historical latency failures retained despite latest ASGI/TCP p95 108.284/174.623 ms passing unchanged <200 ms. No public deployment or production cutover.**

Start the next session from the [domain/release handoff and prompt](./2026-10-19-domain-release-next-session.md).
The opening status above is dated M5 evidence. Current source expects 0360; M6A local
reads, M6B synthetic authorization and M6D prototype/test-local persistence remain
separate from rollout. Repository-only CI/release packaging is implemented; public
hosting and promotion remain disabled/unverified. See the
[release contract](../../deploy/RELEASE.md). Packaging does not complete M6 or
authorize production changes. The existing apex/`www` landing page must be preserved.
The [M4 closeout](./2026-10-16-m4-closeout-m5-start.md) retains prior acceptance decisions.
The implementation/review sections below retain historical evidence, not old next-step instructions.

## M4 read-only sandbox implementation (2026-10-16)

- The user explicitly selected **Sandbox assistant**, KB/product-information reads only.
  This intentionally narrows the original M4 confirmation requirement: callbacks and
  confirmation actions are rejected, not wired to business mutations. M5 staff inbox/
  approval execution remains separate.
- Migration 0290 adds tenant-RLS participant/thread/turn records. A dedicated contact per
  account/tenant is independent of session user IDs and existing customers. Internal
  endpoint metadata is disabled and token-free; no existing Telegram route is modified.
- Human session/CSRF/role/tenant and exact owner checks protect all chat surfaces.
  Atomic bounded/redacted Envelope 3.0 ingress, request fingerprint/replay conflict,
  stable-ID pagination and audited archive reuse existing queue/audit primitives.
- The shared runner permits only KB query and order-agent product search/detail on web;
  customer reads, business mutations, staff escalation and pending-action consumption
  are denied. Tenant capability switches and grounding remain authoritative.
- Web delivery validates the participant/message/trace binding and atomically commits
  local availability plus delivery attempt, with no external call or Telegram decryption.
  Held drafts remain private and held without staff notification or automatic release.
  Fail-safe text does not promise staff follow-up and the turn remains failed.
- `/chat` and `/portal/chat` have private history, 2.5-second polling, stable older-page
  cursors, explicit same-key retry, typed rejection vs uncertain timeout, deadline warnings,
  reconnect without resend and confirmed archive. Voice/files/confirmations are unavailable.
- Focused runtime/runner/Telegram/contract/migration selection: **60 passed**, including
  synthetic KB-to-web retrieval, post-outbox-commit worker crash, local-delivery transaction
  rollback, tenant/user/contact denial, archive and disposable-only `make db-reset` at 0290.
  Final sandbox rerun **10 passed**, including redaction expansion, bigint cursor bounds,
  stronger trace bindings and system-refusal-only grounding exemption. Isolated frontend
  **11 transport/routing/cancellation + 2 state/deadline tests**, lint/types/production
  build and safe traces pass. Final full validation is recorded in
  [progress](../PROGRESS.md); do not add overlapping test selections.
- Final full validation after fixing gateway readiness's expected schema version to
  0290: **674 passed / 1 failed**. TCP ack p95 **202.643 ms** exceeds the unchanged
  `<200 ms` bound; ASGI ack p95 **79.734 ms** passes, with 150 completed turns/deliveries
  per transport. The exact isolated TCP repeat had passed at **110.980 ms**, but does
  not supersede the full-run failure. Full acceptance is not green; no cause is established
  and no threshold was relaxed. The user subsequently explicitly approved guarded dev
  rollout despite that failure; this is not a performance acceptance pass.
- Forward-only 0290 and guarded gateway/worker/delivery/frontend replacements completed
  on isolated dev without reset. Health/readiness/login and anonymous-access gates pass.
  User-operated KB and archive results are metadata-corroborated. Invoice clarification
  exposed a fact-free `billing_invoice` plan with no agent tasks, not an invoice read.
  The offline fix refuses unsupported account-domain clarifications and confirmations
  while preserving grounded general KB answers; sandbox tests **11 passed**.
  Latest full rerun: **674 passed / 2 failed** (ASGI **508.717 ms**, TCP **298.288 ms**
  against unchanged `<200 ms`), 134 warnings, 2113.83 seconds. Lint and disposable-only
  reset pass. The user explicitly approved worker-only replacement despite both failures;
  the fix is live. Fresh user-operated refusal, sandbox KB and Telegram results are
  metadata-corroborated; audited archive, history reload and tab offline/online recovery
  pass. With separate approval, the user privately registered one synthetic reader;
  agent-operated `/portal/chat` confirms disabled creation, absent send/archive, empty
  owned list, other-owner active/archived histories 404 and cross-tenant selection 403.
  Reader own pre-existing history is test-proven, not live-replayed (new fixture).
  Latest unchanged, uninstrumented reader/backend + ASGI/TCP selection **2 passed /
  1 failed**: ASGI **93.234 ms** passes; TCP **261.451 ms** fails. Instrumented passing
  runs do not supersede this or full-run failures. No cause is established and no
  speculative runtime optimization was applied.
  No production, infrastructure or webhook changes. See [the API contract](../CONSOLE_API.md).
- Closeout decision: the user explicitly accepted functional M4 with a laptop-dev
  performance exception and carried latency work forward. Laptop hardware is reported
  context, not a measured cause. Do not claim green tests or relax `<200 ms`; performance
  remains release debt for M7/M8. M5 can begin under its separate authority boundaries.

## M3 implementation (2026-10-15)

- Authenticated/CSRF multipart upload, server UUID storage, strict extraction/signature
  validation, streaming 3-file/10 MiB-per-file/31 MiB-request limits and bounded headers.
  Stable tenant idempotency returns the original 202 job or conflict for changed files.
- Migration 0280 adds upload batches/files/deletion hashes and staged RLS. Durable
  atomic file publication precedes queue/metadata/audit commit; explicit storage
  reconciliation handles orphan/partial/deleted files without deleting active sources.
  Ambiguous commit cleanup checks durable metadata rather than deleting blindly.
- Tenant locks and pre/post-embedding validity/hash fences prevent deleted content
  resurrection, including legacy jobs and retries. Deliberate new uploads after
  deletion are allowed. Original-file download authorizes by source ID; already-open
  downloads may finish. Deletion removes linked duplicate aliases and searchable
  chunks, with confirmed UI semantics and audit.
- Adopted `/knowledge-base` and `/portal/faq` show originals, job/per-file status/errors,
  duplicates, versions, embedding metadata and gaps; reader writes are absent/denied.
  Curated public DTOs omit internal paths/requester/arbitrary job metadata.
- With explicit approval, persistent isolated dev was forward-migrated to 0280 without
  reset, gateway/frontend restarted and a guarded KB-only worker started for the synthetic
  M1 tenant. Tasks share a dedicated private upload root, overriding the unsafe configured
  repository-root value without editing the environment file. Only that tenant's KB
  subscription/switch were enabled; no full worker, delivery, bots or legacy import.
- Authenticated platform-admin browser checks pass: three-format upload reached done
  through loopback TEI, original bytes/hashes match, duplicate adds no chunks, confirmed
  source/document deletion removes aliases/chunks/download access, reconciliation retains
  active sources and audits are DB-corroborated. One synthetic DOCX/chunk remains for testing.
  Live acceptance found/fixed gap-list SQL argument drift in both callers; regression/
  launcher/upload/agent/contract selection **34 passed**, final lint passed. See
  [run instructions](../CONSOLE_API.md#isolated-dev-rollout-prerequisites).
  Tenant-role M3 browser replay and customer conversation retrieval remain unverified;
  populated gap UI, live version progression and disposable crash-window acceptance
  subsequently passed on 2026-10-16 (see [progress](../PROGRESS.md)). Read-only role
  denial is covered by disposable integration tests, not a live reader browser replay.
  No Gemini/provider-general claim.
- M3 follow-up: an unmatched synthetic KB-agent query appeared in authenticated
  Unanswered questions and survived polling; M1 showed no gap; resolving it removed the
  entry and produced one DB-corroborated audit. Two synthetic uploads of the same logical
  document showed version 1 superseded/version 2 active in the UI. No cleanup or source
  deletion was used.
- Disposable-only crash coverage: **15 passed** across commit acknowledgment loss,
  publication rollback, abandoned jobs, versioning, tenant/read-only boundaries and
  deletion/read/embedding races. Added an injected worker crash after chunks commit but
  before job completion; retry completes the existing job without duplicate documents
  or chunks and retains one upload batch/source/audit. The lost-COMMIT-ack test also now
  asserts unique batch/file/job/audit metadata after same-key replay. Focused rerun:
  **2 passed**; `make lint` passed. Persistent dev was not reset.
- Validation: disposable-only `make db-reset` succeeded at schema 0280 with all three
  new tables RLS-enabled. Python `make lint` passes. Focused upload/worker/streaming/
  agent/contract selection **57 passed**; final three-format retrieval/binary-contract/
  projection follow-up **3 passed** (overlap). Earlier broader identity/migration/RLS
  selection **103 passed / 1 failed**;
  the immutable-context fixture error was fixed and that upload test passes.
  Isolated frontend lint/types/production build/safe traces and **8 transport checks**
  pass. Full suite **607 passed / 2 failed**: unchanged 200 ms ingress thresholds,
  ASGI **266.718 ms**, TCP **887.834 ms** (150 completed turns/deliveries each).
  Final source-resource follow-ups are covered by focused checks and final lint.
  Unchanged isolated repeat **2 passed**: ASGI **123.840 ms**, TCP **137.444 ms**,
  150 completed turns/pending deliveries each. Full acceptance is not green and
  no cause for the variability is claimed. Detailed evidence is in
  [progress](../PROGRESS.md#m3-authenticated-kb-upload-and-lifecycle-2026-10-15).

## M2 browser follow-up (supersedes earlier pending notes)

With explicit approval the isolated gateway was restarted after served OpenAPI showed
stale M1 code. Authenticated platform tenant subscriptions, Agents and Runtime activity
then loaded without Not Found; zero recorded runs were labelled honestly. The user
subsequently confirmed the guided tenant-admin/read-only checks and cleanup. Those
role actions are user-confirmed, not a second agent-operated browser run. Populated-run
and live-provider browser acceptance remain unverified.

## M2 implementation (2026-10-15)

- Added session-scoped `/agents` metadata, separate subscription/tenant controls and
  specialised inspection navigation at `/agents` and `/portal/agents`.
  Platform users explicitly select a tenant; tenant admins control only tenant switches;
  staff/read-only users see state but no switches. Required safety capabilities cannot
  be disabled; initially disabled safety capabilities can be enabled deliberately.
- Readiness separates local functionality, prepared-only requests, missing implementations,
  setup, KB model compatibility and configured-but-unverified TEI. Default e-commerce
  wiring has no adapters; invoice lookup is not PDF delivery; MyInvois is not built.
  Removed fabricated provider/compliance metrics from specialised tabs.
- Added scoped `/overview`, stable ID-keyset `/runs` and `/runs/{id}` metadata APIs.
  `/activity` and `/portal/activity` poll recorded runs and existing trace events.
  Raw run payloads and customer text are not exposed; errors are not zero/empty fallbacks.
- Fixed the execution gap: production spoke attempts read current switches directly
  rather than trusting discovery caches/static planner descriptions. Existing callbacks
  and retries cannot bypass a committed disable. Console KB execution is also gated,
  and disabled KB upload jobs remain queued without spending attempts.
  Already-started/claimed work is not cancelled; existing breaker behavior is retained.
- Focused API/catalogue/identity/registry/runner/contract selection: **74 passed**.
  Subsequent runtime/order-callback/KB-queue/timeline/worker selection: **77 passed**.
  Selections overlap and must not be summed. Final Python lint passed. Isolated frontend
  transport/routing checks **6 passed**, ESLint/type generation/typecheck/production
  webpack build and environment-free build traces passed.
- No schema/dependency change or database reset. Tests use disposable Postgres and
  synthetic fixtures. Existing loopback frontend/gateway respond, and anonymous `/agents`
  redirects to login. The gateway has not been restarted to load these backend changes;
  authenticated M2 browser acceptance and all live provider checks remain pending.
  Full-suite result: **559 passed, 32 failed**. Fixed lifecycle-setting mocks and
  unprovisioned golden fixtures; gateway/golden/runtime/contract rerun **45 passed**.
  The full run also failed the ASGI 200 ms threshold at **853.178 ms**. Both unchanged
  transport benchmarks subsequently passed alone: **ASGI 92.637 ms**, **TCP 126.684 ms**,
  150 completed turns and 150 pending deliveries each. No clean full-suite rerun
  after corrections or cause of performance variability is claimed.

## Isolated dev browser session (2026-10-15)

The existing `csa-dev/postgres` database on port **5434** was verified and migrated
forward from 0240 through **0270**, without a reset. The user provisioned the
identity password, repaired URI encoding locally and bootstrapped the first admin
through private prompts. DSN target/role and identity readiness checks pass.
Initially only gateway **127.0.0.1:18081** and frontend **127.0.0.1:13000** ran as VS Code
tasks. The subsequent M3 activation above adds only the synthetic-tenant KB worker,
schema 0280 and loopback TEI ingestion; no full worker/delivery/bot or public proxy change.
Open **http://localhost:13000** through private VS Code forwarding.

Live gateway health/readiness, anonymous route gates, same-origin rewrite and
CSRF/no-store checks pass. User confirms login, forced password change, admin home,
tenant list and member screen work; audit verifies login/password change. The shared
agent browser has a separate unauthenticated session, so authenticated UI evidence
is user-reported. Guided lifecycle checks subsequently created synthetic tenant
`m1-browser-20261015` and tenant-admin/read-only users. Tenant creation, scoped
memberships, consumed invitations and nine disabled capabilities are DB-corroborated.
User reports role-based management gates and used-invitation rejection pass.
Logout acceptance subsequently passed: user observed **204** for the reader's
incognito-window logout request; a follow-up DB check corroborated reader logout
audit events and session revocation (latest 14:19:35 UTC). User reports direct
portal access after logout redirects to login. This resolves the earlier
redirect-only observation that lacked matching server-side evidence.
The frontend build/typecheck/lint and five transport tests passed before launch.
The historical TCP failures below are retained; subsequent M2 isolated checks pass both
transports (see the M2 record), but a clean full-suite rerun remains outstanding.

The review and planning snapshots below predate this explicitly authorized dev setup;
their no-migration/no-browser/no-process statements describe those earlier steps.

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
  runbooks and all `ui-guide/` contents remain. The deferred PM pack and historical
  source-state evidence were explicitly reference-only at that point. Nested Git
  metadata and the former gitlink were subsequently removed and reviewed source
  was adopted as ordinary parent-repository files. Local env files, frontend
  dependencies and the legacy backend remain excluded from Git. The old Docs tree,
  including the ignored drop snapshot/workflow export, was later removed on
  2026-10-20 by owner request. This is repository unification, not completed removal
  of the legacy application.
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
- At that historical review, the next bounded implementation was M2's entitlement/tenant-switch/role/provider
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
Status: implemented locally with focused evidence (see M2 record above). Authenticated
M2 browser acceptance and outstanding performance acceptance are not yet complete.

1. Harden current capabilities mutations; remove client-supplied `changed_by` as authority.
   Atomic before/after audit; protect required system capabilities. Fresh uncached authorization
   before each production attempt replaces cross-process cache invalidation as the enforcement
   mechanism; discovery caching is not authority.
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
   controls using existing pending-action codecs/context binding. **Approved M4 narrowing:
   the selected read-only sandbox rejects callbacks/confirmations; no mutation controls
   are exposed. A future broader scope needs a separate authority decision.**
7. Replace destructive-looking history deletion with audited archive/hide unless an explicit
   retention-approved purge is implemented. Do not invent `status='deleted'` semantics.
8. Keep voice and arbitrary attachments unavailable until their own end-to-end support.
   KB uploads remain a distinct action, not an automatic side effect of chat.

Exit (approved read-only scope): text → durable ingress → worker/graph → private held
draft or web outbox → visible reply; duplicates, wrong contact, crash/retry and
callback-denial tests pass. Held draft release and business confirmations are not part
of this sandbox. Existing Telegram delivery remains unchanged.

### M5 — Staff conversation inbox and approvals

M5 continuation at that stage: [Telegram scope and evidence](2026-10-17-m5-telegram-inbox.md).
The user selected Telegram-only customer transcripts/reply approvals for verified platform
admins with explicit tenant selection, tenant admins and tenant staff; readers/machines
are denied. Customer-web and stock-changing order decisions are deferred acceptance.
This bounded slice is reviewed and validated: **692 full tests pass**, lint and isolated
frontend checks pass. Explicitly approved forward-only dev0300 and coordinated replacement
are complete; platform-admin UI/synthetic Telegram acceptance and live reader denial
replay are verified. Representative tenant-admin/staff browser replay also passes;
fresh per-role approval transitions retain automated evidence.
Private sandbox contacts/history/holds stay excluded. Persistent dev was not reset.
The historical work package below is retained with this scope narrowing.

Dependencies: M1; web-channel approval round trip depends on M4.

M4 is the private read-only sandbox, not a staff-readable customer web inbox. Before
M5 implementation confirm customer-channel/message-read roles and whether a separate
customer-web product is in scope. Never expose sandbox histories or release their
private held drafts through staff APIs. If customer web is deferred, explicitly stage
that part of the exit criteria rather than using the sandbox as approval evidence.

1. Add scoped conversation/message reads with pagination, channel filters and safe source
   links. Restrict message content by membership/role; do not expose credential payloads.
2. Add audited takeover/release/human reply through shared domain services and outbox.
3. Extract reusable approval transition results from LG07 services where necessary.
   Current functions return structured shared transitions with Telegram text adapters;
   the Telegram edit adapter binds its staff action; provide a structured authenticated-console adapter, not a fake Telegram
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

#### Expanded seller outcome (approved direction 2026-10-17)

The user requested a **Business management** workspace for orders, tracking, product
listings, inventory and the operations needed to manage multiple ecommerce platforms,
starting with the reported existing TikTok Shop connection. This is an expanded staged
roadmap, not evidence that every seller operation is currently implemented or authorized.
User explicitly chose:

- Business records: verified platform admin with selected tenant, tenant admin and
  tenant staff only. Deny readers, machines and anonymous access, including direct reads.
- Initial inventory: **read-only local stock, explicitly not synchronized**.
  Stock-write authority/source-of-truth is deferred. Do not infer it from local tables.

Keep one tenant-scoped workspace with platform/store filters and common read contracts.
Retain native platform/store/order identifiers; distinguish local records, provider
snapshots, configured credentials, authorized connection and verified live integration.
No connected badge, fresh-sync timestamp or empty-marketplace claim based solely on local
records. Missing configuration and provider errors must remain explicit errors/unavailable
states. No fake successes or arbitrary JSON/credentials exposed to browsers.

| Stage | Deliverable | Exit / explicit gate |
|---|---|---|
| M6A — Local read foundation | Separate `/business` and `/portal/business`; bounded local order summaries/tracking references and catalogue/stock with generated read contracts | Staff-only human access, tenant isolation, stable pagination, decimal money, omitted customer address/contact/raw attributes; no provider calls or writes. Initial implementation below is this foundation only |
| M6B — Unified seller reads and connections | Platform/store filters; scoped store connection metadata/permissions/expiry; supported TikTok orders/items, products/variants/listings, shipment references and freshness/error status | Verify actual adapter wiring, account scope, API response contracts, pagination and provider error handling against mocked fixtures first. Live dev-store read/OAuth start/revoke needs explicit approval; no production credentials or webhook changes |
| M6C — Controlled TikTok seller operations | Supported listing create/edit/publish, price/stock updates, fulfilment/labels/dispatch, cancellation and return/refund handling | Separate action-authority approval; role/action permissions, tenant/store-bound identifiers, durable idempotent requests, audit before/after, retries/reconciliation and clear pending/failure states. Do not expose unsupported platform actions |
| M6D — Multi-platform catalogue and inventory | Canonical product/variant SKU mapping to each store listing, warehouses/reservations, low-stock alerts and additional adapters | Decide inventory source-of-truth (platform/local/external), reservation ownership, conflict resolution, oversell prevention, reconciliation and mapping uniqueness before enabling sync. Add platforms one at a time with conformance evidence; Shopee/Lazada draft code is not working integration |
| M6E — Business support and staff preparation | Read-only tickets/invoices plus preparation-request list/detail with actor/trace/status; verified-session `StaffActor` for supported email/research/invoice preparation | `prepared` never implies approved/sent/executed. Fix invoice signing fallbacks and implement real authorized PDF storage/serving before exposing download buttons. Missing signing configuration is an explicit error |

Seller workspace sections should cover an actionable overview (when real metrics exist),
orders, shipping/tracking, products/listings, inventory, returns/refunds and stores/connections.
Before each section is wired, display a specific unavailable state, not inert action buttons
or mocked success. Tracking numbers/carriers in local orders are **not live carrier tracking**.
The local catalogue is **not a platform listing**; active local products are not proof of
published TikTok listings. Product images/source links require separate safe URL handling.

Cross-cutting acceptance:

1. No widening of private sandbox authority or held drafts. No business API accepts browser
   actor identity. Explicit selected tenant for platform admins; reject cross-tenant cursors,
   resources and writes. Financial/customer fields need an explicit projection/access policy.
2. External/persisted adapter data uses Pydantic contracts; SQL stays parameterized under
   `db/sql/`. Preserve additive migrations and staged RLS, with disposable reset/migration tests.
3. Track provenance, provider/store IDs, last successful sync versus local update time,
   pending requests and operational errors. No cross-store stock writes until M6D decisions.
4. Role/read/mutation/tenant tests, pagination/empty/error states, cancellation/reload and
   real backend versus unavailable UI checks; generated contract, frontend build/types and
   backend lint/tests. Writes additionally require duplicate/retry/crash/reconciliation tests.
5. LHDN/payment execution stays disabled until separately approved sandbox verification.
   Google OAuth/watch/Calendar execution is a separate integration slice, not completed by
   env values/forms. No real production keys are required to migrate screens.
6. Persistent-dev migrations, restarts, live provider access/sends, infrastructure/webhooks
   and production remain outside implementation authority unless separately approved.

#### M6D inventory authority and reconciliation design (2026-10-18; user-selected direction)

The user selected the design direction below. This is still design only; no inventory
authority, warehouse/reservation schema,
marketplace synchronization, write endpoint or background reconciler is implemented by
this section. The existing workspace remains read-only local catalogue/stock; its counts
must not be presented as provider inventory or live availability.

**Selected authority model**

- Define authority for each tenant + canonical product variant + warehouse/location.
  Do not choose one authority for the tenant's entire catalogue: mixed ownership is
  necessary when some SKUs are local-manageable and other listings remain native to a
  marketplace or an external ERP.
- The CSA inventory ledger is the authority only for explicitly enrolled,
  CSA-managed variants. Existing `products.stock_qty` is a local operational projection,
  not evidence that a row is enrolled or that its number matches physical or marketplace
  stock. Keep non-enrolled rows local-only/read-only.
- Each managed variant has an explicit authority mode: `csa_managed`,
  `external_authoritative` (platform or named ERP, read-only in CSA), or `unmanaged`.
  No implicit mode inference from a store ID, SKU match, credential, connection status
  or imported snapshot. Switching authority is a reviewed cutover with a stock count,
  reservation/order reconciliation, mapping validation and recorded effective time;
  never an automatic failover.
- Store/channel listings are sales-channel projections, not independent stock masters.
  Until the relevant API semantics, order event coverage and write access are verified,
  treat provider quantities as observations only. Never write provider-observed values
  back over the CSA ledger or use them as physical on-hand.

**Quantity and ownership vocabulary**

- `on_hand`: physically held sellable units in one warehouse, changed only by an
  idempotent receipt, verified count or audited adjustment. A sale/fulfilment does not
  silently become a stock adjustment.
- `reserved`: active claims on those units, with a single owner and lifecycle. Include
  local order holds and, once a provider feed is explicitly enabled, imported external
  order claims. A provider order or event must be represented once; do not count both a
  webhook reservation and a mirrored local order as two claims.
- `safety_stock`: explicit policy quantity withheld from allocation, not a hidden
  negative adjustment. Initially zero unless the tenant deliberately configures a value.
- `available_to_allocate = max(0, on_hand - active_reserved - safety_stock)`.
  A reservation reduces availability but not on-hand. Shipment/fulfilment commits the
  stock decrement and releases its reservation atomically. Cancellation releases an
  uncommitted reservation once. A return adds sellable stock only after receipt and
  inspection; refunds/return status alone do not restock.
- Marketplace allocation is a separate per-store, per-variant quantity cap. Initial
  synchronization is fixed, non-overlapping store allocations whose sum never
  exceeds available-to-allocate. Treat an allocation as a hard cumulative sellable cap,
  not a quantity to blindly rewrite on every sync; first verify whether each provider
  quantity field means on-hand, available, or remaining after its own order holds. Do not
  offer a dynamically shared pool in the first write-capable slice: delayed external
  orders, webhook loss and human edits otherwise make a global availability value race
  across stores. Reallocation requires fenced reconciliation and confirmation that the
  provider accepted the reduced cap before any other store's cap is increased.
- Listing identity must be explicit and tenant-scoped: canonical variant + platform +
  shop + provider listing + provider variant, with database uniqueness preventing one
  provider variant from being mapped twice within a shop. SKU text is a match candidate,
  never a binding or write target by itself.

**Reservation ownership and order-state reconciliation**

- Begin with local-order lifecycle only: order lines own local reservations;
  the database transaction that changes order status owns the reserve/release/commit
  transition. Reservation identity must be stable and idempotent, and all quantities
  are aggregated by canonical variant and warehouse under deterministic row-lock order.
- Defer imported provider-order reservations until a later, separately gated slice. Then
  the provider order-line identity owns exactly one
  external reservation. Require exact tenant/shop/listing/variant mapping before
  reserving. Unmapped, duplicate, partially mapped, stale or contradictory orders are
  quarantined for staff resolution; do not guess a SKU or silently omit lines.
- Apply duplicate and out-of-order provider events idempotently using provider event or
  order revision when available, plus a durable source identity. A late cancellation
  cannot release a newer reservation; a repeated fulfilment cannot commit twice. If
  the provider exposes no stable event/revision, the adapter must document and test a
  deterministic fallback and preserve ambiguous transitions for review.
- The current `products.reserved_qty` is a materialized local-order reservation cache.
  `run_reservation_reconcile(repair=True)` recomputes it from configured local holding
  order statuses; it does **not** reconcile physical on-hand, imported marketplace
  reservations, provider listing quantities or listing mappings. Keep this narrowly
  scoped repair behavior distinct; do not extend it to external values by summing
  another source into the existing cache without an explicit migration and proven
  single-count lifecycle.

**Reconciliation and conflict policy**

1. Produce a timestamped, tenant/store/variant-scoped comparison with authority,
   mapping, source revision/event watermark, last successful observation, expected
   allocation, provider-observed sellable quantity and classified drift. Distinguish
   unavailable/stale data from a true zero; never infer "out of stock" from no response.
2. Reconciliation defaults to report-only. It does not alter `on_hand`, reservations,
   mappings or provider stock. Never auto-resolve a difference by selecting the larger
   or newer-looking number; timestamps/revisions are compared only within their source.
3. Missing mapping, stale watermark, unexplained quantity delta, incomplete order batch,
   unknown provider status or uncertain earlier write blocks further stock writes for
   that mapping/store allocation. Reads show the last observation time and degraded/
   conflict state; no green sync badge.
4. Resolution is either an audited physical count/adjustment, an explicitly reviewed
   mapping correction, or a source-specific order/reservation repair. Record actor,
   reason, before/after and originating evidence. Only after the conflict is resolved
   and a fresh source observation succeeds may writes resume.
5. Outbound updates, if later approved, are durable idempotent operations containing
   exact target listing/variant, allocation revision and desired quantity. Verify each
   provider's update semantics first. On timeout/response loss, mark outcome unknown,
   read/reconcile before retrying; never blindly replay a stock write. Record requested,
   accepted and subsequently observed quantities separately.

**Incremental enablement gates**

- Stage 0: current local read-only view; no sync, write authority or live claim.
- Stage 1: schema/API proposal and the synthetic acceptance matrix below, using generated
  fixtures only. A pass requires every applicable invariant to hold under duplicate,
  retry, stale-version, rollback and concurrent execution; example-based happy paths alone
  do not satisfy the gate.
- Stage 2: one platform and one explicitly enrolled synthetic/development store;
  read-only mapping/order/stock observation and drift reports, no provider writes.
- Stage 3: only after separate user approval, confirmed app eligibility and provider
  contract coverage, one bounded write path with durable intent, operator-visible
  ambiguous outcomes, audit, rollback/reconciliation drill and an explicit kill switch.
  Add channels one at a time. Shopee/Lazada draft adapters are not evidence of readiness.

**M6D provider-agnostic synthetic acceptance matrix (design only)**

This matrix defines later test evidence; it does not request implementation now. Use
synthetic tenant, variant, warehouse, order and shop identifiers. Do not encode a
TikTok/Shopee/Lazada endpoint, field name, quantity interpretation or API fixture before
that provider's official contract is verified. Every state-changing case must assert
both persisted state and audit/outbox state, including that a rejected operation leaves
no partial writes.

| ID | Scenario and synthetic action | Required pass condition |
|---|---|---|
| INV-01 | Enroll one canonical variant in each authority mode; attempt to infer/enroll another from a matching SKU, store record, credential or imported snapshot | Only explicit enrollment grants ledger authority. Non-enrolled/unmanaged rows remain unchanged; external-authoritative observations cannot mutate CSA on-hand. |
| INV-02 | Reserve a local order line, repeat the same transition, then cancel twice | Reservation identity is stable; the repeated transition creates no second claim; cancellation releases once; `on_hand` is not changed by reserve/release. |
| INV-03 | Fulfil a reserved line, replay fulfilment, and inject failure between stock commit and reservation release | Stock decrement and reservation release commit atomically exactly once. Injected failure rolls both back; replay cannot decrement again. |
| INV-04 | Return/refund an order without recording receipt/inspection, then record receipt and disposition | Refund/return status alone does not restock. Only an audited receipt/inspection of sellable units adds to on-hand; damaged/non-sellable units do not. |
| INV-05 | Reserve multiple order lines for one variant and concurrently place competing reservations whose combined quantity exceeds availability | Quantities aggregate per tenant/variant/warehouse under deterministic locking. No interleaving produces negative availability or reservations above eligible stock; excess is explicitly rejected/unavailable. |
| INV-06 | Use identical variant/order identifiers in two synthetic tenants and attempt cross-tenant reads and mutations | No data, reservation, audit entry or mutation crosses tenant scope; a rejected cross-tenant operation changes nothing. |
| INV-07 | Apply a zero-variance count, then submit a non-zero count variance and manual delta | Zero-variance count is auditable and does not change on-hand. Non-zero change remains pending and excluded from availability until approved by a second distinct tenant admin; submitter self-approval is rejected. |
| INV-08 | Approve a pending count/adjustment after another transaction advances ledger version or authority revision; also test rejection and no second approver | Approval cannot post against a stale version/revision. Rejection preserves evidence and changes no stock. Missing second approver leaves the command pending; there is no self-approval or emergency bypass. |
| INV-09 | Replay a count/adjustment command with the same idempotency ID, then reuse that ID with a different payload | Exact replay returns the existing terminal/pending outcome without a second ledger entry. Reuse with different scope or payload is rejected and audited without mutation. |
| INV-10 | Configure fixed caps for two stores, including a request whose sum exceeds available-to-allocate; attempt to increase one store before another store's reduction is confirmed | Accepted caps are non-negative, non-overlapping and cumulatively bounded by current allocatable quantity. No increase is released until the reduction is confirmed; unresolved reduction freezes reallocation. |
| INV-11 | Generate duplicate and out-of-order local order status transitions, including a late cancellation after a newer transition | Each local order line owns one reservation lifecycle. Stale transitions cannot release/commit a newer claim; ambiguous transitions are retained for review, not silently applied. |
| INV-12 | Feed synthetic provider observations with true zero, missing value, stale watermark, missing/duplicate mapping, incomplete order batch, unknown status and unexplained delta | Report distinguishes zero from absent/stale. Observations never change canonical on-hand/reservations/mappings. Any unresolved mapping/feed/status/drift conflict marks the scoped allocation degraded and blocks future writes. |
| INV-13 | Simulate an outbound operation with acknowledged success, explicit rejection, timeout/connection loss after dispatch, and partial batch result | Requested, accepted and observed values remain distinct. Uncertain outcome is durable `unknown`, blocks further writes for its target, and is never blindly replayed; reconciliation/read evidence is required before retry or resolution. |
| INV-14 | Attempt authority cutover with unresolved reservations, stale mapping, unexplained drift, in-flight/unknown operation, then retry with a current expected revision after resolution | Writes remain frozen on failed preconditions. Cutover is tenant/variant/warehouse scoped and compare-and-swap guarded; old authority stops before new authority can write. Rollback is a new audited cutover, never an automatic flip. |
| INV-15 | Inject database failure at each boundary of count approval, reservation transition, allocation re-fencing and cutover | Each operation is all-or-nothing across ledger state, reservation/allocation state and audit/outbox records; no success-shaped response or partial durable state remains after rollback. |

INV-11 in Stage 1 covers local order lifecycle only. Provider-order reservation cases are
**deferred** until a separately approved provider feed has verified complete status/event
coverage, stable order-line identity, revision/watermark and recovery semantics. Then add
synthetic duplicate delivery, out-of-order update, partial/unmapped order, mirrored-order
single-count and backfill/replay cases without changing the local-order acceptance baseline.
Similarly, INV-12/13 are provider-agnostic state-machine tests at Stage 1; binding them to
a live adapter or asserting concrete provider quantity fields is blocked on the official
versioned contract and the later read-only/write approvals. No real provider request is
part of this matrix.

**Persistence and concurrency acceptance plan (behavioral; no schema selected)**

The immutable prototype establishes single-process domain rules only. Before proposing
inventory tables or migrations, agree the observable transaction contract below. This
plan specifies outcomes, not an implementation strategy: row locks, compare-and-swap
updates or another mechanism are acceptable only if they prove the same behavior. It is
not yet database execution evidence; that requires a separately approved implementation
and must run against disposable PostgreSQL with synthetic fixtures and independent
connections before any persistent rollout or stock-write authority. Do not infer a schema,
isolation level, RLS policy or lock order from this test plan.

The focused database acceptance set must cover:

- **Competing reservations:** begin with a known eligible quantity and synchronize
  concurrent transactions attempting claims whose sum exceeds it. Committed active
  reservations never exceed eligible stock; excess claims are explicitly rejected.
  Replaying one order-line claim creates no second reservation or audit/outbox effect.
- **Terminal order transition race:** race cancellation and fulfilment for one reserved
  line, then replay both commands. Exactly one terminal outcome owns the claim: either
  cancellation releases it without changing on-hand, or fulfilment decrements on-hand
  and consumes the reservation. No interleaving releases and consumes it twice.
- **Return request and inspection:** concurrently request returns against the same
  fulfilled quantity and ensure accepted quantities do not exceed it. Race duplicate
  inspection commands; sellable on-hand is added once, only after receipt, with its
  inspection evidence. A refund or return-request status alone never adds stock;
  non-sellable received units add zero.
- **Approval against active work:** race non-zero count approval with a reservation or
  another version-changing command. The approval either commits against its expected
  ledger and authority revisions exactly once, or remains unapplied/rejected as stale;
  it cannot erase a newer reservation or let a pending count fund availability. A
  distinct second tenant admin remains mandatory.
- **Tenant isolation and replay scope:** repeat identifiers in separate synthetic tenants
  and attempt cross-scope operations. State, reservation claims, return evidence and
  audit/outbox records remain tenant-scoped. Same-key/same-payload replay is idempotent;
  same-key/different-payload reuse is rejected without partial success state.
- **Atomic failure injection:** inject an error between every coupled durable write for
  reservation, fulfilment, cancellation, count approval and return inspection. Verify
  ledger quantity/version, claim/return state and audit/outbox effects are wholly
  committed or rolled back according to the operation contract; no success-shaped
  partial state remains. If rejected attempts are audited, assert the specified rejection
  evidence without a stock mutation or success event.

When implemented, use barriers so races overlap rather than merely running sequentially,
read final state from a fresh connection, and assert both conserved quantities and exact
durable event counts. Run only against disposable Testcontainers Postgres and synthetic
fixtures; no persistent development database, production data, provider transport or
`make db-reset`. Record repeatable focused commands and outcomes. Until that evidence
passes against an approved implementation, the in-memory prototype remains a domain
demonstration, not proof of database correctness. Once this behavioral contract is
accepted, the next reviewable artifact can be a narrow additive schema and transaction
proposal; agreement is not approval to apply migrations or enable stock writes.

**Disposable candidate-schema feasibility check**

`tests/test_inventory_persistence_feasibility.py` now exercises a test-local candidate
schema using only the repository's disposable Testcontainers PostgreSQL fixture and
synthetic tenants, products, orders, items, warehouse and inventory. The focused command
`uv run pytest tests/test_inventory_persistence_feasibility.py -q` passed **4 tests in
10.47 s** (one existing Testcontainers deprecation warning). Coverage verifies composite
tenant/product relationships reject cross-tenant parents and mismatched claim products;
two barrier-synchronized reservations of four units against five available produce one
claim and one rejection; a stale count command is rejected without removing a newer
active claim; and an injected event constraint failure rolls back fulfilment account and
claim changes. `make lint` passes (Ruff, formatting, Pyright and import-linter).

Expanded continuation (2026-10-19): the same focused command passes **21 tests in 21.74 s**
with one Testcontainers deprecation warning. Added eight barrier-synchronized cancel/
fulfil races on independent async connections, deterministic coverage of either terminal
winner, exact terminal replay/opposite-transition rejection, and six rollback cases
injecting failure after account, claim or event writes for both terminal transitions.
Fresh-connection assertions verify exact quantities, versions, claim state and event
payloads; a failed transition can then be retried once without duplicate events.
Concurrent reservation replay also returns the existing active claim before checking
remaining capacity, rejects changed quantity, and cannot revive a terminal claim.
`make lint` passes. The fixture now uses non-UUID text order IDs, matching the baseline
`orders.id` and `order_items.order_id` types.
Combined regression command `uv run pytest tests/test_inventory_domain.py
tests/test_inventory_persistence_feasibility.py -q`: **1,373 passed in 32.92 s**,
one Testcontainers deprecation warning. `git diff --check` passes.

Return/replay continuation (2026-10-19): the candidate set now contains **36 cases**.
Combined `uv run pytest tests/test_inventory_domain.py
tests/test_inventory_persistence_feasibility.py -q` passes **1,388 tests in 49.21 s**,
one Testcontainers deprecation warning; `make lint` and `git diff --check` pass.
The fixture adds a tenant-composite return-to-claim relationship and test-local async
request/receipt/inspection transactions. Competing requests cannot collectively exceed
fulfilled quantity; duplicate requests persist one request/event. Request and receipt
leave on-hand unchanged. Inspection before receipt is rejected; duplicate inspection
posts once, including zero sellable units, and concurrent conflicting dispositions
preserve one winner. This candidate advances version once for an inspection even when
sellable quantity is zero; zero remains distinct from missing inspection.
Injected failures after request/receipt writes, each inspection account/return write
and event insertion roll back the complete transaction and permit a single-effect retry.
Invalid quantities, changed request quantity/order-line and changed inspection disposition
raise explicit errors without partial writes. Fresh-connection assertions check account,
return and event state, including exact event payloads on successful transitions.
Two tenants can independently reuse synthetic account/order-line/request/source keys
without reading or changing the other's records; missing tenant-scoped targets fail.
This deliberately composite-key fixture does not imply the baseline permits duplicate
globally unique product/order/item IDs.

This is a feasibility demonstration of candidate constraints and transaction patterns,
not application persistence, the full Stage 1 acceptance matrix, evidence about existing
database rows, deployed RLS/roles, or proof of production concurrency. Two-admin identity,
durable audit/outbox semantics, full-payload/tenant replay coverage across all command
types, refund/payment integration, order-state updates and actual application transactions
remain untested here. Return receipt/inspection flags are synthetic, not authenticated
receipt or inspection evidence. These
test-local helpers do not connect to the in-memory prototype or change legacy writers.
No application code, migration, DDL, runtime wiring or stock-write authority was added.
The full suite was not rerun for this test-only slice; the earlier 2,419-test result and
historical latency caveats remain prior evidence, not validation of the new cases.

Schema review confirms the conceptual tenant-composite anchors are still needed; baseline
single-column parent FKs cannot enforce same-tenant relationships. Nullable product lines
must remain outside enrollment. This candidate fixture creates its own tables; it does
not apply the proposed anchors to the baseline, test migration compatibility or implement
RLS/event immutability. Next offline step: separately review a narrow, non-authoritative
migration proposal with exact parent types/anchors, staged privileges/RLS, append-only
events and migration apply/reapply tests. Review must distinguish the return feasibility
subset from missing trusted actors, command fingerprints, count approval and application
transaction integration; no full Stage 1 completion or migration implementation is implied.
Existing-row audits, deployed settings/writers and physical counts are deployment/cutover
gates, not prerequisites for further synthetic development or reviewing a proposal.

**Candidate persistence shape (proposal only; not DDL)**

The baseline currently has tenant-scoped `products` and `order_items`, but no separate
canonical variant or warehouse relation. `products` has a tenant-unique SKU and stock
columns; the local order draft flow searches and locks by tenant + SKU, writes the matched
`products.id` to `order_items.product_id`, and adjusts stock/reserved quantities by that
product ID. Staff approval and customer cancellation also aggregate order quantities by
`product_id`. Thus the **current local order system treats a product row as its stock
unit**.

That behavior does not prove every real catalogue product is a single variant: the product
read model exposes `attributes` as unrestricted JSON, with no typed parent/variant or
option contract; order-line `product_id` is nullable and SKU/name are snapshots. The
ecommerce order snapshot contract contains SKU/name/quantity/price, and TikTok normalization
collapses `seller_sku` or `sku_id` into that SKU snapshot. `product_channel_map` has
optional `external_sku_id` but does not make it the local canonical ID. Do not use matching
SKU text, JSON attributes or provider identifiers to infer a variant relationship.

For an initial **local-only** ledger slice, candidate identity may be the existing
tenant-scoped `products.id`, but only for explicitly enrolled rows handled by this local
SKU-level workflow. It must not be described as verified marketplace-variant identity;
provider mapping and provider inventory remain deferred. If the product catalogue must
support parent products with selectable stock-bearing options, that product/variant model
must be defined before this ledger key can be generalized.

**Local-only relational proposal (conceptual; not DDL)**

Scope this first persistence slice to the existing local stock unit `products.id` and one
explicitly created warehouse per tenant. No provider mapping, marketplace inventory,
store allocation, provider order claim, sync worker or outbound stock operation belongs
in this slice.

| Concept | Candidate key and relationships | Required invariant |
|---|---|---|
| Warehouse | Tenant + warehouse ID; unique tenant + warehouse code. Create exactly one explicitly identified default warehouse for the initial slice; multi-warehouse behavior is deferred. | No warehouse is inferred from a product, store, provider, or empty ID. Tenant identity participates in every relationship. |
| Inventory account/head | Primary key `(client_id, product_id, warehouse_id)`; composite FKs to `(products.client_id, products.id)` and `(warehouse.client_id, warehouse.id)`. Fields: authority mode/revision, ledger version, on-hand, safety stock, created/updated timestamps. | Presence is explicit enrollment; there is no automatic backfill from `stock_qty`. Only `csa_managed` is eligible for future CSA changes. Quantities are non-negative. `active_reserved` is the sum of active claims for this account, not a second independently mutable quantity; compute it from claims under account serialization unless a later proven need justifies a transactionally maintained cache. |
| Reservation claim | Primary key `(client_id, order_item_id)` with `product_id` and `warehouse_id`; composite FK to account `(client_id, product_id, warehouse_id)` and composite FK to order item `(client_id, order_item_id, product_id)`. Fields: claimed quantity, active/cancelled/fulfilled state, timestamps and transition version. | One local claim per order line. The composite order-item FK proves the claimed product matches the line; null-product order lines cannot reserve this account. Claimed quantity must equal the order-line quantity at reserve time; after reservation, line product/quantity is immutable or any edit must be a separately audited atomic cancel-and-re-reserve transition. Quantity is positive. Terminal state cannot transition again. Exact command replay is a no-op; conflicting reuse fails. |
| Stock-change command | Primary key `(client_id, request_id)`; carries `product_id` and `warehouse_id` with composite FK to the account. Fields: command fingerprint, count/adjustment kind, submitter, verified actor-context reference, reason, expected ledger/authority revision, before quantity, counted quantity or signed delta, decision/approver/reason, resulting version. | Reuse with identical content returns recorded outcome; changed content is rejected. Non-zero change remains pending until a distinct tenant admin approves. Zero-variance count is recorded without changing on-hand. Pending non-zero command blocks new reservations/availability once the ledger is authoritative. |
| Inventory event | Tenant-scoped event ID; account key `(client_id, product_id, warehouse_id)`; nullable unique command reference and nullable reservation reference as appropriate. Fields: event kind, before/after on-hand and active-reserved values, version before/after, source transition key, verified actor-context reference, reason/evidence reference, occurred-at. | Insert in the same transaction as the state transition. Event history is append-only to runtime roles. Dedupe by stable source-transition/command identity; do not assume account version alone is unique because a zero-variance count can be recorded without advancing it. |
| Return case | Primary key `(client_id, return_id)`; carries `product_id`, `warehouse_id` and `order_item_id`, with composite FKs to the reservation/order line and inventory account. Fields: requested quantity, receipt actor/time/reason, inspection actor/time/reason, sellable quantity, resulting event reference. | Request cannot exceed fulfilled quantity across all returns for that order line. Receipt precedes inspection; inspection posts sellable quantity once. Refund/payment state alone has no inventory effect. |

Tenant-composite FKs require suitable unique parent keys. The baseline currently has
single-column primary keys, so a future additive DDL review must provide unique
`(client_id, id)` anchors for products, orders and order items, plus
`(client_id, id, product_id)` for order items. The order-item parent relationship must
also enforce `(client_id, product_id)` -> `(products.client_id, products.id)` and
`(client_id, order_id)` -> `(orders.client_id, orders.id)`; otherwise a composite child
FK cannot prove product and order tenant ownership. These are integrity requirements, not
migration instructions. The existing `order_items.product_id` is nullable, so null lines
remain outside inventory; they must not be silently resolved by snapshot SKU. The existing
SKU uniqueness is tenant-scoped, but SKU remains a lookup key, not the account PK.

**Transaction boundary proposal**

All of the following are one database transaction apiece. Every command first establishes
tenant and verified actor context, validates its idempotency key/fingerprint, then checks
the account authority/revision and expected ledger version. Serialize changes to the same
account; the exact locking/isolation mechanism remains open. Calculate active reserved
from the account's active claims while holding the account serialization boundary; do not
trust a separately updated `products.reserved_qty` value. Enforce
`available_to_allocate = max(0, on_hand - active_reserved - safety_stock)` and reject a
new claim that exceeds it. Update account version and the related claim/command/return plus
its event atomically. On any error, roll back all success state; do not emit a
success-shaped result. For commands spanning several accounts, process account keys in
deterministic order.

| Command | Atomic writes and checks |
|---|---|
| Draft reservation | In the same transaction as order and order-item creation, require explicit CSA enrollment and sufficient available units; create one claim per local order item (the active-claim sum increases); append reserve event(s). Existing duplicate-SKU lines must be aggregated before stock validation or rejected as duplicate lines. |
| Cancel/reject/expiry | Lock/validate order and active claims; transition each claim to cancelled once; release reservation quantity; update order state and append release events. A terminal order or claim replay does not release again. |
| Fulfil/approve | Lock/validate order and active claims; ensure on-hand covers each claim; decrement on-hand and active reservations together; transition claims and order; append fulfil events. Any partial-line failure rolls back the whole order transition. |
| Count/adjust request | Lock account, bind before-value and expected revisions, persist command. A zero variance records command/event only. A non-zero command remains pending and cannot change on-hand. |
| Count/adjust approval or rejection | Require separately verified second tenant admin for non-zero approval; re-check command state and expected revisions under serialization. Approval posts the quantity delta, increments ledger version and appends event atomically; rejection only records decision evidence. Replays cannot post twice. |
| Return request/receipt/inspection | Serialize cumulative return quantity per fulfilled line. Receipt records evidence only. Inspection validates receipt and remaining returnable quantity; add sellable quantity, version and event exactly once with inspection result. |

Existing `products.stock_qty` and `reserved_qty` are currently written by order flows and
read by catalogue/order views and reservation repair. They must not continue as an
independent competing authority. Keep the proposed ledger tables non-authoritative while
legacy flows remain unchanged. Before cutover, a separately approved cutover must:
(1) freeze local order state transitions and all order/expiry/repair writers;
(2) verify the configured stock-holding order statuses and resolve or explicitly retain
every draft/pending order; derive active line claims from that verified lifecycle and
reconcile them against `reserved_qty`;
(3) record an approved physical count for each explicitly enrolled product rather than
blindly copying `stock_qty` as physical truth;
(4) seed one warehouse account and its local active claims with auditable source evidence;
(5) verify account on-hand, claim totals, and the legacy projection under a compare-and-swap
revision; then (6) atomically switch all order writers, readers and repair jobs to the
ledger, maintaining legacy columns only as same-transaction projections if compatibility
requires them. Coordinate the application release so no old writer can resume after the
authority switch; a database flag alone cannot fence an already-running incompatible
application version. Any mismatch, unknown holding status or unresolved order blocks
cutover. Rollback is a new audited cutover, not a silent revert. Never dual-write outside
one transaction or run two stock authorities concurrently.

**Tenant isolation and privilege proposal**

Every tenant-owned table carries `client_id`; use tenant-composite FKs to prevent
cross-tenant product/order/account references. Enable RLS with the existing
`app.client_id` policy convention, without `FORCE` while the owner-role path remains.
The runtime role may read/insert/update account and command state only through reviewed
operations; revoke event `UPDATE`/`DELETE` even if broad default grants otherwise apply.
No actor role is trusted from an input parameter: authorization uses the verified
application actor/membership context. Validate RLS for `csa_app` and read-only behavior
for `csa_ro` with disposable tests. Audit events are not a provider outbox; provider work
remains excluded.

**Review findings and disposition (2026-10-19)**

Review tightened four ambiguities before any schema proposal can become DDL:
(1) the local reservation must carry product and warehouse keys and prove its product
matches the order item, not merely reference both independently;
(2) active reserved quantity has one source of truth—the sum of active claim rows under
account serialization, not a second mutable counter;
(3) the initial slice has exactly one explicitly identified default warehouse, with
multi-warehouse support deferred; and
(4) cutover must reconcile rather than copy `products.stock_qty` as physical truth, freeze
all order/expiry/repair writers, verify configured holding statuses, account for pending
orders and claims, seed from approved counts, and coordinate release versions so old
writers cannot return after the authority switch. The necessary composite parent anchors
are now called out as a prerequisite because baseline primary keys alone do not prove
tenant/product/order consistency for child rows. Reservation quantity must remain bound
to the order-line quantity or use an audited atomic cancel/re-reserve edit.

**Narrow migration-proposal review (2026-10-19; documentation only)**

Disposition: the feasibility model is useful evidence, but copying its DDL into
`db/migrations/` would omit required contracts. Recommend an identity-only first
migration, followed by separately reviewed integrity and ledger work. This is a proposed
scope, not authorization to create migration files, run the migration runner or change
gateway readiness. No version number is reserved here.

Source reviewed: [baseline](../../db/schema_baseline.sql),
[role/default grants](../../db/migrations/0100_roles.sql),
[grant audit/RLS pattern](../../db/migrations/0310_ecommerce_grants.sql),
[identity memberships](../../db/migrations/0270_console_identity.sql),
[migration runner](../../scripts/migrate.py) and
[migration tests](../../tests/test_migrate.py). No database rows or environment files
were inspected. The runner applies each unrecorded migration and its version stamp in
one transaction; apply/reapply means runner-level ledger idempotency, not executing raw
CREATE statements twice.

| Stage | Proposed scope | Explicit exclusions and acceptance boundary |
|---|---|---|
| A: identity scaffolding | Keep existing global PKs and SKU uniqueness. Add only the tenant-composite unique anchors needed for later FKs; create an empty tenant warehouse registry with explicit default identity. | No enrollment, account quantities, claims, commands, returns, events, backfill, triggers on legacy stock/order writes, runtime registration or write grants. Even this stage requires implementation approval and disposable migration tests. |
| B: parent integrity | Separately add same-tenant order-item/product and order-item/order FKs and validate them after synthetic compatibility checks and an approved existing-row preflight. | These constrain legacy writers even without inventory enrollment. Not part of Stage A; no automatic remediation or persistent validation is authorized. |
| C: inert ledger persistence | Review account/claim/command/event/return schema and immutable command/evidence contracts, then test real migrations against baseline parents and least-privilege roles. | No default enrollment, authoritative initialization, production data, provider outbox or legacy writer switch. Actor/count/transition contracts below must be resolved before writable operations are granted. |
| D: application integration and cutover | Separately approve atomic order/ledger transitions, authenticated approval, count initialization and all-writer fencing. | All existing deployment/physical-evidence gates apply. Schema presence never enables authority. |

Stage A key/type proposal:

- Preserve `products.id` and `order_items.id` as UUID, `orders.id` and
  `order_items.order_id` as text, `client_id` as UUID, and the legacy column `qty` as
  integer. No UUID conversion of order IDs or renaming `qty` to the fixture's `quantity`.
- Add unique `(client_id, id)` anchors to products and orders, and unique
  `(client_id, id, product_id)` to order items for the proposed claim relationship.
  A separate `(client_id, id)` item anchor is needed only if an accepted child FK
  actually references that pair; do not add unused redundant indexes.
- Warehouse: UUID ID with unique `(client_id, id)`, UUID tenant FK to `clients`,
  nonblank tenant-unique code and an explicit default marker. A tenant-scoped partial
  unique index may enforce **at most one** default, not existence of exactly one.
  Later enrollment must require an explicitly selected default; migration seeds none.
  Multiple-warehouse enrollment remains outside the initial runtime scope.
- Unique anchors alone do not fix or prove parent tenant consistency. Null-product
  order lines remain valid legacy records but cannot own a ledger claim. Preserve
  existing PKs, deletes and stock behavior; do not infer links from SKU snapshots.

Parent-integrity review: `NOT VALID` is not a disabled FK. It skips the historical scan
but enforces subsequent inserts/updates, so adding these constraints changes legacy
write behavior immediately. Plan existing-row checks and validation separately;
do not advertise Stage B as behavior-neutral. The runner's per-migration transaction
also precludes casually inserting `CREATE INDEX CONCURRENTLY` into its SQL files.
Persistent lock/scan duration and release scheduling need their own approved deployment
plan; disposable timings are not production capacity evidence.

Privileges/RLS proposal:

- New tables inherit `csa_app` table DML from migration 0100. Revoke inherited rights
  explicitly from `PUBLIC`, `csa_app` and `csa_ro` within the creation transaction;
  grant only tenant-scoped SELECT to application/read-only roles for the inert registry.
  No insert/update/delete/truncate or callable mutation helper is granted in Stage A.
- Enable the existing `app.client_id` USING/WITH CHECK policy, without FORCE.
  Test missing tenant, tenant A/B, and actual `csa_app`/`csa_ro` connections; owner tests
  are not RLS evidence. The owner can bypass RLS, so this does not fence legacy n8n.
- Later inventory events must reject runtime UPDATE/DELETE/TRUNCATE and public helper
  execution. Prefer the existing hardened trigger-owned append pattern (qualified
  objects, fixed search path, revoked direct execution) once complete event inputs are
  defined; a table INSERT grant alone cannot guarantee one audit per state mutation.
  Define zero-delta event identities independently of ledger version and prevent
  deletion cascades from erasing audit history. No event table/helper is proposed for
  Stage A, so it cannot imply that audit enforcement is implemented.

Unresolved before Stage C write-contract approval:

- Pydantic persisted command/evidence contracts with stable tenant-scoped command keys,
  canonical payload fingerprints, explicit conflict outcomes and retained decisions.
  The feasibility helpers compare selected fields, not a complete production fingerprint.
- Session-derived admin identity and membership revalidation within the approval
  transaction, a distinct service/contact principal for order events, and durable
  submitter/approver evidence with no self-approval. Do not give `csa_app` broad access
  to identity tables merely to implement an inventory FK.
- Count pending/freeze rules and concurrent approval: the conceptual authority model
  blocks new reservations on pending non-zero counts, while the feasibility stale-count
  case deliberately allows a reservation to advance the version. That test proves
  stale rejection only, not the pending-freeze rule; both need explicit coverage.
- Return evidence must contain authenticated receipt/inspection identity, time, reason
  and immutable disposition. Fixture booleans and quantities are not that evidence.
  Keep cumulative quantity serialization and explicit zero-versus-missing disposition.
- Atomic order status, quantity edits, ledger/claim transitions and mandatory event
  insertion, including multi-line rollback; no helper currently proves the integrated
  application path or durable rejection audit. Provider work remains excluded.

Implementation acceptance for Stage A, if separately approved: disposable baseline plus
all ordered migrations; exact types/unique anchors/catalog privileges/RLS flags; existing
synthetic legacy order and stock operations unchanged; no automatic rows/enrollment;
tenant-role read isolation and mutation denial; unchanged legacy PK uniqueness/null
product behavior; runner reapply leaves one stamp and unchanged objects/data. Inject a
migration failure in an isolated test fixture to prove DDL and version-stamp rollback.
Add no failure-only migration to the real directory. If a migration is introduced later,
update the gateway's exact expected version and its readiness/migration tests together;
this review leaves source expectation 0350 untouched. Validation must stay disposable,
with no persistent `make db-reset`, provider requests or service changes.

Review outcome: proceed next with separately approved Stage A implementation or resolve
Stage C contracts in documentation first; do not treat all Stage 1 criteria as complete.
This review changed documentation only. The prior **1,388-test** combined regression and
lint result remain prior evidence; no new code or database validation run was needed.

**Stage A implementation evidence (2026-10-19; user-approved bounded continuation)**

Added [0360_inventory_identity.sql](../../db/migrations/0360_inventory_identity.sql)
with the three proposed composite unique anchors and the empty warehouse registry.
Existing global PKs, text order IDs, UUID product/item IDs and nullable product lines
are preserved. Warehouse constraints enforce tenant/code uniqueness, a nonblank code,
tenant FK and at most one default per tenant; no default or other warehouse is seeded.
RLS is enabled without FORCE, inherited privileges are explicitly revoked, and runtime
roles receive SELECT only. No account/claim/command/event/return table, enrollment,
stock backfill, same-tenant parent FK, legacy trigger or mutation helper is added.
Gateway source and migration-test expected schema version are 0360. This changes source
readiness requirements, not the persistent database or running services; rollout requires
separate approval and the corresponding migration.

[test_inventory_identity.py](../../tests/test_inventory_identity.py) covers the catalog,
actual non-owner role isolation and write denial, warehouse constraints, existing-row
synthetic upgrade preservation, unchanged stock SQL/null lines/global PKs, runner reapply
and transactional migration-failure rollback. A synthetic pre-existing cross-tenant
parent link remains untouched, proving Stage A does not smuggle in Stage B enforcement.
Readiness coverage now includes a missing current version as well as an older gap.
`NO_LOCAL_ENV=1 ENV=ci make lint` passes. Combined command:

```sh
NO_LOCAL_ENV=1 ENV=ci uv run pytest \
  tests/test_inventory_identity.py tests/test_inventory_domain.py \
  tests/test_inventory_persistence_feasibility.py tests/test_migrate.py \
  tests/test_health.py tests/test_order_agent.py \
  -q -k 'not test_make_reset_only_disposable_container'
```

Result: **1,421 passed, 1 deselected, 1 warning in 119.07 s**. One existing Testcontainers
deprecation warning; the reset test was intentionally deselected. Database tests ran only
on disposable containers with synthetic fixtures. No persistent reset/migration, environment
inspection, service restart or provider request occurred. Full-suite/latency acceptance
was not rerun; prior full-suite results and historical latency failures remain prior
evidence. Stage A does not establish ledger correctness, admin/count evidence or cutover
readiness. Next: resolve persisted Pydantic command/actor/evidence contracts, canonical
fingerprints and pending-count freeze semantics before proposing writable ledger storage.

**Offline count-command contracts (2026-10-19)**

Added [contracts.py](../../csa/inventory/contracts.py), a frozen extra-forbidden Pydantic
count/adjustment request and review contract, not a repository, authentication mechanism
or runtime adapter. Account identity is UUID tenant/product/warehouse. Admin evidence
retains tenant/user/session, explicit tenant-admin role and aware UTC verification time;
constructing this model proves no identity or membership. Future server adapters must
load and revalidate evidence, including current session/membership, at transaction time.
Service/contact actors, physical receipt/inspection evidence and persisted terminal
records remain separate unfinished contracts.

The request binds command ID, account, submitter user, kind, normalized reason, expected
ledger/authority revisions, before quantity and counted quantity/signed delta. Integers
are strict; resulting quantities must fit non-negative PostgreSQL integer range.
The schema-v1 SHA-256 fingerprint uses sorted ASCII JSON with explicit separators.
It includes all business fields and stable submitter user, excluding replaceable session
ID/verification time (retained separately as audit evidence). A renewed verified session
for the same user therefore permits exact replay; a different user or business payload
does not. This digest is not an authorization proof or a persisted deduplication fence.
Reasons are stripped before hashing; no other textual equivalence is inferred.
Non-zero changes require a distinct same-tenant admin review; zero-variance commands
are recorded without review. Review models alone do not enforce current roles, stale
versions, terminal outcomes or database atomicity.

A focused prototype regression clarifies the existing pending-count policy: pending
non-zero changes expose zero allocatable quantity and block new reservations; existing
fulfilment may proceed and stale the count. Approval then fails stale; explicit rejection
removes the pending freeze without changing stock. This reuses existing domain behavior;
no runtime or domain transition was changed. The feasibility stale-count fixture that
allows a new reservation still proves only stale rejection, not this freeze policy.
How exact reservation replay behaves while frozen needs explicit transaction-contract
review; do not infer it from the count request fingerprint.

Validation: `NO_LOCAL_ENV=1 ENV=ci make lint` passes; `NO_LOCAL_ENV=1 ENV=ci uv run pytest
tests/test_inventory_contracts.py tests/test_inventory_domain.py -q`: **1,383 passed in
7.45 s**. No database used, migration/reset run, provider access or full-suite/latency
rerun. Stage A migration 0360 remains the latest schema source. Next: define persisted
count outcomes and actor/evidence loading boundaries, plus reservation replay under
freeze, before writable ledger DDL or application integration.

**Outcome and replay continuation (2026-10-19; offline only)**

`StockChangeRecord` now defines persistable pending/recorded/approved/rejected shapes:
pending requires non-zero change and no decision/result; recorded requires zero variance
and the unchanged version; approval requires a matching distinct-admin decision and
expected version plus one; rejection retains the decision without a stock-result version.
The decision must reference the exact retained command/evidence. Exact command replay
returns that same record without changing its state, including pending/terminal outcomes;
changed business content raises explicitly. These are validation rules, not implemented
database transitions, event insertion or stale-version enforcement.

`TenantAdminLoader` and `reload_tenant_admin` introduce an explicit injected async
boundary with no default implementation. The result must match tenant, user and session
and cannot predate claimed verification; loader errors propagate. The future adapter must
validate actual current session/membership inside the stock transaction, reject
revocation/expiry, and serialize approval against changes as required. Matching model
fields or a timestamp alone are not authentication, nor is this helper wired to console
identity. No database role permissions or identity-table access were added.

Intentional prototype behavior change: reservation lookup and quantity matching now
precede pending-count and reservation-shortfall checks, after tenant/authority/input
validation. Exact existing reservation replay returns the unchanged ledger during a
count freeze; changed quantity still fails, new claims remain blocked, and terminal
claims are not reactivated. This follows the prototype's existing terminal no-op behavior,
not a new successful reserve transition. Authority mismatch still rejects replay.
Count freeze still permits existing fulfilment and stale-approval rejection.

Validation: `NO_LOCAL_ENV=1 ENV=ci make lint` passes; `NO_LOCAL_ENV=1 ENV=ci uv run pytest
tests/test_inventory_contracts.py tests/test_inventory_domain.py -q`:
**1,391 passed in 7.60 s**. `git diff --check` passes. No database, migration/reset,
provider/service access or full-suite/latency rerun. Source remains0360. Next: review
persisted count transition/audit schema and transaction semantics using these contracts,
including trusted actor adapter implementation and count-freeze concurrency; service/
return evidence contracts and full application integration remain gated.

**Count storage/transaction review (2026-10-19; no writable DDL approved)**

Disposition: **not ready for writable persistence**. Existing models validate record
shapes and the prototype validates sequential rules; neither enforces transitions across
database rows. The following findings are design blockers, not claims of an exposed
runtime defect. No inventory writer or default actor loader exists.

| Priority | Finding and evidence | Required disposition |
|---|---|---|
| High | Shape validation is not transition validation. `StockChangeRecord.validate_outcome` accepts a structurally valid approved record without a previous pending row or current account state; it cannot check authority, before-value, account version or prior terminal decision. | Future approval must load and serialize current account and command state, compare fingerprint/authority/version/before-value, then CAS only pending to approved. A terminal decision cannot be replaced. Do not expose record construction as a write API. |
| High | Pending freeze needs durable serialization independent of ledger version. Request/rejection can change availability without incrementing that version. The earlier stale-count feasibility test does not implement freeze. | Serialize pending insert/rejection and every new reservation on the same account boundary; add at-most-one-pending uniqueness per account. A cached ledger version alone is insufficient for availability. Exact replay reads existing state before freeze checks, but only after current authorization and scope checks. |
| High | Actor reload is not transaction-bound by its interface. `TenantAdminLoader` has no connection/unit-of-work argument; an implementation could perform an independent read and lose a revocation race before stock commits. Existing console identity resolution uses its own pool transaction. | Bind a future loader to the caller's transaction or an explicitly reviewed atomic authorization boundary, with deterministic locks/CAS for session/user/membership changes and approval. Avoid casually nesting identity queries on another pool or granting broad identity access to `csa_app`. Prove both approval-wins and revocation-wins ordering. |
| High | Persistable outcomes do not yet contain a durable audit envelope. They retain request/reviewer snapshots but no event ID, command decision revision, decision/record time or observed rejection version/authority. Optional repository event insertion could leave a valid-looking outcome without audit. | Define immutable request/decision event envelopes and exact transaction-owned append enforcement before event-table DDL. Store event occurrence separately from actor verification time. Each accepted transition must atomically persist account/command/event; deny direct event mutation and duplicate logical transition keys. No provider outbox belongs here. |
| Medium | The earlier transaction table says approval **or rejection** rechecks expected revisions, while `reject_stock_change` intentionally permits rejecting a stale command to clear its freeze. A generic version guard would make that recovery impossible. | Approval requires original ledger/authority/before-value agreement; authorized rejection may clear a stale pending command without posting stock. Retain its observed current revisions and reason. Stale approval failure alone leaves pending state/freeze; it must not silently become a rejection. |

Proposed transaction sequence for a future isolated implementation:

1. Establish current trusted actor and tenant through the transaction-bound adapter.
   Revalidate even for exact replay; do not grant access based on a historical record's
   `role` or verification timestamp. Fix identity/account/command lock order across
   requests, decisions and membership/session revocation before coding concurrency.
2. Locate the stable tenant + command key. Exact request replay returns retained
   outcome/evidence without mutation even when current ledger revisions differ.
   Changed payload/account/submitter is a conflict, not a new command. Decision replay
   must also compare reviewer user, outcome and normalized review reason; a renewed
   session may authorize replay but never replace retained original evidence.
3. For a new request, serialize account, enforce explicit CSA authority and expected
   authority/version/before-on-hand, then reject another pending command. Insert a
   zero-variance recorded outcome/event or non-zero pending outcome/event. Never let
   the pending proposed quantity fund new reservations.
4. For approval, serialize the pending command and account, require distinct current
   tenant admin, and recheck original fences. Atomically change on-hand/version,
   terminal command/decision and mandatory event. A stale approval fails without
   stock or command mutation. Check integer/bigint bounds before posting.
5. For rejection, require a current distinct admin but allow a stale pending command.
   Persist rejection evidence/event and remove its freeze without changing stock or
   ledger version. Existing cancellation/fulfilment continues to use its own atomic
   transitions; no count may erase their claim state.
6. Return success only after commit. Inject failure after every coupled write and
   audit append; rollback must leave no success event, partial stock or lost freeze.
   Exact retries may observe a committed terminal outcome but must never post twice.

Recommended next bounded work: a **test-local disposable count transaction harness**,
not another application migration. Cover request versus reservation, approval versus
fulfilment, two pending requests, approve versus reject, concurrent exact/conflicting
decision replay, stale rejection recovery, actor revocation ordering, and event-write
failure rollback. Use synthetic actor adapters and clearly label them as such; they
cannot verify real console identity integration. Read final account/command/event state
from fresh connections and assert exact payloads/counts. The production adapter and
complete immutable audit envelope still require implementation review before writable
ledger tables or runtime grants.

This review changed documentation only; no new code validation was run. Prior evidence
remains **1,391 contracts/domain tests** and lint passing, not database count-transaction
evidence. Migration source remains0360; no persistent database, reset, service or
provider access occurred.

**Disposable count-transaction evidence (2026-10-19)**

Added [test_inventory_count_transactions.py](../../tests/test_inventory_count_transactions.py),
a **20-case test-local harness** using a private schema in disposable Testcontainers
PostgreSQL, async independent connections and synthetic actors. It does not apply
application migrations or connect a runtime stock writer. Fixture state is one account,
synthetic enabled/disabled principals, count records, an aggregated reservation counter
and a selected event payload; it is not a tenant/RLS, real identity, order-line claim or
complete immutable evidence implementation.

Evidence covers:

- Barrier-synchronized request/reservation and competing-request races: only a valid
  serial outcome commits, pending quantities never fund availability, and at most one
  pending command is allowed by a test-local partial unique index.
- Approval versus fulfilment, duplicate approvals, approve/reject and conflicting
  reviewer races: one terminal decision owns the command, fulfilment is conserved,
  changed review reasons conflict, and exact request/decision replays add no events.
  Self-review is rejected before mutation; zero variance records without freezing or
  changing stock/version.
- Independent ledger-version, authority-revision and before-quantity fences. Failed
  stale approval retains pending state; authorized explicit rejection can clear it
  without stock changes and records observed current revisions. Existing fulfilment
  can stale the count while new reservations remain frozen.
- Ordered synthetic approval/revocation transactions share an actor-row lock held
  through commit. Approval-first may commit then be revoked; revocation-first blocks
  approval. Even a replay requires current enabled identity. This proves the simplified
  row-lock pattern only, not console session/membership or cross-pool authorization.
- Seven fault cases after request/decision/account writes or a deliberately failing event
  CHECK. Fresh-connection snapshots prove complete rollback; a retry has exactly one
  effect with checked quantities, versions, terminal state and event payload.

Validation: `NO_LOCAL_ENV=1 ENV=ci make lint` passes. Combined command:

```sh
NO_LOCAL_ENV=1 ENV=ci uv run pytest \
  tests/test_inventory_count_transactions.py tests/test_inventory_contracts.py \
  tests/test_inventory_domain.py tests/test_inventory_persistence_feasibility.py -q
```

**1,447 passed, 1 warning in 63.76 s**. Warning: existing Testcontainers deprecation.
`git diff --check` passes. Full suite and latency checks were not rerun. No reset,
persistent database, provider request, environment-file inspection or service change;
migration source remains0360.

At the time of the preceding harness evidence, it compared a selected request tuple and
had no persisted Pydantic command/event roundtrip. The following audit-envelope slice
closes those selected fingerprint/payload gaps, but not real session expiry/revocation,
tenant isolation, production privileges, mandatory trigger-owned audit or multi-account
deadlock proof. The test caller still writes its own event; fault injection proves
transaction rollback, not that an arbitrary writer cannot omit audit.
**Immutable count audit envelope and disposable composition (2026-10-19)**

Added a frozen `StockChangeAuditEvent` in
[contracts.py](../../csa/inventory/contracts.py). It retains event UUID, typed command,
transition kind, actor evidence, timezone-aware occurrence time, canonical command
fingerprint, observed ledger and authority revisions, before/after on-hand, resulting
version and optional decision. Shape validation binds tenant and fingerprint, keeps event
time at or after actor verification, requires request events to preserve stock and match
command fences, requires approvals to post the proposed quantity exactly once at the
expected revisions, and allows stale rejection only when stock/version are unchanged.
This is an immutable payload contract, not database append enforcement or transition
authority.

The synthetic disposable count harness now serializes typed command, decision, record
and event payloads in the same transaction. Read-back assertions validate record
fingerprints and model shapes, bind events to the stored command/event ID, and compare
terminal event decisions with the stored record. Request, approval, stale rejection and
injected rollback/retry paths exercise these checks. Contract tests round-trip pending,
approval and stale-rejection events and reject tenant, fingerprint, transition and time
mismatches. The prior fulfilled-count replay now explicitly supplies the original
before-on-hand value; strict replay fingerprints remain unchanged.

Validation: focused count/contracts **59 passed, 1 existing Testcontainers deprecation
warning in 38.44 s**; combined count/contracts/domain/persistence **1,448 passed,
1 warning in 92.68 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` and `git diff --check` pass.
The full suite and latency checks were not rerun. No reset, persistent database,
environment-file, provider or service access occurred; schema remains 0360 and no
runtime writer/migration/grant was added.

**Test-local database audit enforcement and identity-boundary review (2026-10-19)**

The disposable `count_trial` schema now tests a database-enforced candidate boundary:

- A `BEFORE` trigger permits only initial `pending`/`recorded` commands and a single
  `pending` → `approved`/`rejected` transition. It protects the retained command
  payload/fingerprint from updates.
- A `DEFERRABLE INITIALLY DEFERRED` constraint trigger checks at commit that every
  inserted command or changed state has a matching event. It compares event transition,
  ID, fingerprint, command JSON and decision JSON with the persisted command state.
- An append-only trigger rejects event `UPDATE`, `DELETE` and `TRUNCATE`.
- Tests omit request and approval events, mismatch the event fingerprint, attempt event
  mutation/truncation, replace a terminal decision and mutate the command fingerprint.
  Rejected transitions roll back; existing successful count scenarios continue to commit.
- A synthetic non-owner `count_trial_app` can commit a correctly audited request, but
  cannot commit without its event, mutate events, disable triggers or set
  `session_replication_role`.

This is **test-local DDL only**, not an application migration. The harness connects as
the container `csa` superuser; it does not prove protection from the table owner or a
superuser, deployed `csa_app` grants, production RLS, or immunity to a privileged trigger
disable. The synthetic role has only the grants deliberately applied in this test. An
actual design must keep runtime writers from owning these tables or having DDL/trigger
privileges, define a separate protected owner/execution boundary if needed, and prove
that the required writer role cannot bypass audit. The `csa` owner/n8n legacy constraint
means that owner bypass and deployment compatibility must be explicitly designed; no
runtime privilege change is approved here.

The identity integration review also found a transaction-boundary gap. In
[identity.py](../../csa/console/identity.py), `IdentityService.resolve` reads/locks the
session and fetches memberships using a dedicated `csa_identity` pool transaction, then
returns an actor after that transaction commits. Tenant writes use a separate
[transaction helper](../../csa/core/tenancy.py) and `csa_app` connection. The session
lookup uses `FOR UPDATE`, but that lock ends before any subsequent domain write; the
membership list is not locked. `csa_app` has no identity-table access. Therefore a
previously authenticated tenant-admin snapshot is not transaction-bound to a future
inventory write, and the synthetic actor-row lock is not evidence of real revocation
ordering.

Current identity changes also lack one established global row-lock order: membership
changes update the membership and then revoke sessions; user disable updates the user
then sessions; password-reset issuance revokes sessions before updating the user.
Any future transaction-bound loader must be designed with the session, user and
membership revocation paths together, then acquire inventory account/command locks in a
consistent order. Do not solve this by granting broad identity reads to `csa_app` or
by assuming the synthetic actor lock proves ordering. No loader, identity migration,
runtime writer or DDL was introduced.

Validation: focused count transactions **29 passed, 1 existing Testcontainers
deprecation warning in 43.73 s**; combined count/contracts/domain/persistence
**1,457 passed, 1 warning in 114.59 s**. `NO_LOCAL_ENV=1 ENV=ci make lint` and
`git diff --check` pass. The full suite and latency checks were not rerun. No reset,
persistent database, environment-file, provider or service access occurred; schema
remains 0360.

Next bounded step: settle an inventory ownership/grant model that addresses the legacy
`csa` owner/n8n bypass constraint, then test the chosen grants and trigger ownership with
disposable roles. Separately reconcile one identity/session/membership revocation lock
order and exercise its competing transitions before proposing a migration. Writable
inventory persistence remains gated.

**Design prerequisites and deployment/cutover gates**

The user confirmed that the repository schema is the available source and approved
continued synthetic development (2026-10-19). No old database or n8n dump is needed.
Before a migration proposal is accepted, specify and test the intended constraints,
actor contract, lifecycle and transaction boundaries using disposable fixtures. Do not
confuse that design work with the following operational evidence, which is required
before applying changes to existing databases or enabling authoritative inventory:

1. Whether existing rows satisfy the proposed composite product/order tenant relationships;
   if not, define explicit remediation or leave those rows outside the enrolled ledger.
   Do not silently rewrite cross-tenant or orphaned references.
2. The trusted actor principal contract for both console-admin commands and customer/order
   lifecycle events. Approval role must resolve from verified tenant membership; automated
   order transitions need a distinct, auditable service/contact principal rather than a
   fabricated console-user ID.
3. Source review confirms the current reservation-repair default is `draft` and
   `pending_confirmation`; draft creation reserves, staff approval commits stock and moves
   the order to `confirmed`, customer cancellation is limited to draft/pending, and expiry
   releases draft claims. Reconfirm this lifecycle against the deployed configuration and
   every order/repair writer before migration; the code default alone is not rollout
   evidence.
4. How deployment fences/drains every old writer and repair job during cutover, and how
   the maintenance/kill switch prevents an old application version from resuming writes.
5. The count evidence and approval procedure for initializing enrolled ledger on-hand;
   legacy `stock_qty` is a comparison/projection only, not the accepted physical count.

Source has now confirmed the current local default lifecycle, but the deployed setting and
writer fleet still require verification. Actor principal and operational cutover fencing
remain unresolved. Source review and synthetic fixtures can advance the design without
pretending to verify deployed state or physical counts. A migration proposal needs separate
review; applying it to persistent databases and enabling authoritative writes need separate
approval and the applicable evidence above. Do not alter existing tenant relationships.

Verification status (2026-10-19):

- **Schema relationship — not guaranteed by baseline constraints.** The baseline gives
  `products`, `orders`, and `order_items` single-column primary keys plus separate
  `client_id` FKs. `order_items.order_id` and `product_id` reference only the parent IDs;
  the schema does not enforce that the order and product belong to the same `client_id`.
  No persistent rows were inspected, so actual orphan/cross-tenant mismatches are unknown.
  A future disposable fixture can prove the constraint gap; any real-row preflight requires
  an expressly approved isolated database and must be reported without repairing rows.
- **Order lifecycle — current source default verified; deployment unverified.**
  `run_reservation_reconcile` defaults to `draft` and `pending_confirmation`; the job
  runner does not pass a replacement holding-status list. Draft creation reserves,
  customer confirmation retains the hold, staff approval commits at `confirmed`, customer
  cancellation releases only `draft`/`pending_confirmation`, and expiry handles draft
  orders. LG10 itself calls the holding set an assumption. No deployed configuration or
  runtime state was checked.
- **Admin actor — not propagated to the order approval transaction.** Console identity
  resolves a session-derived user and membership role, but the console agent path does not
  expose an order-approval mutation. Telegram `AgentContext` contains tenant/contact/
  trace, not the approving human. `ApprovalInput.staff_target_id` identifies the intended
  staff target; it is not evidence of the callback actor. Future non-zero count approval
  cannot safely reuse this path until a verified `ConsoleActor` identity and tenant-admin
  role reach and are revalidated by the transaction boundary.
- **Cutover writer fence — no verified mechanism.** Production compose runs gateway,
  worker and delivery services, and source includes order expiry/reconciliation writers.
  No inventory maintenance gate or all-writer drain/version fence has been implemented or
  exercised. Do not infer operational fencing from container restart policies.
- **Initial physical count — not performed or approved.** No physical count evidence,
  approver or per-product initialization record exists in this proposal. `stock_qty` is
  not accepted as the count. Enrolled accounts stay uninitialized until a separately
  approved synthetic/development procedure is specified; no seller/live count is requested.

Current actor-source evidence: customer order flows carry tenant/contact/trace context;
`AgentContext` has no verified staff actor, while the order-approval input identifies a
staff target rather than the authenticated approving human. Console APIs do have a
session-derived `ConsoleActor` and tenant membership role, but that identity is not yet
propagated into the order agent transaction. Future approval evidence must use that verified
admin identity; order lifecycle events may use a typed contact/system principal. Never
record the staff target or a caller-supplied role as proof of who approved.

**Product/variant identity verification (2026-10-19)**

The local catalogue-backed order flow treats one tenant SKU match and its resulting
`products.id` as the stock/reservation unit. It does not establish semantics for arbitrary
product attributes or parent/child options, or prove one local row maps one-to-one to an
external listing SKU. The user selected a first local-only proposal scoped to explicitly
enrolled `products.id`; it must not be generalized to provider inventory or selectable
stock-bearing options without a separately defined variant and order-line contract.

This remains a proposal only. It does not authorize schema changes, migrations, runtime
wiring, stock mutation or provider synchronization.

**Offline domain prototype evidence (unwired)**

An immutable in-memory rules prototype now lives in
[`csa/inventory/domain.py`](../../csa/inventory/domain.py), with synthetic unit tests in
[`tests/test_inventory_domain.py`](../../tests/test_inventory_domain.py). It exercises
exact tenant/variant/warehouse scoping, explicit CSA authority, local reservation
idempotency and fulfil/cancel transitions, the selected second-admin count/adjustment
approval rule, ledger-version and authority-revision fences, fixed allocation caps with
confirmed-reduction ordering, and unknown outbound outcomes that freeze allocation
changes until a matching synthetic observation resolves them. Returns add stock only
after receipt and inspection, and only the inspected sellable quantity is restocked.
Report-only reconciliation distinguishes a real zero/match from zero/drift, missing and
stale observations, invalid mappings, incomplete feeds, unknown statuses and incomparable
quantity semantics. It emits typed report rows without mutating ledger state. Deterministic
generated sequences exercise reservation conservation and accepted store-cap bounds.
State is immutable so a rejected transition leaves its input state unchanged.

Focused evidence: `uv run pytest tests/test_inventory_domain.py -q` — **1,352 passed**;
`uv run pyright csa/inventory/domain.py tests/test_inventory_domain.py` — **0 errors**;
`NO_LOCAL_ENV=1 ENV=ci make lint` — passed, including Ruff, formatting, repository
Pyright and import-linter. Generated action sequences are deterministic exhaustive small
state sequences, not a concurrency or database-locking proof. This does not implement the
full INV-01..INV-15 matrix: it has no durable audit/outbox, refunds/returns integration,
transaction rollback, true concurrency, provider observation parser or provider order
lifecycle. It is not imported by the application and has no persistence, API, UI, provider
transport, runtime registration or stock-write authority. The database reset was not run;
this prototype adds no schema or migration.

Final repository validation for this prototype: `uv run pytest` — **2,419 passed,
136 warnings, 1,874.60 s**, using the test suite's disposable Testcontainers Postgres
fixture; `NO_LOCAL_ENV=1 ENV=ci make lint` passes; `git diff --check` passes. This run
reported no test failures, including latency checks. Earlier documented latency failures
remain historical evidence and are not waived or erased. No persistent development or
production database was used, and `make db-reset` was not run.

**Selected policy and detailed design proposal**

The user selected (2026-10-18): CSA ledger per explicitly enrolled variant; fixed,
non-overlapping per-store quotas rather than a shared pool; local orders first, with
provider-order reservations deferred until event coverage and exact mappings are verified;
one default warehouse initially; tenant admins may record future physical counts or
adjustments with a required reason. This selection does **not** enable inventory writes:
no stock mutation, sync, schema, endpoint or reconciler is authorized or implemented.

The following operating controls are a conservative design proposal for review, not
additional user-approved policy or implementation authority.

**Count and adjustment audit contract (proposed)**

Every future count/adjustment command must be tenant-, variant- and warehouse-scoped and
bind to the authority mode/revision and ledger version it observed. Persist immutable
evidence sufficient to reproduce the decision:

- stable command/idempotency ID; command type (`physical_count` or
  `manual_adjustment`); tenant, canonical variant, warehouse and authority revision;
- actor ID and role, submission timestamp, effective/count-observed timestamp, and
  required reason code plus non-empty explanation;
- ledger version and `on_hand` before the command, observed physical count or signed
  adjustment delta, and derived after-value; never accept caller-supplied before/after
  values as authoritative;
- optional tenant-scoped evidence reference (for example, an attachment ID), not
  embedded credentials, customer data or unbounded/raw provider payloads;
- approval actor, decision, timestamp and reason when approval applies; final posting
  timestamp and resulting ledger transaction/version.

A physical count records an observation, not a reservation edit. Applying a discrepancy
changes only on-hand; active reservations, safety stock and per-store allocations remain
separate and must be re-evaluated. A manual adjustment is a signed delta against the
observed ledger version, not a blind set operation. Commands are idempotent. If the
ledger version changed after observation, reject/review the stale command rather than
rebase it silently. A count/adjustment conflict freezes allocation/write decisions for
the affected variant until posted or explicitly resolved. These rules preserve actor,
reason, before/after and source evidence without allowing a stale count to erase newer
orders or reservations.

**Approval policy (user-selected 2026-10-18)**

The user approved requiring a second distinct tenant admin to approve every non-zero
manual on-hand change, including a physical-count variance; the submitter cannot approve
their own command. A zero-variance count is recorded but does not change on-hand and needs
no second approval. This deliberately avoids an arbitrary unit/percentage threshold that
behaves differently across variants. Keep the change pending and exclude it from
available stock until approval; approval must re-check the bound ledger version and
current authority revision. Rejection preserves the audit record and makes no stock
change. If no second authorized admin is available, fail closed and leave the command
pending; there is no emergency self-approval path. This is a selected future policy, not
an implemented approval workflow.

**Provider quantity contract and evidence gate**

CSA's canonical quantities remain physical `on_hand`, active reservations,
`safety_stock`, and a fixed per-store allocation cap. A provider value is a separate
observation until a platform/market/endpoint/version-specific contract proves its meaning.
Each adapter must explicitly document and test:

1. exact read and write endpoint/version, market, item/SKU/warehouse granularity, field
   names, units, bounds and whether quantities are absolute sets or relative deltas;
2. whether a returned/settable number means physical seller stock, provider sellable
   stock, available-to-buy, or a value after provider reservations/promotions/fulfillment
   allocations; which actors/systems consume it and how those holds are reflected;
3. warehouse and fulfillment-mode scope, partial/batch failure behavior, write
   idempotency, read-after-write consistency, revision/watermark support, and the complete
   order/reservation event lifecycle needed to avoid counting a claim twice;
4. the tested transform from one CSA store cap to the provider field and back to a
   comparable observation, including drift categories and the behavior when any input
   is unknown.

Never compare unlike quantities or infer one field from a similarly named field. If a
provider field includes provider-managed holds, compare it only with a projection that
accounts for those holds exactly once; if that cannot be proven, keep that adapter
report-only and do not claim reconciliation. A write is blocked for that mapping when its
field semantics, order watermark, mapping, or prior write outcome is unknown.

Official starting references for contract verification are [TikTok Shop Products API
overview](https://partner.tiktokshop.com/docv2/page/products-api-overview),
[Shopee Open Platform documentation](https://open.shopee.com/documents), and
[Lazada Open Platform inventory documentation](https://open.lazada.com/apps/doc/api?cid=5&path=%2Fproduct%2Fprice_quantity%2Fupdate).
The public pages retrievable for this design review did not expose a complete, versioned
inventory field contract. Accordingly, no platform-specific quantity meaning, event
completeness, endpoint support, app eligibility or write behavior is asserted here.
Shopee/Lazada draft adapters and existing TikTok order-read verification are not
inventory-contract evidence. Record the verified market/version, official field
definitions and synthetic request/response fixtures in the adapter's conformance evidence
before a read or write stage is proposed.

**TikTok inventory contract verification attempt (2026-10-18; unresolved)**

TikTok is the proposed first platform for contract research because it is the current M6B
focus. The user selected Malaysia as the target market. This does not authorize provider
access or approve an inventory operation. The following official references were checked:

- [Products API overview](https://partner.tiktokshop.com/docv2/page/products-api-overview)
  and [Get Product v202309](https://partner.tiktokshop.com/docv2/page/get-product-202309)
  returned the Partner Center JavaScript application shell without a rendered endpoint
  schema in the public retrieval available for this review.
- The user supplied [Update Inventory v202309](https://partner.tiktokshop.com/docv2/page/update-inventory-202309)
  for the Malaysia target. It also returned only the Partner Center JavaScript shell;
  no operation description or rendered schema was available.
- The user suggested `/product/202309/products/{product_id}/inventory/update` as a
  possible operation path. This is a useful search/cross-check candidate, but it has not
  been confirmed by the official rendered reference and is **not** treated as the API
  path or an implementation contract.
- The [TikTok Shop Public Workspace “Update Stock” request](https://www.postman.com/tiktok-shop-open/tiktok-shop-public-workspace/request/4qd7tj1/update-stock)
  is listed in the official Postman workspace, but the request page did not expose its
  method, URL, parameters or body schema in the public retrieval available here.

Consequently, this review did **not** verify that an inventory operation exists for the
Malaysia seller market/version or establish its method/path, request/response fields,
quantity semantics, warehouse/fulfillment scope, order holds, idempotency, consistency,
permissions or event coverage. Search-generated summaries for the supplied URL suggested
generic, conflicting placeholder endpoints and fields; these and third-party examples are
not accepted as contract evidence. The existing M6B v202309 order-reference verification
is unrelated and does not prove an inventory contract.

**Documentation needed from Partner Center (no API call):**

1. The rendered title/version/market and exact operation page, showing method and full
   path (confirm or reject `/product/202309/products/{product_id}/inventory/update`).
2. Request path/query/header/body fields and types, plus success and error response
   schemas; redact credential values, shop IDs and seller/product data.
3. The official definition of the quantity field: absolute set versus delta, unit/bounds,
   sellable versus physical/on-hand meaning, and how provider order/promotion/warehouse
   holds affect it.
4. Applicable warehouse/fulfillment mode, required permission/scope, per-item/batch
   behavior, idempotency/retry and read-after-write consistency/revision behavior.
5. The corresponding official inventory read operation and fields, if available, plus
   official order-event/status coverage relevant to whether provider reservations can be
   counted exactly once.

A sanitized screenshot or copied documentation text is enough; do not open or use the API
Testing Tool, send a seller request, or include credentials/tokens/shop IDs. If the
reference does not state a behavior, mark it unknown rather than infer it. Until the
versioned official contract is reviewable, TikTok inventory remains report-only and no
adapter request, provider read, or stock write is proposed.

Meanwhile, offline work can continue only at the provider-agnostic conformance level:
define synthetic cases for exact variant binding, non-negative bounded quantities,
absolute-versus-delta semantics, warehouse scope, partial batch outcomes, stale/missing
observations, duplicate/out-of-order order claims, unknown write outcomes and read-after-
write drift. These cases must not encode TikTok route/field assumptions; bind them to an
adapter only after the official contract is verified.

**Authority cutover and rollback procedure (proposed)**

1. Enroll an exact tenant + canonical variant + warehouse and explicitly choose
   `csa_managed`, `external_authoritative`, or `unmanaged`. Verify tenant/shop/listing/
   variant identity and uniqueness; SKU text alone never binds a mapping.
2. Freeze stock writes and store-cap reallocation for that variant. Record a cutover
   request with actor, reason, source/destination, expected authority revision and
   effective-time intent. Resolve or explicitly quarantine outstanding/ambiguous
   operations before proceeding.
3. Take and audit a physical count; reconcile local order holds and, only in a later
   approved slice, provider order claims. Verify every provider listing mapping and
   source watermark. Do not infer physical stock from a provider quantity.
4. Run read-only comparisons through a fresh observation window. Any stale/missing
   mapping, incomplete order feed, unexplained drift or uncertain write aborts the
   cutover and keeps writes frozen.
5. Commit the new authority with a compare-and-swap on the expected revision, a single
   effective time and an immutable audit record. The old source becomes read-only before
   the new source can issue writes; never run two authorities concurrently.
6. Resume only the explicitly approved bounded action after the cutover is observed and
   reconciled. A rollback is a new, audited cutover: freeze writes, resolve pending
   operations, recount/reconcile and advance the authority revision. Never copy a stale
   provider number or flip authority back automatically.

For any future provider-order reservation phase, require documented complete status
coverage, stable order/line identity, duplicate/out-of-order behavior, cursor/watermark
semantics, recovery/backfill and reconciliation against provider order snapshots before
counting external claims. Until then only local-order reservations are included.
Confirm external ERP/platform authority per variant. The separate app/seller eligibility
and live-acceptance gates remain applicable before any provider access or write.

#### Current M6A implementation evidence

- Offline foundation implemented: typed `/console/v1/business/orders` and `/products`,
  bounded keyset pages and scoped summary-detail endpoints, separate protected frontend
  routes/navigation, local tracking fields and stock/reserved/available counts.
- Local records have explicit source/unavailable-sync metadata. No arbitrary JSON, customer
  address/contact, credential fields, provider URLs or tokens enter these read models.
  Money uses decimal contracts, not floating-point conversion.
- No migration/dependency change/provider calls/service replacement. Last approved dev
  build/schema is M5/0300; a current read-only probe found no18081/13000 listeners.
  Service restoration/replacement requires separate approval; no M6 live claim.
- Inspection found `EcommerceAgent()` constructed without adapters in runtime dependencies;
  TikTok adapter code exists but is not proof of a live wired store. Shopee/Lazada remain
  draft adapters. Do not connect production or read credentials to resolve this during M6A.
- Targeted backend/contract **6 passed**; isolated frontend **13 transport + 2 state
  tests**, lint/types/build/trace checks pass. Full lint passes; **695 passed / 1 TCP
  latency failure**, ack p95 **788.334 ms** against unchanged **<200 ms**. Disposable-only
  reset tests pass. Exact metrics and warnings are in [progress](../PROGRESS.md).
  Full validation is not green; M4's accepted exception is not an M6 waiver.
  M6A is not the whole M6 exit or a live rollout claim.

#### M6B order-read contract groundwork (offline, not provider verification)

- Existing TikTok order detail/search parsing now validates success envelopes, required
  identifiers/status/payment/currency/items and finite nonnegative decimal amounts.
  A rejected or malformed payload cannot become a zero-valued/default order or empty
  successful list. Detail identity must match the requested order.
- `EcommerceOrderPage` and `TikTokShopAdapter.search_order_page` retain the opaque
  continuation token. One bounded provider request per page; duplicate/oversized pages
  and unchanged continuation tokens are explicit failures. The legacy `search_orders`
  return remains a bounded first-page list, not a complete-history claim.
- Order reads use header-only access tokens, disable redirects even on an injected
  redirect-following client, and expose sanitized `TikTokReadError` codes instead of
  provider bodies, signed request URLs or raw transport/validation exception messages.
  This does not claim that legacy product/write methods have the same protections.
- Synthetic `httpx.MockTransport` coverage: **41 passed** including existing ecommerce
  tests. Full lint passes; **732 passed / 1 TCP latency failure**, 136 warnings,
  2701.96 s. TCP ack p95 **608.321 ms** against unchanged **<200 ms**; disposable-only
  reset checks pass. Full validation remains **not green**, with no new performance waiver.
  Exact full-suite results are recorded in [progress](../PROGRESS.md).
  No browser route, provider call, credential lookup, runtime wiring or store badge added.
- This validates the repository's existing synthetic payload assumptions, **not current
  official endpoint/response compatibility**. Public official documentation returned only
  a shell via fetch and redirected the browser to login; no login was attempted.
  Search summaries are not authoritative contract evidence. Before any live read, verify
  the detail endpoint/envelope, pagination query-versus-body placement, line-item quantity/
  price semantics, and search-summary versus detail fields against an approved current
  specification. Do not guess aliases or manufacture missing fields.
- Tenant UUID is still not a credential binding in the legacy adapter. A verified
  tenant/store-bound resolver, scoped connection permission/expiry metadata and common
  safe browser projections remain required before console exposure. The normalized
  internal order retains its existing buyer identifier and is not a browser projection.
  Product/variant contracts, platform/store filters and connection lifecycle remain
  pending; M6B is not closed by this groundwork.

#### M6B local source/store filters (offline continuation)

- Local order list/detail now accept exact persisted `channel` plus optional `shop_id`.
  Store IDs require a channel; supported channels match the existing orders constraint,
  not the list of working provider adapters. No migration, store discovery or credential
  inspection is required. Missing store IDs remain visible when no store filter is set.
- Tenant, source and store constrain both results and cursor/detail lookup. Foreign/
  mismatched scope returns404; unknown store produces an empty local page without
  confirming or denying a connection. Equal-timestamp pagination retains ID tie-breakers.
- UI uses explicit Apply and visible applied scope. Changed tenant/filter scopes reset
  paging and abort obsolete reads. Local catalogue/inventory stays tenant-wide and is
  explicitly unaffected by order filters; these filters do not establish listing mappings.
- Targeted backend/contract selection: **10 passed**, 1 warning, 166.26 s. Isolated frontend:
  **14 transport + 2 state tests**, lint/types/production build/trace-boundary checks pass.
  Full lint passes; **736 passed / 1 TCP latency failure**, 136 warnings, **2713.67 s**.
  TCP ack p95 **235.195 ms** against unchanged **<200 ms**; ASGI p95 **104.607 ms**
  passes. Disposable-only reset checks pass. Full validation is **not green**, no new
  waiver or latency retry. Exact metrics are recorded in [progress](../PROGRESS.md).
- A temporary synthetic-only API/frontend fixture passed loopback HTTP readiness, but the
  shared browser could not reach either loopback hostname (connection refused). It was
  stopped and removed; no browser rendering, pagination or abort-race acceptance is claimed.
  No forwarding/infrastructure change was made. Authenticated live acceptance remains gated.
- No TikTok account page/credentials were inspected, no provider API called, and no
  persistent-dev restart/reset performed. Tenant/store credential binding, authoritative
  provider specification verification and safe connected-store metadata remain pending.
  This is another partial M6B slice, not live seller integration or M6B closure.

#### M6B local store identities and binding (offline continuation)

- Added human staff-only `/console/v1/business/stores` list/detail and Business workspace
  local store-record panel. Existing `channel_credentials` is the source of record IDs,
  tenant, platform/store IDs, expiry and local update timestamps only. SQL never selects
  access/refresh tokens or arbitrary `meta`, nor checks/decrypts credential contents.
  No migration, dependency or secret provisioning is needed.
- Immutable `StoreBinding` holds tenant/record/platform/store identity. The bound
  detail path matches all four against local records, with authenticated tenant scope;
  cross-tenant, platform/store mismatch or missing store identity cannot resolve.
  This is **metadata identity binding**, not a provider credential resolver. It does
  not change or secure the legacy adapter's caller-supplied tenant parameter.
- Immutable public `StoreRecord` omits tenant/private metadata and permits only
  `authorization=unverified`, `permissions=unknown`, `provider_reads=unavailable`.
  Unknown/expired/future recorded expiry is evaluated explicitly; future expiry never
  means valid token, granted scopes or verified authorization. The schema has no
  trustworthy scope/revocation/live-verification evidence; none is fabricated.
- Marketplace-only list supports optional platform and stable UUID-descending keysets.
  Null-store records are visible but not bindable. Paging is not ordered by verification
  or update recency. Tenant/platform changes reset store pages; errors remain explicit.
  No connection lifecycle controls, provider calls, runtime wiring or token reads.
- Initial backend/contract selection **15 passed**, 1 warning, **229.13 s**; final pure
  contract selection **3 passed**, **1.70 s** (overlap; adds one no-authority contract case).
  Isolated frontend **15 transport + 2 state tests**, lint/types/build/traces pass.
  Full lint passes; **742 passed / 1 TCP latency failure**, 136 warnings, **2766.25 s**.
  TCP ack p95 **200.161 ms** against unchanged **<200 ms**, scheduled ack **204.904 ms**.
  ASGI p95 **98.408 ms** passes. Disposable-only reset checks pass; full validation
  remains **not green**, without a latency retry, threshold change or new waiver.
  Exact final full validation is recorded in [progress](../PROGRESS.md).
- Keep authoritative current provider-spec verification, explicit dev-store approval,
  durable permission/revocation/verification evidence and a separately guarded credential
  resolver ahead of any provider-backed console reads. This does not close M6B.
  No signed-in TikTok account pages or real credentials were inspected.

#### M6B official TikTok request verification (2026-10-17; offline correction)

Official rendered API Reference pages were read directly through the shared documentation
browser, without opening account settings or the API Testing Tool, inspecting credentials,
or calling a seller API. Earlier unversioned URLs/search summaries were not reliable
endpoint evidence. These findings supersede the earlier inaccessible-documentation gate
**only for the inspected v202309 fields**, not all provider compatibility:

| Official reference | Confirmed contract | Implementation/evidence |
|---|---|---|
| [Get Order List v202309](https://partner.tiktokshop.com/docv2/page/get-order-list-202309) | `POST /order/202309/orders/search`; `page_size` and opaque `page_token` are query parameters; `order_status` is a JSON body filter | Corrected placement before signing, preserving token bytes and bounded local limit1..50 (provider allows1..100); no silent page walk |
| [Get Order Detail v202309](https://partner.tiktokshop.com/docv2/page/get-order-detail-202309) | `GET /order/202309/orders`, comma-separated `ids` query (provider max50), response `data.orders` | Corrected legacy path-based request and single-object assumption. Existing single-order method sends one ID, rejects comma injection and requires exactly one matching result |
| [Get Authorized Shops v202309](https://partner.tiktokshop.com/docv2/page/get-authorized-shops-202309) | `GET /authorization/202309/shops`; seller token and authorized shop/cipher evidence needed for shop APIs | Documentation verified only; no shop lookup/token use implemented or called |

Both inspected order endpoints require `seller.order.info` **or**
`seller.fs.order&fufillment.management`; authorized-shops requires
`seller.authorization.info` **or** that same FS scope (retain the provider's exact spelling).
Required scopes in docs are not evidence an existing token has them. Tokens remain in
headers, never request query strings; content type is application/json, including GET.
The API Reference navigation currently links a newer202507 detail version; this work
deliberately verifies existing202309, not a silent version upgrade or a guarantee of
market/app eligibility or ongoing provider support.

**Remaining response blocker:** inspected v202309 order line fields include a distinct
line-item `id`, `sku_id`, `seller_sku`, `sale_price` and product name. No `quantity` field
was present in that inspected schema. Existing normalized `OrderItemSnapshot` requires
quantity and the legacy parser expects `sku_sale_price`. Do **not** assume quantity1,
aggregate repeated SKUs, alias prices or silently discard lines. A synthetic documented-
shape line without quantity is regression-tested to fail explicitly. Existing successful
normalization fixtures still describe the old internal assumptions, not actual provider
compatibility. Complete a typed provider line snapshot/projection and verify quantity/
bundles/discount/currency semantics before enabling normalized live order reads.
The parser's buyer field also remains legacy-shaped; do not widen customer projections
or treat this request correction as a complete response-model migration.

Before any guarded credential resolver/provider read:

1. Require server-verified tenant/record/platform/store binding, not a browser actor,
   raw token, inferred order channel or matching shop ID alone.
2. Establish tenant/store/app/credential-version-bound grant evidence from a verified
   authorization exchange and authorized-shop result; record its source, verification
   timestamp, granted scopes and expiry. Local arbitrary `meta`/future expiry is not proof.
3. Track revocation/disconnection and credential generation changes durably. Reject
   unknown/stale/expired/revoked/mismatched evidence; do not silently refresh credentials
   or fall back to another tenant/store. Evidence freshness policy requires an explicit
   decision before enforcement; no interval is invented here. The later per-operation
   decision below supersedes this open policy question, not the persistence/verifier gates.
4. Store/decrypt secrets only on the guarded server with row/field AAD and exact scope;
   no token/cipher/grant payload in browser responses, logs or documentation. A migration
   for trustworthy evidence and provider lifecycle semantics remains separately scoped.
5. Complete response contracts and mocked request/error/revocation/race tests first.
   Then request explicit approval for a named isolated-dev store and read-only actions.
   No production store, OAuth/revoke/refresh lifecycle or webhook change is authorized here.

Targeted synthetic adapter/ecommerce validation: **47 passed**, **3.22 s**. No frontend,
console route, DB schema or dependency changed in this request-correction slice.
Full lint passes; **749 passed**, 136 warnings, **2783.22 s**. Standalone ASGI/TCP
ack p95 **102.572/150.074 ms** pass unchanged **<200 ms** checks. Disposable-only reset
checks pass. Simultaneous TCP p95 **1184.752 ms** remains diagnostic evidence, not a
200 ms performance claim. Prior failures remain recorded; one pass is not a capacity
guarantee or proof of a latency cause. No retry/threshold change/new waiver.
Exact full lint/test/disposable-reset evidence is recorded in [progress](../PROGRESS.md).
No live request was sent; M6B remains incomplete.

#### M6B provider-native order snapshots (2026-10-17; offline continuation)

Added selected, immutable Pydantic contracts in
[`tiktok_models.py`](../../csa/agents/ecommerce/tiktok_models.py) and explicit
`get_order_snapshot` / `search_order_snapshot_page` adapter reads. These are internal
provider projections, not normalized `EcommerceOrder`, persisted sync records or console
response contracts. They retain order identity/status/payment currency and total, plus
distinct line `id`, `sku_id`, optional seller SKU, product name and `sale_price`.
Repeated SKU lines remain distinct and ordered; duplicate line IDs fail the whole read.
Decimal strings retain precision. No quantity, unit price, bundle/discount interpretation,
summation equality, currency default or legacy price alias is introduced.

Unknown provider fields are deliberately excluded, including buyer identifiers, addresses
and arbitrary payloads. Lines and pages use immutable tuples. Missing/malformed required
fields fail explicitly; the legacy normalizer continues rejecting documented lines without
quantity/`sku_sale_price`. Both read modes share signed request construction, exact detail
identity/cardinality, bounded search validation, continuation and safe error handling.
The existing adapter's `client_id` argument still does not enforce credential binding;
these methods are not wired to console, agents or credential resolution.

Synthetic native/legacy regression selection: **82 passed**, **3.48 s**.
Repository Ruff/format/Pyright/import-linter pass. Full synthetic suite:
**784 passed**, 136 warnings, **2757.17 s**, including disposable-only reset checks.
Standalone ASGI/TCP ack p95 **102.295/189.661 ms** pass unchanged **<200 ms**.
Simultaneous TCP ack/scheduled p95 **1256.362/1467.896 ms** remains diagnostic,
not sub-200 ms acceptance. Historical failures remain; no retry, threshold change
or capacity guarantee. Exact evidence is in [progress](../PROGRESS.md).
No frontend, DB schema or dependency changed.
No live seller call, account/credential inspection, persistent-dev reset, service
replacement, infrastructure/webhook or UI-guide change occurred.

Next: verify remaining quantity/bundle/price semantics before any normalized conversion.
Durable authorization/scopes/revocation/credential-generation evidence remains required
before a credential resolver or approved live
read. A typed snapshot is not proof of granted scopes, authorization or store connection.
The subsequent per-operation decision below resolves the freshness-policy question only.

#### M6B per-operation authorization evidence (2026-10-17; offline continuation)

**User decision:** require fresh authorization/shop verification for **each seller-read
operation**, rather than reuse evidence for a cached interval. Each detail read and each
search page is a new server-created operation. Verification must occur between that
operation's start and its evaluation time; no guessed TTL is added.

Added internal immutable Pydantic contracts and a pure metadata gate in
[`authorization.py`](../../csa/agents/ecommerce/authorization.py):

- Exact tenant/credential-record/platform/shop/app/credential-generation context.
- Trusted grant identity, revision, active/revoked/disconnected state, verified-exchange
  provenance label, immutable scope set and aware verification/expiry timestamps.
- Operation identity/start and verifier evidence bound to that operation, exact context,
  grant identity and lifecycle revision.
- Reject missing, mismatched, inactive, expired, stale-revision or invalid-time evidence.
  Require both documented order-read and authorized-shop scope alternatives; preserve
  the provider's exact `seller.fs.order&fufillment.management` spelling.
- Fixed safe rejection codes, no token/cipher fields, no fallback to another store or
  credential generation. Store metadata remains unverified and reads unavailable.

These contracts are **not a live authorization boundary by themselves**. Provenance labels
cannot authenticate arbitrary input. Only a future trusted server verifier may create
evidence; no browser evidence endpoint exists. The caller must load current durable state.
This function neither consumes an operation nor locks state through provider dispatch;
revision mismatch tests are not concurrency/race acceptance. Durable lifecycle persistence,
trusted verification and atomic dispatch/revocation handling remain separately scoped.
Do not wire the legacy adapter directly on the strength of this pure check.

Synthetic authorization/store/native/legacy tests: **121 passed**, **4.41 s**.
Repository lint, formatting, Pyright and import-linter pass. Full synthetic validation:
**819 passed / 1 TCP latency failure**, 136 warnings, **2740.30 s**.
TCP ack/scheduled p95 **643.091/853.983 ms** fails the unchanged **<200 ms**
ack check; ASGI **90.558/91.601 ms** passes. Both transports completed 150
requests with 150 durable deliveries. Disposable-only reset checks pass.
Full validation is **not green**; no retry, threshold change or new waiver.
See [progress](../PROGRESS.md) for exact results and diagnostic metrics.
No DB migration, credential lookup/decryption, OAuth/token refresh/revoke, live provider
request, service replacement, infrastructure/webhook change or UI-guide edit occurred.

Next: design durable evidence/lifecycle storage and trusted per-operation verification,
including revoke/disconnect/credential-rotation versus read-dispatch races. Any migration
application, real credential resolution or named dev-store verification still requires
separate approval. Quantity/bundle/discount normalization remains an independent gate.

#### M6B durable grant metadata (2026-10-17; offline continuation)

Added additive [0310](../../db/migrations/0310_ecommerce_grants.sql), internal
[`grants.py`](../../csa/agents/ecommerce/grants.py) and parameterized queries under
[`db/sql/ecommerce/`](../../db/sql/ecommerce/). The migration creates empty grant and
lifecycle-event tables; it does not backfill local credentials into verified authority.
It is tested on disposable databases only and **not applied to persistent dev0300**.

- Creation accepts trusted verifier output only, requires initial active/revision1
  and matches the exact tenant/record/TikTok/shop metadata without selecting token
  columns. No browser or agent entry point is added. Provenance strings remain labels,
  not authentication of caller-supplied evidence.
- One grant per tenant/credential record; duplicate creation fails explicitly. There
  is no active overwrite, reactivation, reauthorization or automatic replacement.
- Revocation/disconnection uses expected revision and active-state compare-and-swap.
  Concurrent terminal transitions have one winner; the loser receives a safe conflict.
  Database triggers enforce immutable evidence and monotonic terminal transitions,
  and append lifecycle events in the same transaction as creation/invalidation.
- Explicit grants revoke broad default table privileges first. Runtime may not edit
  evidence columns, delete grants or write/delete audit events. The narrowly scoped
  audit trigger uses a fixed search path and no public/runtime function execution.
  Tenant RLS is enabled, not FORCE; the read-only role remains SELECT-only.
- A credential deletion does not erase lifecycle history. There is intentionally no
  cascading credential FK. A loaded grant is historical metadata, **not** proof that
  the credential still exists or its generation/app/store binding remains current.

Synthetic grant/authorization/store/native/legacy/migration/reset selection:
**154 passed**, 1 warning, **70.14 s**. Covers durable reload through a new pool,
cross-tenant isolation, exact creation binding, stale/concurrent invalidation,
irreversibility, append-only audit privileges, rollback atomicity, read-only role,
and existing per-operation evidence denial after reloading revoked state.
Migration re-run and real `make db-reset` both pass against disposable containers.
Full lint, formatting, Pyright and import-linter pass. Initial full suite:
**828 passed / 4 failed**, 136 warnings, **1419.40 s**. The gateway still expected0300,
causing two health failures and both TCP checks to stop at readiness503 before any
latency measurement. Updated gateway expectation to0310; readiness/migration/grant
regressions **23 passed**, 1 warning, **54.29 s**. Corrected full suite:
**831 passed / 1 ASGI latency failure**, 136 warnings, **2219.18 s**; full lint and
disposable reset checks pass. Readiness failures are resolved. ASGI ack/scheduled
p95 **681.146/817.483 ms** fails the unchanged **<200 ms** ack limit; TCP
**145.722/150.077 ms** passes. Both completed 150 requests with 150 durable
deliveries. Full validation is **not green**; no further latency retry, threshold
change or waiver. Earlier failures remain evidence, not a proven laptop diagnosis.
The new gateway build must not be deployed to persistent dev0300 without separately
approved forward migration; it deliberately requires0310.
See [progress](../PROGRESS.md) for final full-suite evidence when available.

**Remaining gates:** current-credential generation ownership/linkage and rotation fencing,
trusted live verifier, per-operation persistence/consumption and atomic dispatch checks.
The grant repository does not hold a lock across a provider call or solve the
revocation-versus-network-dispatch race. Concurrent invalidation tests are not read-
dispatch race acceptance. Credential resolution/decryption, provider calls and console
wiring remain unavailable. No live authorization, token refresh/revoke, service restart,
persistent reset, infrastructure/webhook or UI-guide change occurred.

#### M6B credential/read fences (2026-10-17; offline continuation)

User explicitly approved **conservative fenced ordering and no automatic replay**:
an invalidation/rotation that wins the database fence prevents dispatch; a read that
wins may finish its bounded in-flight callback while invalidation/rotation waits.
This cannot recall a request already sent or guarantee provider-side exactly-once behavior.

Added additive [0320](../../db/migrations/0320_ecommerce_read_fences.sql) and internal
[`dispatch.py`](../../csa/agents/ecommerce/dispatch.py). No persistent migration is
applied; the new gateway's readiness expectation is0320, while approved persistent dev
remains0300. Do not deploy this build without a separately approved forward migration.

- Database-managed credential generation starts at1 without granting authority to old
  rows. Any material credential-row change (including token, metadata, shop or expiry)
  increments generation and disconnects an active grant atomically with its audit event.
  Timestamp-only updates are excluded; direct generation-only edits are rejected.
  Credential deletion disconnects grants and preserves lifecycle history.
  The trigger compares rows inside PostgreSQL; no token values are selected into Python,
  returned, logged, decrypted or copied into evidence/operations.
- Grant creation now requires an exact current credential generation. Existing terminal
  grants cannot be reactivated; reauthorization/reconnection remains separately scoped.
- Server-created operations durably bind identity, context, grant and a typed immutable
  detail/search request, including the specific order ID or search page token.
  Evidence for another operation/page is rejected; request fields cannot be rewritten
  through the runtime role. No operation/evidence API is exposed to the browser.
- Dispatch validates trusted verifier evidence while locking the credential then grant,
  commits single-use consumption **before** any send, then reacquires both locks and
  revalidates current state. A revocation in that commit gap prevents sending.
  The locks stay held through one callback; its entire dispatch budget is15 seconds,
  using the existing adapter's timeout scale, including pool/lock waits and validation.
- Crash/response-loss/cancellation after consumption cannot make the attempt reusable.
  No automatic replay, retry or success-shaped fallback exists. A new operation and
  fresh verification are required; a consumed row is not proof a provider received a
  request. A trusted callback must be read-only, single-send and cancellation-cooperative.
  This internal helper is not wired to the adapter, console, agent or live credentials.

Tests cover credential changes before dispatch, real row-lock exclusion during send,
concurrent dispatch one-winner, committed-attempt failure/cancellation, verification per
page, commit-gap revocation, callback deadline and fence release. These are synthetic
callbacks, not live-provider or process-kill acceptance. Final targeted read-stack/
authorization/lifecycle/readiness/migration/reset selection: **183 passed**, 1 warning,
**135.22 s**. Repository lint/format/Pyright/import-linter pass. Real disposable-only
`make db-reset` and migration idempotency pass. Final full lint/test:
**857 passed**, 136 warnings, **2823.83 s**, including disposable reset checks.
Standalone ASGI ack/scheduled p95 **156.515/165.059 ms** and TCP
**146.065/147.137 ms** pass unchanged **<200 ms** checks. Each completed
150 requests with 150 durable deliveries. Simultaneous TCP ack/scheduled p95
**1418.025/1485.486 ms** remains diagnostic, not sub-200 ms acceptance.
Exact evidence is in [progress](../PROGRESS.md). The previous
**831 passed / 1 ASGI latency failure** remains historical; one pass does not
establish stable capacity or resolve earlier causes. No threshold change or new waiver.

**Latency evidence inspection (no retry or threshold change):** the previous failed ASGI
run had ack/scheduled p95 **681.146/817.483 ms** and start-lag p95 **474.326 ms**;
the previous failed TCP run had **643.091/853.983 ms** and start-lag **447.883 ms**.
The earlier passing snapshot run's start-lag was **5.250 ms ASGI / 3.660 ms TCP**.
The load test measures start lag before entering `http.post`, so failed runs include
substantial request-launch scheduling delay, not just handler execution. Ack latency
also failed, and percentiles are not additive: these figures do not isolate a handler,
DB/pool, CPU or I/O root cause. ASGI shares the test event loop; TCP uses a child gateway.
Workers drain after standalone ingress; simultaneous-worker contention cannot by itself
explain those standalone failures. Existing aggregates lack per-stage/host-time-series
evidence. The latest full run passes standalone thresholds, but historical variability
and root-cause investigation remain open; do not claim stable capacity or laptop causality.

Next gates: trusted authorization/shop verifier, safe credential resolution and a
server-owned single-request adapter integration, reauthorization lifecycle, normalized
quantity/bundle/price semantics and separately approved named dev-store testing.

#### M6B fresh authorized-shop primitive (2026-10-18; offline continuation)

**Revised user decision:** per-operation verification means a fresh authorized-shop
match plus stored verified grant scopes; the actual order endpoint enforces current
order-read permission. This supersedes any interpretation that shop lookup proves fresh
order scopes or that a new authorization exchange/introspection is required before every
read. No official fresh-scope/introspection endpoint was verified; do not claim one exists
or does not exist based on search summaries.

The official [Get Authorized Shops v202309](https://partner.tiktokshop.com/docv2/page/get-authorized-shops-202309)
page initially rendered a navigation shell, then loaded after a reload. Direct inspection
confirmed GET `/authorization/202309/shops`, JSON content type, seller token in
`x-tts-access-token`, signed `app_key`/`timestamp` query parameters, and no `shop_cipher`
query parameter for this endpoint. Its response example confirms `data.shops` with `id`
and `cipher`. No account settings/testing tool, real credentials or seller API was used.

Added internal adapter `require_authorized_shop(shop_id)`:

- Require the requested ID to match the adapter's configured nonempty shop ID/cipher
  before HTTP; no fallback to another shop.
- Omit shop cipher from this endpoint before signing, retaining existing signed order
  request behavior unchanged. Token remains header-only, redirects disabled.
- Strictly validate the selected response fields, reject duplicate shop IDs, missing/
  malformed records and wrong cipher, and do not cache success. Empty shop lists deny.
- Keep ciphers in a secret-masked internal model and return no provider records.
  Fixed safe errors exclude provider payloads, signed URLs and credentials.

This is a **shop-match primitive, not the completed trusted verifier**. It does not
authenticate the adapter's tenant/app/credential generation, mint operation evidence,
prove fresh order scopes or integrate with fenced dispatch. Existing
`verified_authorization_and_authorized_shops` evidence source labels must not be minted
from this method; they predate the revised policy and are not a claim of fresh-scope proof.
The future orchestrator must distinguish historical grant scopes, fresh shop-match
provenance and actual order-call authorization outcome. Permission rejection must fail
explicitly, without retry/fallback; a successful shop lookup cannot bypass it.

Synthetic authorized-shop/native/legacy/evidence selection: **138 passed**, **3.49 s**.
Full lint/format/Pyright/import-linter pass. Full synthetic suite:
**877 passed**, 136 warnings, **1785.66 s**, including disposable-only reset checks.
Standalone ASGI ack/scheduled p95 **64.403/64.999 ms** and TCP
**149.340/152.742 ms** pass unchanged **<200 ms** checks; each completed 150
requests with 150 durable deliveries. Simultaneous TCP ack/scheduled p95
**450.294/470.276 ms** is diagnostic, not sub-200 ms acceptance. Historical failures
remain; no latency-only retry, threshold change, waiver or stable capacity claim.
See [progress](../PROGRESS.md). No schema/dependency/frontend
change or persistent migration/reset/rollout occurred. Gateway still requires0320 in
source; persistent dev remains approved0300.

Next: bind a trusted server-resolved adapter to exact operation/app/generation, define
truthful revised evidence provenance and integrate fresh shop lookup with fenced order
dispatch. Credential decryption, authorization lifecycle/reconnection, normalized pricing
semantics and named dev-store acceptance remain separate gates.

#### M6B bound-reader composition (2026-10-18; offline continuation)

Added server-only [`reader.py`](../../csa/agents/ecommerce/reader.py). A trusted injected
resolver receives the persisted operation context and returns a bound reader. The wrapper
checks the resolver's exact context and expected app key/configured shop, snapshots
credentials privately and exposes only shop verification and native detail/search reads.
It does not implement database credential lookup/decryption or establish a caller's trust.
The resolver must authenticate app-ID-to-app-key mapping and tenant/record/shop/generation;
`app_id` is not guessed to equal an API app key. No arbitrary browser resolver is accepted
through an API, and no production resolver is registered.

The existing dispatch helper now accepts a trusted asynchronous evidence factory as well
as the earlier internal evidence value. It validates current grant metadata/scopes under
credential-then-grant locks before invoking that factory. The coordinator resolves the
reader, checks its exact context, performs a fresh authorized-shop lookup, then stamps
truthful `fresh_authorized_shop_with_stored_grant_scopes` evidence for this operation and
grant revision. The legacy evidence source remains accepted for existing internal
contracts; it is not minted or reinterpreted by this coordinator.

After verification, consumption commits before a second locked lifecycle check and one
order call of the persisted target. The same private reader handles both HTTP calls.
The entire dispatch/resolution/verification/order budget remains15 seconds, with no
automatic retries, page walking, provider fallback, normalized price conversion or writes.
Shop failure happens before consumption; order rejection/response loss happens after
consumption and cannot replay. A successful shop call proves neither fresh order scopes
nor successful order access: the actual order endpoint must succeed and pass strict
native parsing. Resolver/client errors propagate; caller-owned HTTP clients must be
nonretrying, cancellation-cooperative and must not log signed requests/secrets.

Synthetic reader/dispatch/authorization/adapter/readiness/migration selection:
**184 passed**, 1 warning, **123.40 s**. Covers exact detail/search query target, fresh shop
before order, replay denial, wrong tenant context/configured app key/shop/cipher rejection,
revoked grant rejection before resolver/HTTP, and explicit order permission/timeout failure
without retry. Existing lifecycle row-lock/commit-gap/deadline tests also pass.
Full lint/format/Pyright/import-linter pass; final synthetic suite:
**887 passed**, 136 warnings, **1957.34 s**, including disposable reset checks.
Standalone ASGI ack/scheduled p95 **96.040/100.194 ms** and TCP
**140.022/140.805 ms** pass unchanged **<200 ms** checks. Each completed
150 requests with 150 durable deliveries. Simultaneous TCP ack/scheduled p95
**535.413/564.530 ms** remains diagnostic, not sub-200 ms acceptance. Historical
failures remain; no threshold change, new waiver or stable capacity/root-cause claim.
Exact evidence is in [progress](../PROGRESS.md).

No console/agent wiring, real credential lookup/decryption, live seller calls, schema/
dependency/frontend change, persistent migration/reset, restart or UI-guide edit occurred.
Gateway source still expects0320; persistent dev remains approved0300.
Next gates: authenticated server credential resolver with correct row/field AAD and trusted
app mapping, verified authorization exchange/reconnection lifecycle, provider-native price/
quantity semantics, and separately approved named dev-store read-only acceptance. The
injected resolver and synthetic successful exchange/grant fixtures are not live evidence.

**External eligibility gate:** user reports no registered company and inability to
publish the TikTok Shop Partner app. No market/account-specific own-shop/custom/
unpublished eligibility exception has been verified. Do not infer developer eligibility
from individual seller eligibility or general TikTok developer sandbox access, and do
not bypass TikTok verification. Offline CSA work can continue; live Shop API acceptance
and public/multi-seller distribution remain gated on confirmed eligible access.

#### M6B offline credential-resolver contract (2026-10-18)

Added [`resolver.py`](../../csa/agents/ecommerce/resolver.py): immutable secret-masked
Pydantic app/credential material contracts and a callable resolver for the existing
bound-reader coordinator. Every dependency is explicit: trusted app-registry loader,
exact-context credential loader, decryptor and caller-owned HTTP client. There is no
default DB query, environment/settings key lookup, credential loader or registered
runtime instance. This is an offline-tested composition contract, not permission to
inspect/decrypt existing credentials or use a real store.

- Reject missing/disabled/wrong app mapping before credential lookup. The trusted
  registry maps app identity to key/secret; legacy credential `meta` is not authority.
- Require exact tenant/record/platform/shop/app/generation context and aware unexpired
  token expiry before decryption. Check expiry again afterward. No token refresh,
  fallback, default shop/currency or cross-tenant credential search.
- Pin existing encryption format `v1:` and reuse repository `credential_aad` for
  `channel_credentials.access_token:<canonical-row-UUID>`. Do not alter existing
  ciphertext/AAD conventions or claim row AAD authenticates tenant/app by itself:
  trusted loaders and the current-generation database fence supply those checks.
- Convert crypto/authentication/Unicode failures into a fixed safe rejection, suppressing
  sensitive cause text. Unexpected storage/configuration errors propagate explicitly.
  Reject empty decrypted tokens; refresh tokens are neither requested nor decrypted.
- Shop cipher must come from trusted verified app/shop binding evidence. The contract
  deliberately does not invent a ciphertext column, accept arbitrary legacy metadata,
  or prescribe missing persistence. Actual trusted cipher/app registry storage remains
  a gate before real credential resolution.
- Loaders run inside existing dispatch fences and must not reacquire conflicting row
  locks through another connection. Private credentials are passed only to the bound
  reader, not returned in public response models. Caller-owned transport must not log
  signed requests or automatically retry.

Synthetic tests use generated AES-GCM envelopes with an explicit synthetic key and
injected decryptor, never repository settings or stored secrets. Cover exact binding,
expiry boundaries, wrong AAD/tampered ciphertext, empty/version failures, secret-masked
serialization, frozen contracts, explicit storage failure and mocked fenced native read.
Resolver/reader/dispatch/authorization/shop/readiness/migration selection:
**122 passed**, 1 warning, **141.49 s**, including real disposable-only `make db-reset`
and migration idempotency. Repository lint/format/Pyright/import-linter pass.
Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
**907 passed**, 136 warnings, **1617.69 s**, including disposable-only reset.
Standalone ASGI ack/scheduled p95 **64.075/64.510 ms**, TCP **128.870/130.786 ms**;
both pass unchanged `<200 ms` ack acceptance, each with 150 requests/150 durable deliveries.
Simultaneous TCP **2583.780/2639.902 ms** remains diagnostic, not latency acceptance;
graph-only p95 **100.422 ms**. Historical failures and host-capacity uncertainty remain.
See [progress](../PROGRESS.md) for results.

No DB schema/SQL/dependency/frontend change, real credential read/decryption, provider
request, persistent migration/reset, service change, console endpoint or UI-guide edit.
Persistent dev remains0300, gateway source requires0320. TikTok developer/app eligibility,
trusted app/cipher storage and loaders, verified exchange/reconnection lifecycle,
provider-native normalization and separately approved live acceptance remain open.

#### M6B trusted shop-binding storage (2026-10-18)

User selected external app secrets plus explicit database binding evidence. Added
[0330](../../db/migrations/0330_ecommerce_shop_bindings.sql) and
[storage.py](../../csa/agents/ecommerce/storage.py), with no default runtime registration
or real key/app-registry lookup. All execution uses synthetic disposable fixtures.

- Immutable tenant/grant-linked shop evidence inherits the grant's immutable exact
  tenant/record/platform/shop/app/generation and verified-exchange provenance. Only
  encrypted shop cipher is stored; no app secret or refresh token is copied.
  AAD is `ecommerce_shop_bindings.shop_cipher:<canonical grant UUID>`, using existing
  `credential_aad` and `v1:` format. Existing access-token AAD is unchanged.
- Trusted internal writer validates exact current active grant after locking credential
  then grant. No overwrite/delete/rebind, legacy backfill or public writer exists.
  It stores authenticated exchange output supplied by a trusted caller; the function
  does not prove that arbitrary inputs originated from TikTok. A crash between grant
  creation and binding insertion leaves a grant that the stored loader refuses to use.
- Evidence callback now receives the existing fenced transaction connection. The reader
  forwards it to its resolver; callbacks must not commit/close/release it or acquire
  conflicting locks elsewhere. The original injected resolver remains usable with its
  optional connection argument; the stored resolver queries that exact connection.
- External app mapping/enablement is checked before stored credential lookup.
  Exact tenant/record/shop/app/generation and active/unexpired grant/credential are joined
  in parameterized SQL. No refresh token, arbitrary `meta` or cross-tenant fallback.
  Shop cipher decryption is safe/fail-closed; access-token resolution reuses the tested
  binding/expiry/AAD contract. No material is exposed in console response models.
- External app-registry configuration is not protected by credential/grant row locks.
  Invalidate relevant grants before rotating/disabling external app material; automatic
  registry lifecycle enforcement and verified exchange/reconnection remain live gates.
- RLS stays staged (no FORCE), with explicit runtime SELECT/INSERT only and immutable
  UPDATE/DELETE guard; existing grant/audit history is retained.

Lint/format/Pyright/import boundaries pass. Targeted synthetic selection:
**103 passed / 1 test-fixture failure**, 1 warning, **140.87 s**; single-connection
fenced resolution, encrypted-at-rest/wrong-AAD/tamper, exact bindings, lifecycle denials
and disposable migration/reset pass. A foreign-tenant FK probe reused an existing
binding key and correctly hit uniqueness first; revised to use an unbound foreign grant.
Corrected focused RLS/FK/immutability/conflict test: **1 passed**, 1 warning, **14.41 s**.
Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
**932 passed / 1 TCP latency failure**, 136 warnings, **1656.08 s**. Storage,
reader/dispatch, disposable reset/migration and lint checks pass. Standalone ASGI
ack/scheduled p95 **78.420/81.942 ms** passes; TCP **259.251/260.143 ms** fails
unchanged **<200 ms** ack acceptance. Start-lag p95 **3.974/4.150 ms** does not
isolate the failure cause. Each transport completed 150 requests/150 durable deliveries.
Simultaneous TCP **1587.302/1922.243 ms**, graph-only **90.947 ms** are diagnostic.
Full validation is not green; no latency retry, threshold change, new waiver or
host/DB/handler root-cause claim.
Gateway source requires0330; persistent dev remains0300. No persistent migration/reset,
secret/key access, live HTTP, runtime registration, service/UI-guide/frontend change.
TikTok eligibility, real external registry/key integration, authenticated exchange,
reconnection and separately approved live acceptance remain open.

#### M6B global app-lifecycle fence (2026-10-18)

User explicitly made the preceding TCP latency failure non-blocking for development.
Keep the failed **259.251 ms** measurement and unchanged **<200 ms** threshold;
home-server causality is not established, and no production performance waiver is implied.
User selected an app-wide gate across tenants, not individually coordinated tenant gates.

Added [0340](../../db/migrations/0340_ecommerce_app_lifecycle.sql) and internal
[app lifecycle helpers](../../csa/agents/ecommerce/apps.py):

- Global tables contain only app ID, monotonic revision, state and transition timestamps.
  They intentionally have no tenant RLS: runtime/analytics may read non-secret global
  metadata, not secrets or tenant grants. Runtime has no app/audit DML privileges.
  The narrow SECURITY DEFINER lock function has pinned search path, no secret access,
  and only runtime EXECUTE. Owner/operator changes use a caller-supplied connection;
  there is no owner DSN lookup, public endpoint, background rollout or default registry.
- Register only disabled revision1. Database guards permit only alternating active/
  disabled transitions with revision+1 and unchanged app ID; no deletion, reset, same-state
  rotation or active insertion. CAS avoids lost updates; audit writes commit atomically.
- Grants persist exact `app_revision`; old grants and old operation contexts map to
  reserved revision1, which cannot be activated. No automatic authorization/backfill.
  Activation uses the next revision, so disabling then reactivating never restores old
  grants. Reauthorization/reconnection remains separately unimplemented.
- Shared app lock precedes credential then grant locks. It is held through verification
  and reacquired after durable consumption through the bounded order callback.
  Read winner may finish before disable commits. Disable winner rejects before resolver/
  HTTP. Disable in the commit gap prevents send and does not restore the consumed attempt.
  Grant creation, binding creation and stored resolution use the same app gate.
- External app material explicitly reports revision as well as app ID/enablement.
  Mismatch fails before credential loading/decryption. Secret material remains external,
  not written to these tables or audit.
- Operator protocol is **disable and commit**, then stage immutable external material
  for the next revision, then activate and obtain new verified grants. A crash before
  activation leaves reads disabled. Do not remove/replace a snapshot used by an active
  revision. Out-of-band changes to external systems are not magically fenced by SQL;
  a compliant registry/secret-store integration remains a live gate.

Lint/format/Pyright/import boundaries pass. Targeted lifecycle/authorization/grant/
dispatch/reader/resolver/storage/readiness/migration selection: **150 passed**,
1 warning, **205.31 s**. Covers real lock wait ordering, cross-tenant disable,
commit-gap denial/no replay, crash/rollback, privilege guards, external revision mismatch
and disposable reset/migration. Expanded lifecycle selection: **9 passed**, 1 warning,
**16.56 s**, adding concurrent CAS and shared-read/cancellation lock-release coverage.
Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
**945 passed**, 136 warnings, **1601.30 s**, including full lint and disposable-only
reset/migration. Standalone ASGI ack/scheduled p95 **53.556/56.647 ms**, TCP
**136.871/137.385 ms**, pass unchanged **<200 ms** ack acceptance. Each transport
completed 150 requests with 150 durable deliveries. Simultaneous TCP
**1242.929/1462.160 ms** and graph-only **84.332 ms** remain diagnostic, not sub-200 ms
simultaneous acceptance. Earlier failures and the development-only non-blocking decision
remain recorded; no latency-only retry or host-capacity/root-cause claim.
Gateway source requires0340; persistent dev stays0300. No existing secret/key access,
provider request, persistent migration/reset, service/frontend/UI-guide change.
TikTok eligibility, authenticated exchange/reconnection, external registry/key integration
and separately approved live acceptance remain open.

#### M6B offline authorization attempts and reconnection (2026-10-18)

User has completed Partner Center account registration but cannot complete the remaining
review/publishing/authorization steps. Live TikTok authorization is externally blocked;
no own-shop/custom-app exemption or pre-review Sandbox access has been verified.
Offline contracts and synthetic lifecycle development may continue.
User selected **preserve existing connection until successful completion** and
**consume before exchange; no automatic replay after uncertain results**.

Added [0350](../../db/migrations/0350_ecommerce_authorization_attempts.sql) and internal
[connection choreography](../../csa/agents/ecommerce/connections.py):

- Server-selected existing local-store context only. Attempt creation checks active
  app revision and exact current credential generation; it does not create arbitrary
  stores or prove user authority. Future staff endpoints must enforce session/RBAC/
  CSRF and bind the browser flow; none is introduced here.
- Opaque random256-bit state returned once as SecretStr; store SHA-256 digest only.
  Ten-minute database expiry, immutable tenant/store/app/generation binding, terminal
  cancellation, one-time consume and completed grant reference. No code/token/provider
  payload in attempt storage. Staged tenant RLS/no FORCE and narrow UPDATE columns.
- Callback coordinator requires explicit tenant, attempt ID, state and secret-masked
  code; missing/foreign/wrong-state/cancelled/expired attempts cannot invoke exchange.
  Consume commits before one injected trusted verifier call. Then app/credential fences
  are reacquired and held through callback and persistence within a15s total deadline.
  Failure, crash, timeout or cancellation after consumption cannot replay; new attempt
  and fresh authorization are required. Cancellation after consumption cannot recall it.
- No real provider exchange parser/client, authorization URL, callback endpoint,
  app/key source or runtime registration is provided. Verifier must authenticate the
  actual exact app/revision/shop and verified scopes, honor cancellation, avoid retry/
  sensitive logging and raise safe errors. A typed synthetic result is not provider
  authentication, nor can a verifier establish binding by copying requested context.
- Completion checks exact result context, verification time, access/grant expiry and
  order/shop scopes, and atomically replaces encrypted access/optional refresh tokens
  using existing row/field AAD. No refresh implementation or guessed expiry defaults.
  Absent refresh token clears any prior refresh token; no reuse of stale credentials.
- Credential trigger increments generation and disconnects active prior grant/audit;
  new grant/audit and encrypted shop-binding evidence are inserted on the same transaction,
  along with completed attempt. Failure anywhere rolls all those changes back while
  keeping the attempt durably consumed. Old ciphertext/metadata is not selected by
  completion; arbitrary legacy `meta` is neither interpreted nor rewritten.
- Replace the old single-grant-per-credential uniqueness with one **active** grant per
  tenant/credential. Retain terminal grants, events, shop evidence and operation history.
  Old grants remain terminal; old generation operations remain denied. No deletion,
  reactivation or provenance overwrite. Concurrent reconnects of the same generation
  have at most one successful credential rotation.
- Reused grant/shop writers now expose connection-owned insertion for atomic completion.
  Grant creation locks app then exact credential generation to avoid insertion racing
  rotation. Pending/failed reconnects preserve the current connection, while brief
  exchange fencing may delay concurrent reads/rotation under the bounded policy.

Lint/format/Pyright/import boundaries pass. Synthetic connection/lifecycle/grant/storage/
reader/dispatch/readiness/migration selection: **116 passed**, 1 warning, **228.66 s**,
including disposable reset and migration idempotency. Expanded connection/grant selection:
**39 passed**, 1 warning, **46.64 s**, including commit-gap changes, concurrent reconnects,
rollback at access/refresh/shop crypto stages, cancellation ordering and revoked-connection
reauthorization. Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
**971 passed / 1 TCP latency failure**, 136 warnings, **1772.52 s**. All connection/
grant/fence tests, disposable reset/idempotency and full lint/types/import boundaries pass.
ASGI ack/scheduled p95 **79.155/80.843 ms** passes; TCP **213.232/213.527 ms** fails
unchanged **<200 ms** ack acceptance. Each transport completed 150 requests/150 durable
deliveries. Simultaneous TCP **2403.971/2513.879 ms**, graph-only **90.017 ms** are
diagnostic. Full validation is not green; latency remains non-blocking for development
by user decision, not production acceptance or proof of home-server causality.
No latency retry or threshold change.

Gateway source requires0350; persistent dev remains0300. No real code/token/key lookup,
provider HTTP, persistent migration/reset, webhook, service, frontend or UI-guide change.
Live eligibility, official exchange-contract verification, trusted real verifier/external
registry integration and separately approved end-to-end seller acceptance remain open.

#### M6B staff connection status (2026-10-18)

The staff Business workspace now observes the offline lifecycle through existing store
list/detail/exact-binding reads. No authorization endpoint or live action is enabled.

- Add `connection:{status,live_integration:"unavailable"}` to the public StoreRecord.
  Retain constant authorization unverified, permissions unknown and provider reads
  unavailable. `authorization_recorded` means only local metadata matches, never a
  connected badge, fresh provider permission, token validity or a dispatch authorization.
- One bounded tenant/record batch query selects status only, reusing exact store scope.
  Latest grant by generation/verification time/ID prevents old terminal history from
  obscuring newer authorization. States cover unsupported/missing-shop, unverified,
  revoked/disconnected, expiry, missing/disabled/revised app, missing binding/permissions
  and authorization recorded. Stored scopes are tested in SQL without returning values;
  binding existence checks never select cipher content. No token presence, secret
  decryption, `meta`, provider data or attempt secrets are read or exposed.
- Existing staff/admin access and tenant selection apply; readers/machines/anonymous
  remain denied. Store mutations, Connect/Reconnect and callbacks remain absent.
  UI disabled controls have no handlers and explain the app/seller eligibility gate.
  Account registration is not treated as review/publishing or Sandbox eligibility.
  No synthetic verifier or legacy OAuth/webhook path is reachable from these reads.
- Existing pagination/filtering/explicit refresh and tenant-change cancellation remain.
  This is a staleable advisory read, not locks held through later actions. Future real
  provider dispatch must always use the separate existing fences and fresh verification.

Initial targeted run found an omitted lateral-query tenant alias and duplicate synthetic
shop fixture IDs; fixed both. Final console/store/contract/readiness/migration selection:
**30 passed**, 1 warning, **216.14 s**, including disposable-only real reset/idempotency.
Lifecycle matrix covers all three permitted staff roles, exact list/detail/binding
responses, missing/revised/disabled app, unknown/future/expired evidence, FS scope,
retained history, no secret exposure and forbidden mutation/provider/crypto invocation.
Backend lint/types/import boundaries pass; generated console OpenAPI snapshot updated.
Isolated frontend **16 transport + 2 state tests**, lint, typegen/typecheck, production
build and safe trace checks pass without copying/loading environment files.
Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`: **975 passed**,
136 warnings, **1989.63 s**, including full lint and disposable-only reset/migration.
Standalone ASGI ack/scheduled p95 **53.206/53.613 ms**, TCP **178.197/178.696 ms** pass
unchanged **<200 ms** ack acceptance; each transport completed 150 requests with 150
durable deliveries. Simultaneous TCP **809.617/839.308 ms**, graph-only **87.540 ms**
remain diagnostic, not sub-200 ms simultaneous acceptance. Historical failures and
development-only non-blocking decision remain; no latency-only retry or stable capacity/
root-cause claim. No live browser/service acceptance or rollout is claimed.

No schema/dependency change; gateway source still requires0350, persistent dev0300.
Attached environment snapshots were not opened. No real secret/key/provider access,
persistent migration/reset, service/webhook or UI-guide change. TikTok eligibility,
verified real exchange/registry integration and live acceptance remain open.

#### M6B public token-exchange contract verification (2026-10-18)

Read the rendered official public
[Authorization overview](https://partner.tiktokshop.com/docv2/page/authorization-overview-202407),
specifically Get Access Token API and the user-type table. The Get Access Token link
from the already verified
[Get Authorized Shops](https://partner.tiktokshop.com/docv2/page/get-authorized-shops-202309)
reference originally points to
`/docv2/page/678e3a3292b0f40314a92d75#Get Access Token API`, which redirected to
that overview. This is public documentation verification, not a live exchange test
or evidence of this account's app eligibility.

Verified request/response semantics:

- HTTPS GET `https://auth.tiktok-shops.com/api/v2/token/get`, with query parameters
  `app_key`, `app_secret`, `auth_code` and `grant_type=authorized_code`.
  The literal is `authorized_code`, not standard OAuth `authorization_code`.
  No order-request signing parameters or shop cipher are documented for this request.
- Successful seller redirect supplies `code` and `state`; the code is sent to the
  token API as `auth_code`. Denial is documented as `code=null&error=auth_denied`.
  A code is single-use and expires after30 minutes. CSA's existing ten-minute attempt
  expiry is a deliberately shorter local bound, not the provider code lifetime.
- Success envelope has integer `code=0`, `data`, and diagnostic `message`/`request_id`.
  Selected data fields include `access_token`, `refresh_token`,
  `access_token_expire_in`, `refresh_token_expire_in`, `user_type` and
  `granted_scopes` (array of scope-key strings).
- Both `*_expire_in` fields are **absolute Unix expiration timestamps**, not durations
  to add to the current time. A future parser must require explicit valid timestamps,
  convert them to aware UTC datetimes, reject expired/malformed values and avoid
  invented seven-day/thirty-day defaults. Existing offline completion already accepts
  explicit datetimes; no runtime behavior changes in this verification slice.
- `open_id` identifies the authorizing user, not an exact shop. Seller name/region
  cannot substitute for shop identity. This token response alone neither yields a
  verified intended shop/cipher nor echoes CSA's app revision or tenant context.
  Exact app registry mapping plus fresh authorized-shop evidence remain necessary;
  copying a requested context into a token result is not provenance verification.
- The table distinguishes standard seller0, creator1, partner2/3 and Global Selling
  seller4/5. Existing verified shop-reference evidence specifically describes seller
  token type0. Do not silently treat every listed type as supported by CSA; broader
  market/user-type handling needs its own verified request coverage and explicit scope.
- `granted_scopes` comes from exchange data, not arbitrary legacy credential metadata.
  Validate actual scope keys without spelling correction/inference. Missing/malformed
  scope data must fail closed before usable grant creation; fresh shop matching is
  still not token introspection or proof of later order permission.

Implementation prerequisites and deliberate limits:

- App secret and code occur in the documented URL query. Standard HTTP request
  logging, redirects, telemetry, hooks and exception strings can therefore carry
  secrets. Before any exchange transport, provide synthetic tests proving no raw or
  encoded secret/code/token leaks through HTTPX/httpcore/stdlib/structured logs or
  raised errors under success, rejection, malformed response and network failure.
  Do not assume SecretStr or suppressing an exception cause also sanitizes HTTP logs.
- Transport must be explicit, bounded, non-redirecting and single-attempt with no
  automatic code replay, caller cancellation honored, fixed safe failure codes and
  no provider-message/body/URL echo. No query-string request may reach live networking
  during offline tests.
- Legacy [`oauth.py`](../../csa/channels/marketplace/oauth.py) is not reused: its
  duration interpretation/defaults, inferred shop identity, error/log text and
  webhook-producing saver do not satisfy the new contract. It remains unchanged
  and is not wired to the Business workspace.
- Next bounded implementation: strict secret-masked selected token-response parser
  with synthetic fixtures, including Unix-time (not duration) semantics, required
  scopes, unexpected user types, malformed/duplicate scopes and safe errors.
  Transport/logging isolation and real verifier composition follow separately.
  No refresh operation, callback route, browser authorization flow or live registration
  is completed by these documentation observations.

Documentation-only change: no new test run required; latest completed implementation
validation remains975 passed, with prior latency history retained. No environment
snapshot, existing key/credential, account login, API Testing Tool, actual token/order
request, persistent migration/reset, service/webhook/frontend/UI-guide change.
Gateway source remains0350; persistent dev0300. Partner onboarding/live eligibility,
trusted external registry/key integration and approved live acceptance remain gates.

#### M6B selected token-response parser (2026-10-18)

Added [token_response.py](../../csa/agents/ecommerce/token_response.py) and
[synthetic parser tests](../../tests/test_tiktok_token_response.py), without transport
or live verifier composition. This implements only the selected response portion of
the verified public token-exchange contract above.

- Require integer envelope code, with nonzero rejection before reading data; malformed
  success data fails explicitly. No provider message/body/request ID is echoed or logged.
  Validation causes are suppressed and returned errors carry fixed safe codes.
- Require nonempty SecretStr access/refresh tokens, strict standard seller user_type0,
  explicit strict bounded integer expiration seconds and actual scope arrays. Creator,
  partner and Global Selling types are not silently accepted merely because they exist
  in the provider enumeration. Missing refresh fields are rejected by this provider
  parser even though the generic internal completion contract permits other verifier
  implementations to return no refresh token.
- Interpret both `*_expire_in` values as absolute Unix seconds, converting to aware UTC
  datetimes and rejecting expired/equal-to-now values. Reject duration-sized historical
  values, booleans/floats/strings/out-of-range timestamps, with no guessed defaults.
  No undocumented ordering between access and refresh expiry is imposed.
- Scopes are nonempty exact strings with no duplicate keys or surrounding whitespace;
  unknown additional keys are preserved but cannot substitute for required order/shop
  scopes. Reuse a shared pure scope check extracted from the existing authorization
  gate, retaining its exact spelling and behavior rather than duplicating inference.
- Frozen selected output contains only masked access/refresh tokens, access/refresh
  expiration datetimes and immutable scope keys. Discard user identity, seller name,
  region, diagnostic and arbitrary fields. No shop ID/cipher/app/tenant is derived;
  output is not VerifiedAuthorizationResult and never creates a grant itself.
- Parser emits no logs, reads no configuration/storage, accepts no default clock and
  performs no networking. Its safe errors do not make a future HTTP transport safe:
  query-secret logging/telemetry/hook/redirect/error isolation remains required.

Lint/format/Pyright/import boundaries pass; parser/shared-authorization selection:
**103 passed**, **0.81 s**. Synthetic coverage includes strict primitive/container types,
missing fields, absolute expiry/boundaries, timezone-equivalent clocks, unsupported user
types, exact FS scope, scope duplicates/inference rejection, safe errors and masked frozen
output. Full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test`:
**1038 passed / 2 failures**, 136 warnings, **2259.43 s**. TCP ack/scheduled p95
**218.063/219.480 ms** fails unchanged **<200 ms** ack limit; ASGI **94.966/98.602 ms**
passes. Each transport completed150 requests/150 durable deliveries. Simultaneous
**514.039/538.389 ms**, graph-only **90.942 ms** are diagnostic.
The second failure was the repository's broad hard-coded-ID guard matching the numeric
timestamp maximum. Derive that maximum from Python's maximum UTC datetime instead,
preserving the bound and guard behavior; add a maximum-supported-second boundary test.
Corrected parser/policy/identifier-guard selection: **105 passed**, **1.53 s**;
full lint/types/import boundaries pass. The subsequent full suite confirms the timestamp
guard now passes: **1052 passed / 1 TCP latency failure**, 136 warnings, **2693.14 s**.
TCP ack/scheduled p95 **553.594/554.196 ms** fails unchanged **<200 ms**; ASGI
**96.235/96.988 ms** passes. Each transport completed150 requests/durable deliveries.
This is development-only non-blocking by user decision, not a green suite, threshold
change, production acceptance or proven home-server cause. No latency-only retry.
No schema/dependency/frontend change, real secret/key/environment snapshot access,
provider request, callback/runtime registration, persistent migration/reset,
service/webhook/UI-guide edit. Gateway source0350, persistent dev0300 remain unchanged.

#### M6B isolated token-exchange transport (2026-10-18)

Added [token_exchange.py](../../csa/agents/ecommerce/token_exchange.py) and
[synthetic transport tests](../../tests/test_tiktok_token_exchange.py). This module is
not registered with the authorization-attempt completion flow and does not verify
app identity, exact shop/cipher, or trusted provenance.

- Fixed HTTPS endpoint and documented GET query fields only. App key, app secret and
  one-use code are unwrapped only to construct the request; there is no caller-owned
  `AsyncClient`, event-hook layer, client instrumentation, or application logging path.
  The operation uses `AsyncHTTPTransport` directly, with `trust_env=False`, TLS
  verification enabled, zero retries, no redirects, and no proxy/environment lookup.
- Apply one 15-second timeout to the exchange, honor task cancellation, and make one
  transport call. Non-200 status, provider rejection, malformed response, network
  failure, timeout and oversized response become fixed authorization codes; no URL,
  code, secret, token, provider diagnostic/body or raw exception is returned/logged.
- Require identity encoding; stream raw bytes in bounded chunks and cap the body at
  64 KiB before JSON parsing. Parse via the strict selected token-response parser.
- Synthetic tests cover documented query construction, selected success output,
  single-attempt HTTP rejection/redirect, provider rejection, malformed JSON, network
  exceptions containing the request URL, bounded response, timeout, cancellation,
  empty preflight input and absence of raw/percent-encoded secrets or tokens from
  captured Python logs and raised errors. Requests are intercepted by `MockTransport`;
  no provider network call is made.

Transport/parser/reconnect focused selection: **105 passed**, **1 deprecation warning**,
**41.69 s**. Full repository lint/format/Pyright/import boundaries pass (**286 files
formatted**, Pyright0 errors). The complete `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s
make test` run via disposable testcontainers finished **1052 passed / 1 failed**,
136 warnings, **2693.14 s**. Sole failure is the TCP ingress latency assertion at
**553.594 ms** vs unchanged **<200 ms**; ASGI **96.235 ms** passes. The timestamp guard
false positive is now cleared. User's development-only latency decision remains; this
is not a green suite or production acceptance, no cause is claimed and no latency-only
retry/threshold change was made.
Later complete suite after the exchange transport: **1065 passed**, 136 warnings,
**3001.57 s**. Standalone ASGI ack/scheduled p95 **74.585/76.926 ms** and TCP
**174.885/175.282 ms** pass unchanged **<200 ms** ack acceptance; each completed150
requests/150 durable deliveries. Simultaneous TCP **506.924/537.490 ms** and graph-only
**105.615 ms** are diagnostic. The run preceded the final narrow post-shop refresh-expiry
check, which is covered in the current focused verifier selection. One passing full run
does not resolve or erase the historical latency failure or establish stable capacity.
No dependency/schema/frontend change, provider request, callback/runtime registration,
persistent migration/reset, service/webhook/UI-guide change; gateway source0350 and
persistent dev0300 remain unchanged. Next: compose the parsed exchange with an exact
app-revision and fresh shop verifier. Partner eligibility and separately approved live
acceptance remain gated.

#### M6B trusted app/shop verifier composition (2026-10-18)

Added [authorization_verifier.py](../../csa/agents/ecommerce/authorization_verifier.py)
to compose the strict exchange parser/transport with the existing authorized-shops
primitive. It has the verifier signature accepted by `complete_authorization`; when
passed there, the existing attempt/app locks remain held. No callback route or runtime
registration invokes it.

- Resolve app material only through an injected trusted server loader keyed by the
  context's app ID. Fail before exchange unless its exact app ID, enabled state and
  immutable revision match. This contract assumes the loader returns the immutable
  revision-specific snapshot; no real app-secret registry/key source exists.
- Exchange once using that exact app key/secret and code. Then issue one uncached
  authorized-shops request with the returned access token and app signature. Select
  only the exact server-selected `context.binding.shop_id`; reject absent, duplicate,
  malformed, provider-rejected, redirected, HTTP-error and network-error outcomes.
- For first authorization, there is no already stored expected cipher to compare.
  `TikTokShopAdapter.authorized_shop_cipher` therefore returns a SecretStr only after
  validating a unique exact ID match. The existing `require_authorized_shop` path still
  requires and compares a previously stored cipher for per-operation reads.
- The verifier creates `VerifiedAuthorizationResult` with unchanged trusted context,
  selected masked tokens/scopes/expiry, exact fresh cipher and a timestamp after the
  shop lookup; it rechecks access and refresh expiry after that lookup. Existing completion continues
  to consume before exchange and atomically persist encrypted token, grant and binding.
- Its authorized-shop call uses a dedicated raw, TLS-verified `AsyncHTTPTransport`
  (`trust_env=False`, retries0), never follows redirects, applies the 15-second bounded
  flow and caps the response at256 KiB. It bypasses AsyncClient hooks/request-URL logs
  for signed requests. Errors are mapped to fixed safe authorization codes.

Synthetic verifier/adapter/parser/exchange/reader/resolver/connection selection:
**173 passed**, 1 testcontainers deprecation warning, **76.18 s**. Tests check app
revision/state before exchange, intended-shop selection amid other shops, multiple-shop
denial, malformed/provider/HTTP/redirect/network rejection, expiry during shop lookup,
secret-safe errors, existing binding-match behavior and connected lifecycle/read tests.
Full lint/format/Pyright/import boundaries pass (**288 files formatted**, Pyright0 errors).
Full suite result **1065 passed** is recorded above; it started before the final refresh
expiry boundary was added, which the current focused tests cover. No provider request,
actual registry,
callback route/runtime registration, completion-flow hookup, persistent migration/reset,
environment/secret access, service/webhook/UI-guide change. The function is ready only
as an injected verifier in synthetic flows; the external registry and approved live
eligibility/acceptance gates remain open.

The follow-on end-to-end composition test passes the concrete verifier to
`complete_authorization` and uses separate `httpx.MockTransport` handlers for token and
authorized-shop requests against a disposable testcontainers database. A query at token
request time proves the attempt is already consumed. The success path verifies the exact
trusted app ID/revision, intended shop among other shops, encrypted token and shop-cipher
values under canonical AAD, and persisted grant/binding. A second attempt with no exact
shop match preserves the prior connection, leaves the attempt consumed, and cannot replay
or emit a second exchange; safe error/log assertions exclude synthetic provider detail
and credentials. A third, synthetic binding-encryption failure after credential/grant
writes start proves the entire token/grant/shop-binding transaction rolls back without
creating partial rows and burns the attempt. Focused connection/verifier/transport/shop/parser
selection: **139 passed**, 1 testcontainers deprecation warning, **35.18 s**. `NO_LOCAL_ENV=1
ENV=ci make lint` passes (Ruff, 289 files formatted, Pyright0 errors, import-linter
kept). No full suite rerun was warranted for this test-only change; the earlier 1065-pass
suite and historical latency failures remain as recorded. No production implementation
change was necessary.

Next gate is external: confirm seller/app eligibility and authorization access. Until
that is confirmed and separately approved, do not add a real app-material registry,
callback route, runtime registration or provider/live request. The next technical scope,
after that gate, is to define and review the immutable revisioned app-material/key source
and its lifecycle before any callback wiring.

Exit: every visible business action has a real backend or an explicit unavailable state;
no stub/fake-provider success in operational UI. Record deferred provider/platform operations
by stage rather than treating a read-only foundation as a complete ecommerce ERP.

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

## 9. Definition of done and current handoff

- [ ] M0–M7 evidence complete; frontend uses CSAV2 exclusively for active CSA features.
- [ ] No active legacy API/n8n/direct-domain-SQL path remains in adopted console code.
- [ ] Platform subscriptions, tenant switches and specialised-tab readiness are truthful.
- [ ] Authenticated KB/chat/approval flows work end to end with synthetic fixtures.
- [ ] Provider-dependent and deferred features stay explicitly unavailable.
- [ ] All deployment actions remain separately approved; runtime cutover is not implied.

The obsolete M2/M3 kickoff prompt has been removed. Use
[M5 current scope and evidence](./2026-10-17-m5-telegram-inbox.md).
The recovery handoff, phase plans and earlier implementation evidence remain reference
material; they do not authorize resets, service replacements or new channel authority.
