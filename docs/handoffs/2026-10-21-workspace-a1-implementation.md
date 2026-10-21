# Developer A — authenticated workspace A1 handoff

Status: A1 implementation is reviewable on its isolated worktree. The additive
migration has been applied only by focused disposable Testcontainers tests; it has
not been applied to persistent development, staging or production databases.

## A1 acceptance-review follow-up (2026-10-21)

This follow-up supersedes the initial validation counts below, not their historical
failure records. Review found and fixed three integration gaps:

- Nonempty history selected internal `action`, `page_key` and `resource_id` columns,
  which the strict public DTO rejected. History SQL now returns only public fields;
  order and guidance history, pagination and replay are regression-tested.
- The frontend proxy had not adopted `/workspace` and `/portal/workspace`, so a
  successful build still left those pages returning 404. Both exact paths are now
  adopted, with anonymous redirect and session-cookie navigation regression tests.
  Other deferred routes remain blocked.
- Embedded workspace input-schema references originally pointed at a nonexistent
  document-root `$defs`. They now point at their actual location in the OpenAPI
  document, with a reference-resolution test and regenerated snapshot.

Platform tenant selection now always starts empty, even if a platform session has an
active tenant. History/list callbacks discard aborted responses before modifying
component state. No additional domain action or authority was introduced.

New disposable evidence covers concurrent exact replay (one turn and one audit),
redacted history, forbidden actor/tenant/role fields, oversized bodies, current
membership revocation, audit-failure rollback/retry, 20-thread/50-turn pagination,
missing/wrong tenant GUC isolation and denied app UPDATE/DELETE/read-only SELECT.

Final focused command (all selected cases passed, none skipped):

```sh
NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 UV_NO_SYNC=1 \
  UV_PROJECT_ENVIRONMENT=/home/amsyrzmi/CSAV2/.venv \
  uv run pytest tests/test_workspace_contracts.py tests/test_console_workspace.py \
  tests/test_console_contract_snapshot.py \
  tests/test_migrate.py::test_migrations_apply_and_are_idempotent -q --tb=short
```

Result: **98 passed**, one Testcontainers deprecation warning, 138.64 seconds.
The same offline/environment prefix with `make lint` passed Ruff, formatting,
Pyright (zero errors/warnings) and import-linter.

The frontend `check:isolated` command below passed **24 tests** (18 identity transport,
2 sandbox state, 3 workspace paths, 1 release origin), ESLint, Next type generation,
TypeScript, the production build and trace exclusion checks.

A broader focused regression command used the same offline/environment prefix:

```sh
uv run pytest tests/test_workspace_contracts.py tests/test_console_workspace.py \
  tests/test_console_contract_snapshot.py tests/test_console_business.py \
  tests/test_console_chat.py::test_execution_rejects_mutations_and_private_reads \
  tests/test_console_chat.py::test_text_authority_bounds_redaction_and_stable_history \
  tests/test_migrate.py::test_migrations_apply_and_are_idempotent tests/test_health.py \
  -q --tb=short
```

It produced **117 passed, 2 failed**, one deprecation warning, 417.01 seconds. Both
failures were in new revocation test fixtures: the last tenant admin cannot be
removed, and membership changes revoke sessions (401, not 403). The tests now create
a second tenant admin where required and assert 401; their corrected versions passed
in the final 98-case run. The existing business, sandbox, readiness and migration cases
all passed in the broader run; they were not rerun after these test-only corrections.
Earlier reproduction checks also failed as expected for nonempty history and proxy
adoption before their fixes. No full/load/reset suite or live acceptance was run.

## Start here

- Worktree: `/home/amsyrzmi/CSAV2-workspace-a0`
- Branch: `feature/workspace-a0-contract`
- Base: `0264c0ade3739f1285ab1a06e577573b71984d44`
- Original `/home/amsyrzmi/CSAV2` worktree remains on `main` at that base and retains
  its pre-existing dirty staging, progress, handoff and review-deletion state.
- Developer B's worktree is now `/home/amsyrzmi/CSAV2-hr` on `feat/hr-foundation`,
  still at the same base; the old `/tmp/csav2-hr-foundation` path is no longer a Git
  worktree. Its dirty HR source, SQL and tests were not edited or integrated.

Read [A0 authority design](./2026-10-21-workspace-a0-authority-design.md),
[console API contract](../CONSOLE_API.md), [LG15](../langgraph-rebuild/LG15_console_turnover.md),
the frontend's [AGENTS.md](../../CSA_WebConsole/csa-console/web/AGENTS.md) and
[README](../../CSA_WebConsole/csa-console/web/README.md) before continuing. The
installed Next.js 16.3.8 Server/Client Components guide was read before final frontend
edits.

## Implemented behavior

- Added `/console/v1/workspace` as a separate authenticated internal staff surface.
  It uses the current human session, active tenant selection, existing role checks,
  Origin/CSRF handling, transaction-local tenant context and RLS. Platform admins must
  select a tenant. Tenant users cannot select another tenant. Readers, anonymous users
  and machine principals are denied.
