# Knowledge cleanup: remove, retry and clear, with friendly words

Status: design for the owner's approval, 2026-11-10. Pull request 1 of the business surfaces programme
(`2026-11-10-business-surfaces-programme.md`). Migrations 0550 to 0559.

## 1. Intent

A tenant Admin who deletes a knowledge source, or whose upload fails, is left with a "Needs attention"
card that offers nothing to click. The owner called it confusing for a tenant Admin. After this change
every card tells the Admin what happened in plain words and offers the one or two actions that make
sense, and nothing the Admin cannot clear can exist.

## 2. What the code does today (survey of 2e7ca4e)

Backend (`csa/console/kb_uploads.py`, `csa/agents/kb/`, `db/sql/kb/`):

- `kb_ingest_jobs.status` is one of queued, processing, done, partial, failed. Nothing deletes,
  dismisses, retries or cancels a job: `GET /kb/ingest-jobs` is the only job route.
- `DELETE /kb/files/{id}` soft-deletes the file (`deleted_at`) and calls `delete_document` only if the
  file already produced a document. `DELETE /kb/documents/{id}` writes a hash tombstone, soft-deletes
  linked files, hard-deletes chunks and the document. Neither touches the job row.
- Ingestion is fenced against deletion (`lifecycle.py` `check_ingestion`): a job that reaches a deleted
  file ends `failed` with "uploaded source was deleted or is no longer valid".
- Stored bytes are swept after a remove by `cleanup_after_remove`, which swallows errors;
  `POST /kb/storage/reconcile` is the manual sweep and does not touch job rows.
- All delete routes sit behind the `enabled` dependency (409 when the knowledge agent is off) although
  the screen blames "adding".
- The worker's `run_once` visits every pilot or active tenant and `claim()` requeues stale
  `processing` jobs before it checks the capability, so a stale job is already requeued. With the
  agent off it then sits `queued`, unclaimed, and reads "Reading" forever.

Console (`CSA_WebConsole/csa-console/web/lib/knowledge-view.ts`, `components/console/knowledge/*`):

- `jobChip` treats only `queued` and `running` as reading, `done` as nothing and everything else as
  "Needs attention". The backend status is `processing`, not `running`, so a job being read shows
  "Needs attention", and `pollingNeeded` stays false so the page does not refresh.
- An attention card offers only "Choose the file again". Delete buttons exist only for live files and
  documents. A failed file never got a document, and once its source is deleted `files_list.sql`
  excludes it, so the card has no actions at all. Its note still says "Add the file again".
- Jobs are never archived; they leave the list only when pushed past the 20-item limit.
- The delete confirmation and success copy describe documents and duplicates, not a failed job.

Root causes, in order of impact: no way to remove a job; deleting a source does not close its job;
the `processing` status is unknown to the screen; stale jobs are only reaped for enabled tenants.

## 3. Goals, non-goals, anti-goals

Goals

- Every card in "Needs attention" has a plain sentence saying why and the right actions: **Try again**
  when the original is still stored, **Remove** always.
- Deleting a source closes its jobs in the same transaction, so deleting never creates an attention
  card.
- A "Clear all that failed" action for several cards.
- A job being read shows "Reading" and the page refreshes by itself until it finishes.
- Existing stuck cards on every tenant disappear on upgrade (one-time data fix in the migration).
- Delete works while the knowledge assistant is switched off.

Non-goals

- Re-syncing a Google Sheet (`kb_sources` is unused today).
- A new storage backend or changes to how chunks and embeddings are stored.
- Changing what the assistant answers from the knowledge base.

Anti-goals

- Deleting a job must never delete a document or chunks that other files still use.
- No silent failure: if the stored bytes cannot be removed the Admin is told, and the retry path stays
  available.

## 4. Approaches

A. **Hard-delete job rows when dismissed.** Simplest, but loses the history that audit and support use
   to answer "what happened to my upload".

B. **Keep the row, add a `dismissed_at` timestamp and a `cancelled` status (recommended).** The list
   query hides dismissed rows; the row stays for audit; one filter and one new status. A migration
   extends the status check constraint and backfills.

C. **Auto-expire failed jobs after N days.** Hides the problem rather than fixing it and leaves the
   Admin without an action in the meantime. Rejected as the only fix; the reaper in B still ends
   stuck "processing" jobs.

Chosen: B.

## 5. Design

Data (migration 0550; read the live constraint with `pg_get_constraintdef` and add to it):

- `kb_ingest_jobs.status` also allows `cancelled`.
- `kb_ingest_jobs.dismissed_at timestamptz` (null = visible).
- One-time backfill in the same migration: set `cancelled` and `dismissed_at` on every `failed` or
  `partial` job whose files are all soft-deleted, and on every `queued` or `processing` job whose
  `locked_at` is older than the stale limit and whose files are all deleted.

API (the job routes sit in `csa/console/router.py` next to `GET /kb/ingest-jobs`, with that router's
package gate; the file and document routes stay in `csa/console/kb_uploads.py`; writer role and an
audit entry on every action):

