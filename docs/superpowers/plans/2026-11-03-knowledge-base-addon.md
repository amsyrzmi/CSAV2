# Knowledge base add-on Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sell the knowledge base as its own add-on requiring Customer support, without any existing company losing anything.

**Architecture:** One new entry in the `csa/console/packages.py` catalogue, with `kb_agent` moved into it. Migration 0530 copies each company's Customer support status to the new row. The knowledge base's gates (routes, assistant rule, menu) move to the new package; Inbox and channels stay on Customer support.

**Tech Stack:** Python 3.12, FastAPI, psycopg3, Postgres 16 testcontainers, Next.js with node:test, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-03-knowledge-base-addon-design.md`

## Global Constraints

- Package key `knowledge_base`, title "Knowledge base", kind `addon`, requires `customer_support`, capabilities `kb_agent`, tab `knowledge_base`, sellable.
- Knowledge base summary: "Answers customer questions from the files you upload."
- Customer support summary: "A shared inbox for your channels, with tickets, handoff to your team and order approvals. Without an add-on, most customer questions go to your team."
- Migration `db/migrations/0530_knowledge_base_addon.sql`, reference `backfill-0530`. `EXPECTED_SCHEMA_VERSION` becomes `"0530"`. No new table, role or grant. Never FORCE RLS.
- `lib/navigation.ts`: only the line `knowledge_base: ["knowledge_base"]` changes (owner's exception). Don't edit `components/AppShell.tsx` or `lib/assistant-view.ts`. Never read or touch the `feat-console-redesign-phase-2` worktree.
- `db/migrations/0450_console_service_packages.sql` stays as it is.
- Synthetic fixtures and disposable testcontainers only. No real email address: the repository is public. Never touch staging containers.
- CI is the merge gate. Locally run each changed file's tests one at a time with `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q <file>`, plus `make lint` (never piped) and, from `CSA_WebConsole/csa-console/web`, `npm test`, `npm run lint`, `npm run typecheck` with `CSA_CONSOLE_API_ORIGIN=http://127.0.0.1:9`. Node is at `/home/amsyrzmi/.cache/pyright-python/nodeenv/bin`; symlink `node_modules` from the main checkout. Run `uv sync --frozen` first if there's no `.venv`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **The console calls a `/kb/*` route for a company with Customer support only.** Home or Inbox may read knowledge base readiness or gaps. The page must still load, with no error shown. (Task 2)
2. **Suspending and restoring, in order.** Customer support can't be suspended while the add-on is active. Once both are suspended, restoring Customer support must not bring `kb_agent` back; restoring the add-on does. (Task 1)
3. **A customer question at a company with Customer support only.** With `kb_agent` off, the reply is the handoff, never an error or a promise of an answer. (Task 1)
4. **The migration run on a company whose Customer support is `requested`.** The new row has the same status and no invented activation date. (Task 1)
5. **A company with no Customer support row, or one that already has the add-on row.** No row is created for the first, and the second is left alone. (Task 1)

---

### Task 1: Catalogue, migration and server gates

**Files:**
- Create: `db/migrations/0530_knowledge_base_addon.sql`, `tests/test_knowledge_base_addon.py`
- Modify: `csa/console/packages.py`, `csa/console/kb_uploads.py:176-177`, `csa/console/router.py:294` and its `/kb/*` routes, `apps/gateway/main.py:62`, `tests/test_console_packages.py`, `tests/test_console_services.py`, `tests/test_migrate.py` or any test pinning `"0522"`
- If PR #65 has merged into this branch's base: `deploy/staging-accounts.example.json`, `tests/test_staging_roster.py`

**Interfaces:**
- Produces: package `knowledge_base` in `PACKAGES`; in `csa/console/router.py`, `kb_package = package_gate("knowledge_base")` for the `/kb/*` routes.

