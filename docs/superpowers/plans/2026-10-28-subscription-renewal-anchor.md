# Subscription Renewal Anchor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Renewals keep their cycle's day of the month (31 Oct → 30 Nov → 31 Dec) instead of drifting to a short month's last day.

**Architecture:**
- A nullable `subscriptions.renewal_anchor` records the instant a billing cycle is counted from.
- One SQL function, `public.subscription_next_renewal`, returns the first cycle date strictly after a given instant, using the tenant's calendar.
- Each SQL file that writes `renews_at` calls the function and sets the anchor, following the owner's rules.

**Tech Stack:** PostgreSQL 16, SQL files in `db/sql/`, psycopg 3 async, pytest with Testcontainers.

**Spec:** `docs/superpowers/specs/2026-10-28-subscription-renewal-anchor-design.md`

## Global Constraints

**Migration and schema version**
- The migration file is `db/migrations/0481_subscription_renewal_anchor.sql`, in lane B's range (0480–0489).
- `apps/gateway/main.py` `EXPECTED_SCHEMA_VERSION = "0481"`. Tests import it; they never hardcode it.
- 0481 must be safe to run twice:
  - `ADD COLUMN IF NOT EXISTS`;
  - `CREATE OR REPLACE FUNCTION`;
  - the backfill touches only rows whose `renewal_anchor IS NULL`.

**Calendar and anchor rules**
- All calendar arithmetic is `((t AT TIME ZONE tz) + interval) AT TIME ZONE tz`, with `tz` from `clients.timezone`.
- A plan's interval is `'year'` → `interval '1 year'`. Anything else, including a missing plan row, is `interval '1 month'`, as today.
- The effective anchor of a row is `coalesce(renewal_anchor, renews_at)`. A NULL anchor keeps the old behaviour until the row first renews.

**Database rules**
- Staged RLS is unchanged; never enable FORCE RLS.
- No new grants are needed: the function is `SECURITY INVOKER`, and `csa_app` already has UPDATE on `subscriptions`.

**Scope**
- SQL lives only in `db/sql/` and `db/migrations/`. No Python date arithmetic is added.
- No API, contract or UI change. `SubscriptionOut` does not expose the anchor, so the OpenAPI file and the contract snapshot do not change.
- Synthetic fixtures and disposable test databases only.

**Tooling**
- An editor hook reflows Python to 120 columns. Write `.py` edits through a script, or rebuild the file, then run `ruff format`. Check `git diff` for unrelated reflow before committing.
- Run tests with `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh .venv/bin/pytest -q -p no:cacheprovider <files>`, from the worktree root. Never run the slow or packaged suites.

## Review Focus

1. **A row with NULL `renews_at`.** Extend, resume and the job's resume set an anchor and a renewal. A month ↔ year plan change leaves both NULL. Covered by Task 2 `test_interval_change_without_renewal_date_keeps_both_null`.
2. **A plan row that has been deleted** (the "ghost" plan) renews monthly, exactly as today. Covered by the Task 3 parametrised renewal test, which keeps its `ghost` case.
3. **A long-lapsed row** still catches up one interval per run, and each step stays on the cycle. Covered by Task 3 `test_renewal_catch_up_advances_one_interval_per_run`, updated: it ends on 31 Dec, not 30 Dec.
4. **A non-UTC tenant across a month end:** a local 31 Jan 00:30 renews on local 28 Feb 00:30. Covered by Task 1 `test_next_renewal_uses_the_tenant_calendar`.
5. **Rows inserted by other paths with a NULL anchor** (seed data, older fixtures) renew as before on their first renewal, then keep that day. Covered by Task 3 `test_null_anchor_renews_from_renews_at_then_keeps_the_day`.

---

### Task 1: Migration 0481, the next-renewal function and the backfill

