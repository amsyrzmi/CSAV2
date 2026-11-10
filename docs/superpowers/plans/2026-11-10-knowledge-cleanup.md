# Knowledge Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tenant Admin can remove, retry or clear any knowledge upload that failed, and deleting a source never leaves a "Needs attention" card behind.

**Architecture:** Keep the job row and add a `cancelled` status and a `dismissed_at` timestamp (approach B of the spec). Deleting a file or document closes the jobs that list it in the same transaction. Four job routes (dismiss, retry, cancel, dismiss-failed) sit beside `GET /kb/ingest-jobs`. The console maps every job state to plain words and the one or two actions that fit it.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async with SQL files under `db/sql/`, PostgreSQL migrations, Next.js console with node unit tests (`.mjs`) and Playwright browser tests.

**Spec:** `docs/superpowers/specs/2026-11-10-knowledge-cleanup-design.md` (approved 2026-11-10). Programme: `docs/superpowers/specs/2026-11-10-business-surfaces-programme.md`.

## Global Constraints

- Migrations 0550 to 0559 only; additive; a rebuilt check constraint reads the live list with `pg_get_constraintdef` and adds to it, as `db/migrations/0540_training_page_key.sql` does. `EXPECTED_SCHEMA_VERSION` in `apps/gateway/main.py` is raised from `"0542"` to `"0550"`; tests import the constant and never hardcode it.
- SQL lives in `db/sql/kb/*.sql`; psycopg 3 async only; every tenant query goes through `tx(pool, str(client_id))` (RLS).
- Every new job action writes an audit row with `kb_audit(conn, actor, client_id, action, target)` and needs the `kb_writer` role. Audit action names: `kb.job_dismissed`, `kb.job_retried`, `kb.job_cancelled`, `kb.jobs_dismissed`.
- Job status words: `queued`, `processing`, `done`, `partial`, `failed`, `cancelled` (new). The console must handle `processing`, not `running`.
- Console words follow `docs/CONSOLE_VOICE.md`; no raw error text outside Technical details. Synthetic test data only (the repository is public).
- Tests: run one file at a time through `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh <command>`; CI is the merge gate. Browser files only with more than 3 GB free memory. After changing a call or a response shape, grep every browser stub of it (`tests/test_knowledge_browser.py` and the others) and run those files by hand before pushing.
- Copy (exact, from the spec): failed with the file stored: "We couldn't read this file. You can try again or remove it."; failed with the original gone: "The original was removed, so this can't be tried again. Remove it."; partial: "Some of this file couldn't be read. The rest is in your assistant."; remove confirmation: "This takes the message off your list. Nothing your assistant knows changes."; paused queued job: "Paused: the knowledge assistant is switched off."

## Review Focus

- Source deleted while a worker is reading its job: no document is stored, the job ends `cancelled` and dismissed, and no card appears (Task 2).
- Try again on a job that failed because its content was deleted after queueing: 409 `original_removed`, and the card offers Remove only (Tasks 3, 4).
- Two quick Try again clicks, or Try again on a job that is already queued: one queues, the other gets 409, never two queued copies (Task 3).
- A legacy job whose `files` entries carry a path and no `file_id`: treated as stored for retry and never cancelled by the backfill (Tasks 1, 3).
- Dismissing a running job gives 409, and another tenant's job gives 404 (Task 3).
- Delete routes now answer 200 with a body: the console must not break on it (Tasks 3, 5).

## Deviations from the spec