- Added owner-private threads and turns. Each turn records tenant, owner and the
  authenticated console session, with a composite database constraint binding that
  session to its owner. Thread/turn reads are scoped by tenant and owner. Request-key
  retries are stable; changed payloads and stale order versions return 409.
- Added only deterministic page guidance and one local order read. The order ID remains
  an untrusted hint; access uses the shared `get_local_order` service and tenant SQL.
  The response contains a bounded local projection, `source`, `marketplace_sync` and
  `as_of`; missing and foreign orders produce the same `resource_unavailable` result.
- Turn text is redacted before persistence. Internal result metadata, credentials,
  actor/role/grant claims, customer contacts, model tools, callbacks, HR data, payroll,
  mutations, queued work, marketplace calls and voice are not included.
- Added the adopted `/workspace` and `/portal/workspace` pages. Platform tenant
  selection is explicit; changing tenants remounts the workspace and aborts in-flight
  requests. UI retries preserve the request key while mounted. The existing customer
  sandbox and Telegram behavior are unchanged.
- Authored additive migration **0370** and parameterized SQL. The migration adds
  workspace tables, tenant RLS, sequence grants and the composite session-owner
  constraint. Gateway readiness and migration expectations now target 0370.
- Updated the console API documentation and generated OpenAPI snapshot.

This A1 implementation has no per-tenant workspace capability switch: the route is
enabled by application rollout and exposes only the fixed read actions above. No
queued proposal exists in this slice, so future queued/approval work must add fresh
capability and authority rechecks rather than treating this synchronous boundary as
execution evidence for later actions.

## Changed areas

- Backend: `csa/workspace/`, `csa/console/workspace.py`,
  `csa/console/business.py`, `csa/console/router.py`.
- Schema/SQL: `db/migrations/0370_workspace_assistant.sql`,
  `db/sql/workspace/`.
- Readiness/contracts: `apps/gateway/main.py`, `tests/test_migrate.py`,
  `tests/test_packaged_console.py`, `docs/console_openapi.json`.
- UI: `CSA_WebConsole/csa-console/web/app/(admin)/workspace/`,
  `app/portal/workspace/`, `components/console/WorkspaceAssistant.tsx`,
  `components/AppShell.tsx`, `lib/workspace-api.ts`,
  `lib/workspace-paths.*`, `tests/workspace-paths.test.mjs` and
  `scripts/check-isolated.mjs`.
- Tests: `tests/test_workspace_contracts.py`,
  `tests/test_console_workspace.py`, and workspace cleanup in
  `tests/test_console_sessions.py`; follow-up coverage also extends
  `tests/test_console_contract_snapshot.py` and frontend identity transport tests.
- Docs: `docs/CONSOLE_API.md`, `docs/PROJECT_OVERVIEW.md`,
  `docs/schema_notes.md`, and this handoff.

## Initial implementation validation evidence (before the follow-up)

Final focused backend command:

```sh
NO_LOCAL_ENV=1 ENV=ci UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/amsyrzmi/CSAV2/.venv \
  uv run pytest tests/test_workspace_contracts.py tests/test_console_workspace.py \
  tests/test_console_contract_snapshot.py \
  tests/test_migrate.py::test_migrations_apply_and_are_idempotent -q
```

Result: **93 passed**, one existing Testcontainers deprecation warning. This includes
synthetic disposable-Postgres coverage for tenant selection/isolation, owner access,
role/CSRF/machine/anonymous denial, foreign order non-disclosure, idempotency,
resource-version replay checks, migration application/idempotency and the OpenAPI
snapshot. It does not establish persistent-dev or browser acceptance.

Final repository lint:

```sh
NO_LOCAL_ENV=1 ENV=ci UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/amsyrzmi/CSAV2/.venv \
  make lint
```

Result: Ruff check/format, Pyright and import-linter passed.

Frontend:

```sh
PATH=/tmp/fourstacks-toolbin:$PATH npm --prefix \
  /home/amsyrzmi/CSAV2-workspace-a0/CSA_WebConsole/csa-console/web \
  run check:isolated
```

Result: 17 identity transport, 2 sandbox state, 3 workspace path and 1 release-origin
tests passed; ESLint, Next type generation, TypeScript, Next production build and build
trace boundary checks passed. The check built `/workspace` and `/portal/workspace`.
`npm ci --ignore-scripts --no-audit --no-fund` installed the pinned frontend packages;
package manifests and lockfile were not changed. The standalone command
`node --test tests/workspace-paths.test.mjs` also passed all 3 tests.

OpenAPI export:

```sh
NO_LOCAL_ENV=1 ENV=ci UV_NO_SYNC=1 UV_PROJECT_ENVIRONMENT=/home/amsyrzmi/CSAV2/.venv \
  uv run python -m scripts.export_console_openapi
```

Result: exported successfully; the contract snapshot test passes.