- [ ] **Step 1: Write the failing tests** in `tests/test_knowledge_base_addon.py`, using `migrated_dsn`:
  - `test_catalogue_entry`: the key, title, kind, requires, capabilities, tab and both summaries exactly as Global Constraints; `kb_agent` is not in `customer_support`.
  - `test_backfill_copies_status`, parametrised on `active`, `suspended`, `requested`: apply 0530's backfill statement to a synthetic company with that Customer support status. The new row has the same status, the same `activated_by`, `activated_at`, `requested_by`, `requested_at`, reference `backfill-0530`, and `kb_agent`'s `is_enabled` is unchanged.
  - `test_backfill_skips`: a company with no Customer support row gets none; a company that already has a `knowledge_base` row keeps it unchanged.
  - `test_activation_rules`: activating `knowledge_base` without active Customer support gives the existing 409; activating it switches on `kb_agent`; activating Customer support alone leaves `kb_agent` off.
  - `test_suspend_and_restore_order`: suspending Customer support while the add-on is active is refused; after suspending both, restoring Customer support leaves `kb_agent` off, and restoring the add-on turns it on.
  - `test_kb_routes_refuse_customer_support_only`: `GET /kb/documents`, `/kb/ingest-jobs`, `/kb/gaps` and a file route give the existing package refusal for a company with Customer support only, and work with the add-on. Inbox and channels still work with Customer support only.
  - `test_customer_support_only_question_hands_off`: following how the existing turn tests run a turn, a business question at a company with `kb_agent` disabled ends in the handoff, with no exception and no answer claiming a knowledge base result. If no existing fixture can drive this, stop and report rather than mocking the outcome.
- [ ] **Step 2: Run them to confirm they fail.** `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_knowledge_base_addon.py`. Expected: the catalogue test fails on the missing key.
- [ ] **Step 3: Implement the catalogue, the migration and the gates.** The backfill is one `INSERT ... SELECT` from `console_service_packages` where `package='customer_support'`, with `ON CONFLICT (client_id, package) DO NOTHING`. The migration's header states preconditions and rollback, like `0450`. If a `/kb/*` route in `router.py` uses `support` for something other than the knowledge base, leave that route on `customer_support` and say so in the report.
- [ ] **Step 4: Update existing tests.**
  - `test_migration_backfill_members_match_catalogue` compares `0450` with the catalogue: allow exactly this one move, `kb_agent` from `customer_support` to `knowledge_base`, and keep the check strict for every other package.
  - `tests/test_console_services.py` hard-codes `kb_agent` in Customer support at lines 35, 62, 156, 179, 215, 269 and 301. Change what the new behaviour requires. Don't delete assertions; move them to the add-on.
  - Any test pinning `"0522"`.
- [ ] **Step 5: Run** each changed test file, then `make lint`. Expected: all pass, lint exit 0.
- [ ] **Step 6: Commit** with `feat(console): the knowledge base is its own add-on, and existing companies keep it`.

### Task 2: Menu, assistant, console and docs

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/navigation.ts:13`, `csa/workspace/rules.py:154`, `scripts/assistant_smoke.py:204` if a case needs it, `docs/CONSOLE_API.md` (lines 654, 1481, 1502), `docs/ASSISTANT_RETEST.md`, `docs/PROGRESS.md`
- Test: `tests/test_workspace_planner.py`, `tests/test_workspace_registry.py`, `tests/test_services_browser.py`, the web tests that cover navigation

**Interfaces:**
- Consumes: package `knowledge_base` from Task 1.

- [ ] **Step 1: Write the failing tests:**
  - In `tests/test_workspace_planner.py`: `tab_available("knowledge_base", Role.TENANT_STAFF, <Customer support only>)` is false, and with the active add-on it is true. `inbox` stays available with Customer support only.
  - In the web navigation tests: a company with `customer_support` active and no add-on has no Knowledge base entry; with `knowledge_base` active it has one; the Inbox entry is unchanged.
  - In `tests/test_services_browser.py` (stubbed API): the Services page shows Knowledge base as an add-on with its summary, and Customer support's new summary.
  - Review Focus 1: find every console call to `/kb/` outside `KnowledgeWorkspace.tsx`, using `grep -rn "/kb/" CSA_WebConsole/csa-console/web/lib CSA_WebConsole/csa-console/web/components`. For each, add a test that a package refusal leaves the page working with no error shown. If a page must skip the call, make it depend on the active package.
- [ ] **Step 2: Run them to confirm they fail.**
- [ ] **Step 3: Implement.** Change the one line in `navigation.ts` and the one rule in `rules.py`.
- [ ] **Step 4: Update the docs:**
  - `docs/CONSOLE_API.md`: the package table and the two gate notes.
  - `docs/ASSISTANT_RETEST.md`: manual rows for a company with Customer support only (no Knowledge base entry, questions go to staff) and for activating the add-on as a platform admin (entry appears, a file processes, the bot answers from it).
  - `docs/PROGRESS.md`: the top entry in its usual form, with rulings, tests, the rollout (one scripted rollout with 0530 and all images), and that deferred minors are in the PR description.
- [ ] **Step 5: Run** each changed test file, `make lint`, and the web checks. Expected: all pass, exit 0.
- [ ] **Step 6: Commit** with `feat(console): the menu, assistant and docs treat the knowledge base as an add-on`.