**Files:**
- Create: `db/migrations/0481_subscription_renewal_anchor.sql`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION`)
- Test: `tests/test_subscriptions_renewal.py` (new)

**Interfaces:**
- Produces:
  - the column `public.subscriptions.renewal_anchor timestamptz NULL`;
  - `public.subscription_next_renewal(p_anchor timestamptz, p_after timestamptz, p_interval text, p_tz text) RETURNS timestamptz`. It is `LANGUAGE sql STABLE STRICT SECURITY INVOKER` and returns the first instant `anchor + n × interval` with n ≥ 0 that is strictly after `p_after`, computed on `p_tz`'s calendar. `p_interval = 'year'` means years; any other value means months.

**Algorithm for the function.** Work in local time: `a = p_anchor AT TIME ZONE p_tz` and `b = p_after AT TIME ZONE p_tz`.
- **Months:** `k = (year(b)*12 + month(b)) - (year(a)*12 + month(a))`.
- **Years:** `k = year(b) - year(a)`.
- Let `c = greatest(k, 0)`.
- If `(a + c intervals) AT TIME ZONE p_tz > p_after`, return that. Otherwise return `(a + (c+1) intervals) AT TIME ZONE p_tz`.
- `a + c intervals` falls in the same local month (or year) as `b`, and `c+1` falls in the next one, so this is the smallest n.

**Backfill.**
```sql
UPDATE subscriptions s SET renewal_anchor = s.started_at
WHERE renewal_anchor IS NULL AND renews_at IS NOT NULL
  AND renews_at = subscription_next_renewal(started_at, renews_at - interval '1 microsecond', <plan interval>, <tenant tz>)