- **Retry re-runs every file of a `partial` job**, not only the failed ones. The ingest already answers `duplicate` for a file whose hash is stored (`kb/find_document_by_hash.sql`), so re-running is harmless and needs no per-file merge into `meta.results`. The spec's "re-runs only the failed files" is dropped for that reason.
- **A job with some live files is left untouched** when one of its files is deleted (the spec's per-file note in `meta.results` is dropped): the card already shows the deleted file as gone because the console knows the live files list.

---

### Task 1: Migration 0550, job columns and the backfill

**Files:**
- Create: `db/migrations/0550_kb_job_cleanup.sql`
- Modify: `db/migrations/README.md` (add the `0550`–`0559` range line: knowledge cleanup), `apps/gateway/main.py:71`
- Test: `tests/test_kb_job_cleanup_schema.py`

**Interfaces:**
- Produces: `kb_ingest_jobs.dismissed_at timestamptz NULL`; status check allows `'cancelled'`; every existing job whose entries all carry a `file_id` of a soft-deleted `kb_upload_files` row is `cancelled` with `dismissed_at` set.

- [ ] **Step 1: Write the failing tests** in `tests/test_kb_job_cleanup_schema.py`, using the disposable-database helpers of `tests/test_training_schema.py` (`_owner`, `_company`, `migrate`). Tests: `test_status_check_allows_cancelled_and_keeps_the_old_values`, `test_dismissed_at_is_nullable_and_new_jobs_start_visible`, `test_backfill_cancels_failed_partial_queued_and_processing_jobs_whose_files_are_all_deleted`, `test_backfill_leaves_a_job_with_one_live_file`, `test_backfill_leaves_a_path_only_job_alone`, `test_the_migration_is_safe_to_run_twice`. The backfill tests insert jobs and `kb_upload_files` rows at the state before 0550 (apply migrations up to 0542, insert, then apply 0550), and assert the final `status`/`dismissed_at`.
- [ ] **Step 2: Run** `pytest tests/test_kb_job_cleanup_schema.py -q` and confirm every test fails (no column, no status).
- [ ] **Step 3: Write `0550_kb_job_cleanup.sql`.** Add `dismissed_at`; rebuild `kb_ingest_jobs_status_check` by reading the live list (the 0540 pattern) and adding `'cancelled'`; then one UPDATE setting `status='cancelled'`, `dismissed_at=now()`, `locked_at=NULL`, `finished_at=COALESCE(finished_at, now())` for jobs in `failed|partial|queued|processing` whose `files` array is non-empty and where every entry has a `file_id` whose `kb_upload_files` row has `deleted_at IS NOT NULL`. Header comment states what, how, rollback, as 0540 does. Add the README range line and set `EXPECTED_SCHEMA_VERSION = "0550"`.
- [ ] **Step 4: Run** the new file and `tests/test_migrations.py` if present (otherwise `tests/test_tiktok_migrations.py`) and confirm PASS.
- [ ] **Step 5: Commit** `feat(kb): migration 0550, cancelled jobs and dismissed_at, with the backfill`.

### Task 2: Deleting a file or document closes its jobs

**Files:**
- Create: `db/sql/kb/jobs_close_for_files.sql`
- Modify: `csa/agents/kb/lifecycle.py` (new helper, `delete_document`), `csa/console/kb_uploads.py` (`remove_file`)
- Test: `tests/test_console_kb_job_cleanup.py` (create; mirror the `sessions: SessionWorld` fixtures and helpers of `tests/test_console_kb_uploads.py`)

**Interfaces:**
- Produces: `async def close_jobs_for_files(conn: AsyncConnection[Any], client_id: UUID, file_ids: Sequence[UUID]) -> int` in `csa/agents/kb/lifecycle.py`: sets `status='cancelled'`, `dismissed_at=now()`, `locked_at=NULL`, `finished_at=COALESCE(finished_at, now())` on every job of the tenant in a non-`done` status whose `files` entries all have a `file_id` that is soft-deleted (the just-deleted ids count as deleted); returns the row count. Never touches `done` jobs or path-only jobs. Call it under the existing `lock()`.
- `delete_document` calls it with the ids returned by `kb/files_delete_document.sql`; `remove_file` calls it with the one `file_id`.

- [ ] **Step 1: Write the failing tests** in `tests/test_console_kb_job_cleanup.py`: `test_removing_the_only_file_of_a_failed_job_cancels_and_hides_it`, `test_removing_a_document_closes_the_jobs_of_its_files`, `test_a_job_with_another_live_file_is_left_untouched`, `test_a_done_job_is_never_changed_by_a_delete`, `test_a_path_only_job_is_never_closed`, `test_remove_while_a_worker_holds_the_job_ignores_the_workers_result` (claim the job as processing, delete the file, then call `KbIngestProcessor._finish(client_id, job_id, "failed", "x", [])` and assert the status stays `cancelled` and no document exists). Assert through `GET /kb/ingest-jobs` that the closed job is absent (Task 3 adds the filter; until then assert the row in the database).
- [ ] **Step 2: Run** `pytest tests/test_console_kb_job_cleanup.py -q` and confirm failure.
- [ ] **Step 3: Implement** `jobs_close_for_files.sql` (one UPDATE with a NOT EXISTS over `jsonb_array_elements(files)` entries that lack a deleted file), the helper, and the two call sites.
- [ ] **Step 4: Run** the new file, `tests/test_console_kb_uploads.py` and `tests/test_kb_ingest.py`, one at a time; confirm PASS.
- [ ] **Step 5: Commit** `feat(kb): deleting a source or document closes the jobs that only it kept alive`.

### Task 3: Job routes, list filter, delete changes, API docs

**Files:**
- Create: `db/sql/kb/job_dismiss.sql`, `job_retry.sql`, `job_cancel.sql`, `jobs_dismiss_failed.sql`
- Modify: `db/sql/kb/list_ingest_jobs.sql` and `get_ingest_job.sql` (hide `dismissed_at IS NOT NULL`; also return `deleted_file_ids uuid[]`: the job's entries' `file_id`s that are soft-deleted), `csa/console/kb_contracts.py` (`JobOut.original_stored: bool`, `public_job`), `csa/agents/kb/lifecycle.py` (export `CONTENT_DELETED_ERROR`, used by `check_ingestion`), `csa/console/router.py` (four routes beside `list_kb_ingest_jobs`), `csa/console/kb_uploads.py` (delete routes), `docs/CONSOLE_API.md`, `docs/console_openapi.json` (regenerate with `scripts/export_console_openapi.py`)
- Test: `tests/test_console_kb_job_cleanup.py`

**Interfaces:**
- Consumes: `close_jobs_for_files` (Task 2).
- Produces (all `dependencies=[Depends(kb_package), Depends(kb_writer)]`; retry also `Depends(kb_enabled)`):
  - `POST /kb/ingest-jobs/{job_id}/dismiss` → 204; 409 `job_still_running` for `queued` or `processing`; 404 if not found.
  - `POST /kb/ingest-jobs/{job_id}/retry` → 200 `JobOut`; one UPDATE guarded by `status IN ('failed','partial') AND dismissed_at IS NULL` sets `status='queued'`, `attempts=0`, `error=NULL`, `created_at=clock_timestamp()`, `started_at=NULL`, `finished_at=NULL`, `locked_at=NULL`; the route first refuses with 409 `original_removed` when `original_stored` is false and 409 `not_retryable` when the guarded UPDATE changes nothing.
  - `POST /kb/ingest-jobs/{job_id}/cancel` → 200 `JobOut`; only `queued` jobs (409 `not_cancellable` otherwise), sets `cancelled`, `dismissed_at`, `locked_at=NULL`.
  - `POST /kb/ingest-jobs/dismiss-failed` → 200 `{"dismissed": int}` for every `failed`, `partial` and `cancelled` job of the tenant.
  - `JobOut.original_stored`: true unless some entry's `file_id` is in the row's `deleted_file_ids`, or the job's `error` or a `meta.results[].error` equals or starts with `CONTENT_DELETED_ERROR`.
  - `DELETE /kb/files/{id}` and `DELETE /kb/documents/{id}` → 200 `{"storage_cleanup": "done" | "pending"}` (`cleanup_after_remove` returns a bool instead of swallowing silently: still logs, returns False on failure); they no longer depend on `enabled` (upload and retry still do).

- [ ] **Step 1: Write the failing tests**: `test_dismiss_hides_a_failed_job_and_audits`, `test_dismiss_of_a_queued_or_processing_job_is_409`, `test_dismiss_of_another_tenants_job_is_404`, `test_retry_requeues_from_the_stored_original_and_resets_attempts_and_created_at`, `test_retry_of_a_partial_job_reruns_every_file_and_ends_done_with_duplicates` (run the processor once after the retry), `test_retry_after_the_source_was_deleted_is_409_original_removed`, `test_retry_of_a_content_deleted_failure_is_409_original_removed`, `test_retry_twice_queues_once` (second call 409 `not_retryable`), `test_retry_when_the_knowledge_agent_is_off_is_409`, `test_cancel_a_queued_job_only`, `test_dismiss_failed_clears_all_and_reports_the_count`, `test_dismissed_jobs_are_missing_from_list_and_get`, `test_a_path_only_job_counts_as_original_stored`, `test_delete_file_and_document_work_while_the_agent_is_off_and_answer_200_with_storage_cleanup`, `test_delete_reports_pending_when_the_storage_sweep_fails` (extend the existing failing-sweep fixture of `test_remove_succeeds_when_storage_cleanup_fails`), `test_job_routes_need_a_writer_and_a_package` (read-only role 403, package off 403/409 as the existing gate does). Update the two existing tests that assert 204 on the delete routes.
- [ ] **Step 2: Run** `pytest tests/test_console_kb_job_cleanup.py -q`; confirm failures for the right reasons.
- [ ] **Step 3: Implement** the SQL files, `JobOut`/`public_job`, `CONTENT_DELETED_ERROR`, the four routes and the two changed delete routes, with an audit row per action.
- [ ] **Step 4: Docs and contract**: add the five route lines and the changed delete responses to `docs/CONSOLE_API.md`; regenerate `docs/console_openapi.json` and run the test that guards it (`grep -l console_openapi tests/*.py`).
- [ ] **Step 5: Run** `tests/test_console_kb_job_cleanup.py`, `tests/test_console_kb_uploads.py`, `tests/test_kb_ingest.py` and the OpenAPI guard test, one file at a time; confirm PASS; `ruff check` and `ruff format --check` on changed files.
- [ ] **Step 6: Commit** `feat(kb): dismiss, retry, cancel and clear failed jobs; delete reports its storage sweep`.

### Task 4: Console rules for job states and the new contract

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/identity-contracts.ts` (`KbJob.original_stored: boolean`; the status union gains `"cancelled"` and `"processing"` if it is a union), `lib/knowledge-view.ts`
- Test: `CSA_WebConsole/csa-console/web/tests/knowledge-view.test.mjs`

**Interfaces:**
- Produces in `knowledge-view.ts`: `jobChip` treats `queued` and `processing` as `reading`, `done` and `cancelled` as `null`, everything else `needs_attention`; `progressWord("processing")` is `"Reading"`; `jobStateText("processing")` is `"Being read now"`; `jobStateText("cancelled")` is `"Removed"`; `groupSources` makes no card for a `cancelled` job; `pollingNeeded` is true while a `processing` job exists; new `export function attentionNote(job: KbJob): string` returning the three copy lines of Global Constraints (failed with `original_stored`, failed without it, partial); new `export type JobActions = { canRetry: boolean; canRemove: boolean; canCancel: boolean }` and `export function jobActions(job: KbJob): JobActions` (retry only for `failed`/`partial` with `original_stored`; remove for `failed`, `partial`, `cancelled`; cancel for `queued`).

- [ ] **Step 1: Write the failing tests** in `knowledge-view.test.mjs`: `processing shows Reading and keeps polling`, `cancelled makes no card`, `attentionNote for each of the three states`, `jobActions table for every status and original_stored combination`, `a failed job with its original gone offers Remove only`, `a queued job offers Cancel`.
- [ ] **Step 2: Run** the node test file (the command used by `package.json`'s test script for `.mjs` files) and confirm failure.
- [ ] **Step 3: Implement** the changes above; remove the `running` handling that nothing sends.
- [ ] **Step 4: Run** the file and `npm run lint`/typecheck for the web app; confirm PASS.
- [ ] **Step 5: Commit** `feat(console): knowledge job states, plain notes and per-job actions`.

### Task 5: Console screens, browser tests and runbook

**Files:**
- Modify: `components/console/knowledge/KnowledgeCard.tsx` (note from `attentionNote`; buttons Try again, Remove, Cancel from `jobActions`; paused line for a queued job when the agent is off), `LibrarySection.tsx` (confirm copy per case, success message per case, "Clear all that failed" when more than one attention card, handlers for retry/dismiss/cancel/dismiss-failed, tolerate the delete response body), `MaintenanceSection.tsx` (sweep result sentence), `docs/RUNBOOK.md` (paragraph: stuck knowledge jobs, what Remove and Try again do, the 0550 backfill)
- Test: `tests/test_knowledge_browser.py`

**Interfaces:**
- Consumes: `jobActions`, `attentionNote` (Task 4); the four routes (Task 3). Web calls: `post(path(`kb/ingest-jobs/${id}/retry`))`, `.../dismiss`, `.../cancel`, `post(path("kb/ingest-jobs/dismiss-failed"))`; `del` already types its result with `<T>`, use `del<{ storage_cleanup: "done" | "pending" }>`.
- Wording: remove confirmation and success sentences per Global Constraints; the delete-source confirm for a failed upload says what it does without document language; a `pending` sweep adds "We'll finish clearing the stored file shortly."

- [ ] **Step 1: Write the failing browser tests** (reuse the `site` fixture): `test_a_failed_card_shows_the_plain_note_and_try_again_and_remove`, `test_remove_asks_first_then_the_card_disappears`, `test_try_again_turns_the_card_into_reading`, `test_a_failed_card_whose_original_is_gone_offers_remove_only`, `test_clear_all_that_failed_removes_every_attention_card`, `test_a_processing_job_shows_reading_not_needs_attention_and_refreshes`, `test_a_queued_job_with_the_agent_off_shows_paused_and_cancel`, `test_deleting_a_source_makes_no_attention_card`, `test_delete_still_works_when_the_agent_is_off`, plus the portal twin of the attention flow. Stub the four new routes and the 200 body of the delete routes.
- [ ] **Step 2: Run** `pytest tests/test_knowledge_browser.py -q -k "<new names>"`; confirm failure.
- [ ] **Step 3: Implement** the components and the runbook paragraph.
- [ ] **Step 4: Run** `tests/test_knowledge_browser.py` entire, then grep and run every other browser file that stubs `kb/files` or `kb/ingest-jobs` (`grep -ln "kb/ingest-jobs\|kb/files" tests/*.py`), one at a time; confirm PASS. Run `npm run lint` and the typecheck.
- [ ] **Step 5: Commit** `feat(console): friendly remove, retry and clear for knowledge uploads`.

### Task 6: Progress entry and retest rows

**Files:**
- Modify: `docs/PROGRESS.md` (one entry: branch `feat/knowledge-cleanup`, migration 0550, spec, plan, what changed, the Deviations above, deferred minors)
- Create: `docs/KNOWLEDGE_RETEST.md` (the owner's manual rows: delete a source that has a failed card, Try again, Remove, Clear all that failed, the knowledge assistant switched off, the portal view)

- [ ] **Step 1: Write both documents** in the voice of the existing entries (plain words, no secrets, synthetic examples).
- [ ] **Step 2: Commit** `docs: progress entry and retest rows for knowledge cleanup`.

---

## Self-review

- **Spec coverage:** migration and backfill (Task 1); closing jobs on delete, worker race (Task 2); dismiss, retry, cancel, bulk dismiss, delete without the agent switch, storage-sweep result, audit (Task 3); `processing` status, `cancelled`, plain notes, per-job actions, paused state (Tasks 4, 5); friendly confirm copy and Clear all (Task 5); runbook, API docs, OpenAPI (Tasks 3, 5); retest rows (Task 6). Spec items changed on purpose are listed under Deviations.
- **Type consistency:** `original_stored`, `deleted_file_ids`, `CONTENT_DELETED_ERROR`, `close_jobs_for_files`, `jobActions` and `attentionNote` have one definition each and are used with those names later.
- **Proportion:** six tasks for a spec of about 150 lines; no code bodies beyond signatures and test names.