Resolved during implementation: the initial result-union schema failed OpenAPI
validation and was changed to a plain typed union; initial API tests exposed internal
columns in the turn response and a session-FK test fixture cleanup issue. These were
fixed and the final focused tests pass. The first frontend lint run rejected synchronous
effect state resets; those resets were removed and the final isolated check passes.

Not run: full `make test` / `make lint test`, the disposable reset acceptance test
`tests/test_migrate.py::test_make_reset_only_disposable_container`, load tests,
authenticated browser acceptance, service starts/restarts or persistent migration
application. These remain deferred pending the coordinated host window and separate
authorization. No `.env.local`, production database, production credentials, staging
connector, provider or production service was inspected or used.

## Integration review and full-suite evidence (2026-10-21)

The integration reviewer ran the deferred validation on this worktree with
`NO_LOCAL_ENV=1 ENV=ci UV_OFFLINE=1 UV_NO_SYNC=1` and disposable Testcontainers:

- `make lint` passed (Ruff, formatting, Pyright, import-linter: 1 kept, 0 broken).
- Full `pytest`: **2640 passed, 1 failed, 4 errors**, 136 warnings, 2087.91 seconds.
  - The failure was `test_load_acceptance.py::test_real_ingress_50rps_and_durable_queue[tcp]`
    (p95 248.69 ms against the 200 ms bound), the previously recorded host-latency failure.
  - The four errors were `test_packaged_console.py` setup errors: Node was not on `PATH`.
- With the local Node toolchain on `PATH`, `tests/test_packaged_console.py` produced
  **3 passed, 1 failed** in 566.77 seconds. The failure was
  `test_packaged_m2_browser_acceptance`, where Playwright's headless Chromium could not load
  the missing host library `libatk-1.0.so.0` (exit 127) before reaching application code.
  CI installs Chromium with `--with-deps`, so hosted CI must provide that evidence.
- Frontend `check:isolated` passed again, including the `/workspace` and `/portal/workspace`
  build routes and trace exclusion checks.
- Reset verification (`tests/test_migrate.py::test_make_reset_only_disposable_container`) ran
  inside the full suite and passed; bare `make db-reset` was not run.

A read-only code review found one medium hardening item: `_fingerprint` in
[service.py](../../csa/workspace/service.py) stores an unsalted SHA-256 of the unredacted
turn payload, so stored rows allow guess-confirmation of short messages. The existing
sandbox chat already uses the same pattern ([chat.py](../../csa/console/chat.py)), so this is
not a regression. A follow-up should switch both surfaces to a keyed HMAC after the key
source and rotation are decided.

## Remaining gates and next bounded slice

- Replace unsalted request fingerprints with a keyed HMAC for both the workspace and
  the sandbox chat once the key source and rotation are decided.

- HR field grants/access owner are unresolved. Do not add HR reads/actions until the
  policy owner and allowed projections are decided; Developer B's independent HR-0
  remains separate.
- Workspace conversation retention/deletion policy has not been selected; records
  currently persist until a separately reviewed retention policy is implemented.
- Voice consent, supported languages and retention are unresolved. No microphone,
  recording, LiveKit session or real provider integration is implemented.
- Coordinate the shared-host window with Developer B before full-suite/reset acceptance.
- Next bounded slice: review A1's shared authority and persistence contract with the
  workspace/HR owners, then run the explicitly deferred disposable reset and full
  validation only in the reserved window. After that seam is accepted, a separate
  synthetic finalized-utterance adapter can reuse the verified workspace boundary;
  keep real LiveKit/provider work outside that slice.

At handoff time no files had been committed or pushed and no PR had been created;
the integration review then committed this slice on `feature/workspace-a0-contract`
for a PR to `main`.

## Copy-ready continuation prompt

```text
Continue Developer A's A1 review in /home/amsyrzmi/CSAV2-workspace-a0 on
feature/workspace-a0-contract, based at 0264c0ade3739f1285ab1a06e577573b71984d44.
Read this handoff, the linked A0 design, current source and AGENTS.md first.
Preserve all worktrees and do not stage, commit, push, merge or create a PR.

A1 adds an authenticated, owner-private staff workspace for deterministic page
guidance and one tenant-scoped local order read. Migration 0370 is authored and
was exercised only in disposable focused tests; do not apply it to persistent
databases. Preserve the customer sandbox and Telegram behavior. Do not add HR,
payroll, business mutation, marketplace, callbacks, model tools, LiveKit or a
voice service. Keep Developer B's HR worktree independent.

Latest focused evidence: 98 pytest cases passed (one Testcontainers deprecation
warning), make lint passed, and frontend check:isolated passed its 24 tests,
lint, type generation, TypeScript, build and trace checks. Full suite/reset,
load tests and authenticated browser acceptance remain unrun by instruction.
The broader regression run had 117 passes and two new revocation-test fixture
failures; those test-only corrections passed in the final focused run. Preserve
that evidence rather than calling the broader run wholly green.
Coordinate the shared-host window with Developer B before requesting those checks.
Resolve HR grants/field policy and voice consent/language/retention before their
respective future work. Next, review the shared authority contract and perform
the deferred acceptance only when explicitly authorized.
```