- `POST /kb/ingest-jobs/{id}/dismiss`: sets `dismissed_at` for a finished job (`failed`, `partial`,
  `cancelled`, `done`); 409 for a running job.
- `POST /kb/ingest-jobs/{id}/retry`: only for `failed` or `partial` jobs whose files still exist;
  re-queues from the stored bytes (no new upload), resets `attempts`, clears `error`; 409 with a
  reason code `original_removed` otherwise.
- `POST /kb/ingest-jobs/{id}/cancel`: for `queued` jobs only (release the lock, set `cancelled`). A
  job a worker is already reading cannot be cancelled by the Admin; it finishes, fails or is requeued
  by the existing reaper.
- `POST /kb/ingest-jobs/dismiss-failed`: dismisses every `failed`, `partial` and `cancelled` job of
  the tenant; returns the count.
- `remove_file` and `delete_document` close every job that lists the file in `files[].file_id`: a job
  whose files are all deleted becomes `cancelled` and dismissed; a job with other live files keeps
  them and gets a per-file note in `meta.results`.
- The delete routes no longer depend on the `enabled` check (they only remove); upload and retry still
  do. `cleanup_after_remove` returns whether the sweep succeeded; the two delete routes change from 204
  to 200 with a small body (`{"storage_cleanup": "done" | "pending"}`), updated in the console, the
  API doc and the OpenAPI file in the same pull request; a pending sweep is left for the existing
  reconcile, instead of being swallowed.
- A job left `queued` because the knowledge assistant is off is shown as "Paused: the knowledge
  assistant is switched off" rather than "Reading", and the Admin can cancel it (the requeue of stale
  `processing` jobs already works and stays as it is).

Console:

- `jobChip`, `jobStateText`, `progressWord` and `pollingNeeded` handle `processing` (the one
  status-name fix) and `cancelled`.
- A card's wording comes from its own state, in the voice of `docs/CONSOLE_VOICE.md`. Draft, for the
  builder to refine: failed with the file stored: "We couldn't read this file. You can try again or
  remove it."; failed with the file gone: "The original was removed, so this can't be tried again.
  Remove it."; partial: "Some of this file couldn't be read. The rest is in your assistant."
- Actions: **Try again** (retry), **Remove** (dismiss) with a confirmation that says exactly what
  happens ("This takes the message off your list. Nothing your assistant knows changes."), and a
  **Clear all that failed** link above the list when more than one card needs attention.
- Deleting a document or file uses copy per case and a success message that matches (a job is not "a
  file your assistant no longer uses").
- The Maintenance section's storage sweep reports what it removed and how many bytes are still
  pending.

## 6. Tests (named for the builder)

- Backend: dismiss, retry (file present, file removed), cancel, bulk dismiss, closing jobs on file and
  document delete (all files deleted, some files live), reaper for a disabled tenant, delete while the
  agent is off, the migration backfill on a database with stuck rows, RLS (another tenant's job is
  404), audit rows written.
- Console: card state table (every status and file-present combination gives the expected words and
  actions), `processing` shows "Reading" and keeps polling, confirm copy, "Clear all that failed",
  portal twin. A Playwright test for the delete-then-card-disappears path. Grep and fix every browser
  stub of the KB job list.
- Docs: `docs/CONSOLE_API.md`, the console OpenAPI file, the runbook paragraph on stuck jobs.

## 7. Left to the builder

Exact copy within the voice guide; where "Clear all that failed" sits; whether dismissed jobs stay
visible in an "Older" disclosure (recommended: no, audit has them); the stale-limit value reused from
the reaper.

## 8. Owner decisions to confirm

1. A dismissed job keeps its row for audit (approach B), not hard-deleted.
2. Delete stays available when the knowledge assistant is switched off.
3. The migration cleans existing stuck cards on every tenant without asking each Admin.

## 9. Builder check (2026-11-10): calls made by the designer

Change any of these on request; none needs the owner.

- **Delete while a worker is reading the job:** `remove_file` and `delete_document` mark the job
  `cancelled` in their transaction. The worker's finish and release only update rows still
  `processing`, so its result is ignored, and the existing ingestion fence (`check_ingestion`, hash
  tombstone) stops it from storing a document for a removed source. The plan adds a test for exactly
  this race (job processing, file removed, worker finishes).
- **Retry:** resets `created_at` and `attempts` and clears `error`, so the tombstone fence compares
  against the new time. A job that failed because its content was deleted after it was queued is not
  retryable (`original_removed`); the card says so and offers Remove only.
- **"The original is still stored":** decided per file by `file_id` (live row in `kb_upload_files`).
  A `files[]` entry with no `file_id` (path only, used by the development ingest script) is treated as
  live for retry, checked against the object store, and is never cancelled by the migration backfill.
- **Retry of a `partial` job** re-runs only the files whose result failed; per-file results are merged
  into `meta.results` instead of overwritten (the plan changes the finish SQL accordingly).
