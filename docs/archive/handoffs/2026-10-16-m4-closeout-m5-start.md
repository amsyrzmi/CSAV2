# M4 closeout and M5 kickoff

M4 closeout recorded 2026-10-16. Historical M5 kickoff; continue from
[the implemented Telegram M5 slice and current evidence](2026-10-17-m5-telegram-inbox.md).
Isolated dev only.

## Closeout decision

The user explicitly selected **Close functional M4 with documented laptop-dev
performance exception; carry latency work forward**. M4's narrowed read-only
Sandbox assistant scope is functionally closed. This is not a green full-suite result,
a performance fix, production readiness, or permission to widen sandbox authority.

The user reports that the server is a laptop. Resource contention may contribute to
latency variability, but no measured cause is established. The exception defers
performance acceptance for local development; the original `<200 ms` checks remain
unchanged and must be revisited before release/public deployment (M7/M8).

## Read first

1. [Repository guidance](../../../AGENTS.md).
2. [Current evidence](../../PROGRESS.md), especially M4 and its latency investigation.
3. [Console API](../../CONSOLE_API.md) and
   [source migration plan, M5](2026-10-15-console-source-migration-plan.md#m5--staff-conversation-inbox-and-approvals).
4. [LG15 console](../../langgraph-rebuild/LG15_console_turnover.md),
   [LG07 approvals/outbox](../../langgraph-rebuild/LG07_egress_outbox_hitl.md) and
   [frontend guide](../../../CSA_WebConsole/csa-console/web/README.md).
5. [Runtime recovery handoff](2026-10-16-runtime-stabilization.md) for incident history,
   reset guards and synthetic fixture provenance. Its older startup instructions are
   historical; this handoff is the current entry point.

## M4 delivered and verified

- Authenticated `/chat` and `/portal/chat`; dedicated account/tenant sandbox contact,
  exact-owner reads, CSRF/role/tenant checks, bounded redacted text and durable ingress.
- Migration **0290** forward-applied to persistent isolated dev with explicit approval,
  no reset. Staged RLS, no FORCE; internal disabled/token-free web routing metadata.
- Runner permits only KB query and product search/detail. No customer-record lookup,
  orders/invoices/payments/booking/ticket/email/marketplace mutations, staff escalation,
  callbacks, confirmations, voice or arbitrary attachments.
- Local web delivery validates participant/trace bindings and atomically records
  availability and delivery attempt without external calls/Telegram token decryption.
  Private held drafts remain held; no staff notification or automatic expiry release.
- Stable history/pagination/polling, explicit same-key retry, timeout versus delayed-turn
  states, reload/reconnect recovery and audited archive/hide rather than deletion.
- Acceptance found an empty `billing_invoice` / `ask_clarifying` plan asking for invoice
  details without calling an agent. The live fix returns the fixed scope refusal for
  fact-free account-domain plans/confirmations, retaining grounded general KB answers.
- User-operated and DB-corroborated fresh invoice refusal, seventeen-day web KB answer
  and Telegram reply pass. Archive, refresh/reopen and tab offline/online recovery pass.
- Agent-operated admin view confirms retained refusal/KB replies. With separate approval,
  the user privately registered Synthetic M4 Reader (`m4-reader-20261016@example.test`).
  Reader creation disabled; send/archive absent; other-owner active/archived histories
  404; cross-tenant access 403; own list 200/empty. No credentials/tokens/cookies inspected.
  Reading that new reader's own pre-existing history remains automated-test evidence
  only: the fixture has no history. M3 KB tenant-role replay is a separate remaining check.

### Validation, without hiding failures

| Evidence | Result |
|---|---|
| Latest full `NO_LOCAL_ENV=1 ENV=ci PYTEST_ADDOPTS=-s make lint test` | **674 passed / 2 failed**, 134 warnings, 2113.83 s |
| Failed full-run 50-rps acknowledgement p95 | ASGI **508.717 ms**; TCP **298.288 ms**, unchanged `<200 ms` |
| Final uninstrumented reader-backend + ASGI/TCP selection | **2 passed / 1 failed**; ASGI **93.234 ms** passes, TCP **261.451 ms** fails |
| Lint | Ruff, formatting, Pyright, import-linter pass |
| Reset/migrations | Actual `make db-reset` passes **only on disposable, fingerprint-verified test DB**, through 0290 |
| Sandbox-focused tests | **11 passed**; strengthened provider-noninvocation test passes separately (overlap) |
| Frontend isolated validation | **11 transport/routing/cancellation + 2 state/deadline tests**, lint/types/production build pass |

Both transports complete 150 turns with 150 pending deliveries in the failed benchmarks.
Instrumented standalone passes do not supersede failures. Passing TCP profile had pool
wait p95 0.113 ms and loop lag 6.477 ms; it did not capture the failing path. Do not infer
a pool/SQL bottleneck or laptop causality from those passing samples. Temporary profiler
source/output/cache files were removed. Detailed metrics/traces remain in progress.

No application-code changes followed the last full run; later changes are documentation
and the explicitly approved synthetic reader registration/browser checks. No commit made.

## Current environment and immutable safety boundaries

- Repository `/home/amsyrzmi/CSAV2`, branch `main`, extensive dirty tracked/untracked work.
  Preserve it; no reset/clean/stash, unsolicited commits or overwrite of others' changes.
  The untracked `docs/reviews/` reports are user-owned context, not implemented fixes.
- Dev: `csa_dev_db`, Compose project `csa-dev`, **127.0.0.1:5434/csa**;
  roles `csa_app`, `csa_identity`, owner `csa`. **Never connect production port 5433**.
- Never read/print environment files, secret snapshots, passwords, tokens, encrypted
  credentials or cookies. Settings loaders may consume config internally after exact
  host/port/database/role guards; reject `hostaddr` and service overrides.
- Never reset persistent dev or reconcile/purge old uploads. Dedicated upload root is
  `$HOME/.local/share/csav2-dev/kb-uploads`; leave it and `.env.local` untouched.
- No production/n8n/infrastructure/Caddy/Cloudflare/hostnames/Telegram webhook changes.
  Do not run bot setup or polling scripts. Existing dev Telegram route remains unchanged.
- `ui-guide/` content and the user-approved existing deletion of
  `CSA_WebConsole/csa-console/UI_GUIDE.md` remain untouched.
- Persistent dev is **0290**. Do not edit the applied migration. Any necessary M5 schema
  change must be additive/ordered and first tested on a disposable database.
- Last closeout checks: gateway readiness **200**; frontend login **200**.
  Managed detached services were `m4-dev-gateway`, `m4-dev-delivery`, `m4-dev-frontend`,
  `m4-dev-worker-scope-fix`. Do not assume those session handles survive a new session:
  verify actual exact processes/listeners/UID/cwd without printing args/env, avoid
  duplicate workers, and ask before migration or service replacement.
- Gateway loopback **18081**; frontend loopback **13000**. The shared browser was last
  signed in as **Synthetic M4 Reader**, not admin; ask the user to switch privately
  when needed, never request credentials.
- Synthetic tenants: Local Telegram Development
  `76946f31-2b21-4e48-9383-65d1a78029c2` (pilot, KB enabled, approved auto policy);
  M1 Browser Acceptance `7f797233-684a-47ab-8222-6e887b6480db`.

## M5: next bounded implementation, not started

Build a **separate staff conversation inbox and approvals workflow**, not a wider
Sandbox assistant. Discovery and implementation may proceed under the functional
closeout; migration, rollout, business/external-send authority still need scoped approval.

### Critical authority boundary

M4 sandbox history remains private to its exact owner, including against other staff
and platform admins. Its holds are private retained turn fields, **not** customer
`approvals`. M5 must not list these histories or release these holds by treating
`Channel.WEB` alone as a customer-inbox authority.

The original M5 plan's cross-channel approval exit must be reconciled with this approved
narrowing before implementation: ask one focused question about staff-readable customer
conversation types, permitted roles and whether a separate customer-web product is
actually in scope. Do not invent a public web participant model or broaden permissions.
If only Telegram customer inbox is authorized, explicitly stage customer-web approval
acceptance as deferred; do not claim it passes using the private sandbox.

### Existing surfaces to reuse

- `csa/console/access.py`, `contracts.py`, `identity.py`: verified actor/tenant/role,
  CSRF, human versus machine separation. Message-content permissions need explicit policy;
  read-only mutation denial does not by itself decide message-read authority.
- `csa/console/router.py`, `runtime.py`, `timeline.py`: existing metadata/read/audit
  patterns; M5 staff inbox/mutation endpoints do not yet exist.
- `csa/approval/service.py`: `hold_reply`, `decide_reply`, `edit_reply`, `_settle`,
  expiry; `db/sql/approval/` locks/transitions/audit/outbox.
  `decide_reply`/`edit_reply` currently return Telegram-facing strings. Extract structured
  shared transition results as necessary rather than interpreting success strings or
  duplicating SQL. Recheck actual code: current `edit_reply` takes actor/text; the
  Telegram `begin_edit`/callback flow has staff-action binding. Do not fake that binding
  for a console session.
- `csa/staff/callbacks.py`, `csa/agents/order_approval/`, `db/sql/staff/` and
  `db/sql/orders/`: order approvals and outbound-reply approvals are different domains.
  Do not route both through a generic status update.
- `apps/worker/main.py`, `apps/delivery/main.py`, `csa/turn/deps.py`: conversation
  ordering, muted ownership, outbox, web participant protection and M4 guardrails.
- `tests/test_staff_path.py`, `test_staff_codec.py`, `test_order_agent.py`,
  `test_console_sessions.py`, `test_console_chat.py`, `test_worker_coordinator.py`,
  `test_console_contract_snapshot.py`: targeted regression starting points.
- Frontend: retain adopted UI patterns, typed same-origin transport and isolated build
  allowlist; copied API/components are reference-only, never import/mount their backend.

### Implementation and acceptance sequence

1. Confirm staff/customer authority scope; read existing transition SQL and channel
   delivery invariants before changing behavior.
2. Add bounded, paginated, tenant/role-scoped customer conversation/message and approval
   reads, safe source links and explicit private-sandbox exclusion.
3. Implement audited takeover/release/human reply through shared domain services/outbox.
   Define ordering and in-flight-AI behavior; prevent stale AI sends after takeover.
4. Add structured approve/reject/edit transitions with expiration, single-use/concurrent
   decisions, proper order versus reply handlers, bounded/redacted edits and audit.
   Preserve existing rejection/expiry fallback behavior unless explicitly changed.
   Queued outbox is not delivered: response/status wording must remain truthful.
5. Build queue/detail/edit UI, role controls, stale refresh and explicit already-handled/
   expired/conflict results. Preserve Telegram callback behavior.
6. Test tenant/role/owner denial, archive/private holds exclusion, expiry and console/
   Telegram races, duplicate submissions, atomic audit/outbox, crash/replay, takeover/
   release and delivery-channel binding. Never promise exactly-once external delivery:
   Telegram remote-acceptance-before-local-ack can still duplicate.
7. Run focused tests first, then `make lint test`, isolated frontend validation and
   disposable-only reset/migration checks per repository rules. Keep latency failures
   reported separately under the approved exception; new failures need investigation.
   Update OpenAPI/docs and obtain separate approval before guarded dev rollout.

M6 business tabs/providers, email/payment/marketplace execution, PM/terminal/Hermes/
workrooms and public deployment remain out of scope.

## Paste into the new session

```text
Continue from docs/handoffs/2026-10-16-m4-closeout-m5-start.md.
Read AGENTS.md, the handoff, PROGRESS.md, CONSOLE_API.md, the M5 migration-plan
section, LG15 and LG07 before editing. Preserve the dirty worktree and user-owned
untracked review reports. UI-guide material and its existing deletion stay untouched.

M4's read-only Sandbox assistant is functionally closed by explicit user decision,
with a documented laptop-dev performance exception. Latest full suite is still
674 passed / 2 latency failures; final targeted TCP p95 is 261.451 ms versus <200 ms.
Laptop causality is not proven. Do not relax thresholds, claim green tests, or block
all M5 discovery on this accepted deferral; carry performance work into M7/M8.

Begin M5 staff inbox/approvals as a separate surface. First trace shared approval,
order approval, takeover and outbox behavior, then ask one focused authority question
about staff-readable customer channels/roles and whether customer-web is in scope.
Never expose owner-private sandbox threads or release sandbox holds through M5.
Reuse shared domain transitions with structured results, verified session actors,
CSRF/RBAC/tenant checks, audit and idempotent outbox; no fake Telegram identities,
success-string parsing, second SQL approval implementation or unrestricted sends.

Use only synthetic fixtures and disposable test databases. Persistent dev is 0290
on 127.0.0.1:5434/csa; never reset it or connect port 5433. Do not inspect/print
secrets, cookies or env files; do not purge/reconcile uploads or change production,
infrastructure, n8n or Telegram webhook settings. Ask separately before migrations,
dev service replacement or live external-send acceptance. Verify existing processes
before startup; do not duplicate workers. The browser may be signed in as the reader.

Implement the agreed bounded M5 slice end to end, test focused cases then lint/full
suite/frontend/disposable reset checks, report old latency exceptions separately,
and update the canonical docs with exact evidence. Do not begin M6 or commit unless asked.
```