```
- The plan interval is taken from `plans` by `(client_id, code = s.plan)`, with `'month'` when the plan row is missing. The tenant tz is taken from `clients`.
- Off-cycle rows and rows with NULL `renews_at` stay NULL.

- [ ] **Step 1: Write the failing tests** in `tests/test_subscriptions_renewal.py`. Use the `pg_container` / `owner_dsn_for` / `seed_tenants` pattern from `tests/test_jobs_subscriptions.py`, and `requires_docker`.
  - Add a helper `next_renewal(conn, anchor, after, interval, tz="Asia/Kuala_Lumpur")` that runs `SELECT subscription_next_renewal(%s,%s,%s,%s)`, and a helper `at("YYYY-MM-DD HH:MM")` for a `+08:00` datetime.
  - `test_next_renewal_monthly_keeps_the_day`: the anchor is `at("2026-10-31 10:00")`. Chaining `after` through each result gives `2026-11-30 10:00`, then `2026-12-31 10:00`, `2027-01-31 10:00`, `2027-02-28 10:00` and `2027-03-31 10:00`.
  - `test_next_renewal_yearly_from_29_february`: the anchor is `at("2028-02-29 09:00")`. Chaining gives `2029-02-28 09:00`, `2030-02-28 09:00` and `2031-02-28 09:00`, then `2032-02-29 09:00`.
  - `test_next_renewal_is_the_first_cycle_date_after`:
    - anchor `2026-01-15 10:00`, after `2026-02-10 10:00` → `2026-02-15 10:00`;
    - anchor `2026-03-15 10:00`, after `2026-03-01 10:00` → `2026-03-15 10:00` (n = 0);
    - anchor equal to after → one interval later;
    - any NULL argument → NULL.
  - `test_next_renewal_uses_the_tenant_calendar`: anchor `2026-01-31 00:30+08:00`, after the anchor, `'month'` → `2026-02-28 00:30+08:00`. That is `2026-02-27 16:30Z`; UTC arithmetic would give `2026-02-28 16:30Z`.
  - `test_backfill_anchors_on_cycle_rows_only`: the tenant uses `Asia/Kuala_Lumpur`, plans `basic` (month) and `annual` (year), and every row is inserted with `renewal_anchor` NULL. Re-execute the 0481 file text on the owner connection, then check each row:
    - started `2026-10-31 10:00`, renews `2026-11-30 10:00`, basic → anchor = started_at;
    - started `2026-10-31 10:00`, renews `2026-12-15 10:00` (extended) → anchor NULL;
    - renews NULL → anchor NULL;
    - started `2027-03-01 10:00`, renews `2028-03-01 10:00`, annual → anchor = started_at;
    - a row with a preset anchor `2026-05-05 10:00` keeps it;
    - plan `ghost` (no plan row), started `2026-01-31 10:00`, renews `2026-02-28 10:00` → anchor = started_at.
  - `test_0481_is_recorded_and_expected`: `max(version)` in `schema_migrations` equals `EXPECTED_SCHEMA_VERSION`, which is `"0481"`.
- [ ] **Step 2: Run the tests and see them fail.** Run `ci.sh .venv/bin/pytest -q -p no:cacheprovider tests/test_subscriptions_renewal.py`. Expected: FAIL, because the function `subscription_next_renewal` does not exist.
- [ ] **Step 3: Write the migration and bump `EXPECTED_SCHEMA_VERSION` to `"0481"`.**
  - The header comment follows 0480's style: purpose, pre-check, rollback (drop the function and the column).
- [ ] **Step 4: Run the tests and see them pass**, together with `tests/test_migrate.py`, `tests/test_health.py` and `tests/test_subscriptions_schema.py`. Expected: all pass.
- [ ] **Step 5: Commit.** Message: `feat(subscriptions): migration 0481 with the renewal anchor and next-renewal function`

### Task 2: Create and staff actions set the anchor

**Files:**
- Modify: `db/sql/subscriptions/subscription_insert.sql`
- Modify: `db/sql/subscriptions/subscription_apply.sql`
- Test: `tests/test_console_subscriptions.py`, `tests/test_subscriptions_executor.py`

**Interfaces:**
- Consumes: `subscription_next_renewal` and `renewal_anchor`, both from Task 1.
- Produces: the rules below. Task 3 mirrors them for the job.

**Rules:**
- **Insert.**
  - The anchor is `coalesce(%(renews_at)s, started_at)`.
  - The default `renews_at` is `subscription_next_renewal(started_at, started_at, plan interval, tz)`, which equals today's one-interval default.
  - The existing `renews_at > started_at` guard is unchanged.
- **Apply `extend_renewal`.** `renewal_anchor = %(renews_at)s`.
- **Apply `resume` when lapsed or NULL** (the existing condition). `renewal_anchor = now()`, and `renews_at = subscription_next_renewal(now(), now(), current plan interval, tz)`. A resume that is not lapsed changes neither.
- **Apply an immediate `change_plan` whose target plan's interval differs from the current plan's.**
  - Each interval uses the month default when its plan row is missing.
  - `renewal_anchor = s.renews_at`, which may be NULL. `renews_at` is unchanged.
  - The same interval, or any other action, leaves the anchor unchanged.

- [ ] **Step 1: Write the failing tests.** Read the anchor with `SELECT renewal_anchor, renews_at FROM subscriptions WHERE id=%s` through the test world's query helper.
  - In `tests/test_console_subscriptions.py`:
    - `test_create_sets_the_anchor_and_a_cycle_default`: POST with `started_at` `2026-10-31T02:00:00Z` on a monthly plan → `renews_at` `2026-11-30T02:00:00Z`, anchor = `started_at`.
    - `test_create_with_renews_at_anchors_on_it`: an explicit `renews_at` `2026-12-15T02:00:00Z` → anchor = that value.
  - In `tests/test_subscriptions_executor.py`, all through the existing card and confirm helpers:
    - `test_extend_renewal_moves_the_anchor`: the subscription is anchored `2026-10-31 10:00+08`, renewing `2026-11-30 10:00+08`. Extend to `2026-12-15 10:00+08` → anchor = `renews_at` = `2026-12-15 10:00+08`.
    - `test_lapsed_resume_restarts_the_cycle_from_now`: after a paused subscription with a past `renews_at` is resumed, `renewal_anchor` is within 5 s of the database's now(), and `renews_at == subscription_next_renewal(anchor, anchor, 'month', tz)`. A paused subscription with a future `renews_at` keeps both values.
    - `test_interval_change_resets_the_anchor_to_the_period_end`: a monthly → yearly immediate change makes the anchor the old `renews_at`, with `renews_at` unchanged. A monthly → monthly change leaves the anchor unchanged.
    - `test_interval_change_without_renewal_date_keeps_both_null`: a monthly → yearly change on a row with NULL `renews_at` and NULL anchor → both stay NULL.
- [ ] **Step 2: Run the tests and see them fail.** Run `ci.sh .venv/bin/pytest -q -p no:cacheprovider tests/test_console_subscriptions.py tests/test_subscriptions_executor.py -k "anchor or cycle"`. Expected: FAIL, because the anchor is unset.
- [ ] **Step 3: Implement the rules in the two SQL files.**
  - Update each file's header comment so it states the anchor rule.
  - The executor's Python changes only if the apply statement needs a new parameter. Pass the target plan's interval through the existing `target` row, not a new query.
- [ ] **Step 4: Run both test files in full and see them pass.**
- [ ] **Step 5: Commit.** Message: `feat(subscriptions): create, extend, resume and interval changes set the renewal anchor`

### Task 3: The due job renews on the cycle, and the docs

**Files:**
- Modify: `db/sql/jobs/subscriptions_due_renew.sql`
- Modify: `db/sql/jobs/subscriptions_due_resume.sql`
- Modify: `db/sql/jobs/subscriptions_due_plan.sql`
- Modify: `docs/CONSOLE_API.md` ("Subscriptions": the job's rules and the paragraph on renewals clamping at month end)
- Modify: `docs/PROGRESS.md` (lane B entry)
- Test: `tests/test_jobs_subscriptions.py`

**Interfaces:**
- Consumes: Task 1's function and column, and Task 2's rules.

**Rules:**
- **Renew.**
  - `renews_at = subscription_next_renewal(coalesce(renewal_anchor, renews_at), renews_at, plan interval, tz)`.
  - `renewal_anchor = coalesce(renewal_anchor, renews_at)`, using the old values.
  - The event payload is unchanged.
- **Resume when lapsed or NULL.** `renewal_anchor = %(now)s`, and `renews_at = subscription_next_renewal(%(now)s, %(now)s, interval, tz)`.
- **Plan rule, when the change applies and the pending plan's interval differs from the current plan's.** `renewal_anchor = s.renews_at`. Skipped changes leave the anchor unchanged.

- [ ] **Step 1: Update and add the tests in `tests/test_jobs_subscriptions.py`.**
  - `add_subscription` already accepts any column, so pass `renewal_anchor=`. Add `renewal_anchor` to `fetch_subscription`'s column list.
  - `test_renewal_catch_up_advances_one_interval_per_run`: the expected second result changes from `2025-12-30 10:00` to `2025-12-31 10:00`. Also assert that the anchor is `at("2025-10-31 10:00")` after the first run.
  - `test_null_anchor_renews_from_renews_at_then_keeps_the_day`: NULL anchor, renews `2026-01-31 10:00`. The first run gives `2026-02-28 10:00` with the anchor set to `2026-01-31 10:00`. A run after that gives `2026-03-31 10:00`.
  - `test_anchored_renewal_returns_to_the_day`: anchor `2026-10-31 10:00`, renews `2026-11-30 10:00` → `2026-12-31 10:00`.
  - `test_extended_anchor_is_followed`: anchor = renews = `2026-12-15 10:00` → `2027-01-15 10:00`.
  - `test_job_lapsed_resume_restarts_the_cycle`: the anchor is the run's `now`, and `renews_at` is one month later on the tenant calendar.
  - `test_scheduled_interval_change_resets_the_anchor`: monthly → `annual` applied by the job makes the anchor the old `renews_at`. Monthly → monthly leaves it unchanged.
  - `test_renewal_advances_by_interval_month_and_year`: keep the parametrised cases, including `ghost`, unchanged. They have NULL anchors.
- [ ] **Step 2: Run the tests and see the new ones fail.** Run `ci.sh .venv/bin/pytest -q -p no:cacheprovider tests/test_jobs_subscriptions.py`. Expected: FAIL in the updated catch-up test and the new tests.
- [ ] **Step 3: Implement the rules in the three job SQL files, and update their header comments.**
- [ ] **Step 4: Run `tests/test_jobs_subscriptions.py`, `tests/test_jobs.py` and `tests/test_subscriptions_renewal.py`, and see them pass.**
- [ ] **Step 5: Update the docs.**
  - **CONSOLE_API "Subscriptions":** replace the sentence that renewals on the 29th–31st fall back to the shorter month's last day with these rules:
    - renewals keep the cycle's day and clamp only in a shorter month;
    - an extension moves the cycle to the chosen date;
    - a lapsed resume restarts the cycle from the resume time;
    - a plan change between monthly and yearly counts the new interval from the end of the current period.
  - **PROGRESS lane B entry:** replace "Known behaviour, owner question open: …" with one sentence on the owner decision and this change. Add the migration `0481` and the test counts from Steps 4 of all three tasks.
- [ ] **Step 6: Run `make -C <worktree root> lint`.** Expected: clean.
- [ ] **Step 7: Commit.** Message: `feat(subscriptions): the due job renews on the cycle's day; document the anchor`
