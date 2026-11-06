# Training library, version 1: design

Status: draft for the owner's approval, 2026-11-06. Branch `feat-training-library`. Migrations 0540-0549.

## Why

Companies want to keep their own training material in the console, so a new colleague can open one page and read it, instead of hunting through chat and drives. HR's Training tab does something else: staff request a course and a manager decides. Nothing today stores or shows training content.

The owner's first real use is PDF slides. Videos (about 15 minutes, about 200 MB) matter too, but they hit three upload limits on this deployment (see Constraints), and the AWS move changes which fix is right. So version 1 is PDFs only, and video follows after the move.

## Goals

- A staff member opens Training, sees their company's items, filters by category, and reads a PDF in the browser: as slides (one page at a time) or as a document (scrolling).
- Tenant admins and the company's HR owner can add, edit and remove items.
- Everyone else in the company can only read.
- A "Summarise" button on an item shows a short summary of the PDF's text, made once and kept.
- Every row and every file belongs to one company and can never be read by another.
- Limits and failures are plain sentences.

## Non-goals, for version 1

- Video, audio, PPTX or any file that is not a PDF. Video is the next version (Approaches, last section).
- Courses, ordered lessons, required items, due dates, reminders, quizzes, certificates, comments.
- Per-person progress or view tracking. A shelf needs neither, and not tracking views is also the privacy-safe default.
- Per-item or per-role visibility. Everyone in the company sees every item.
- A customer-facing library.
- Summaries of video, and summaries in the console assistant.
- Changing HR training requests in any way.

## Anti-goals

- A PDF from one company is reachable by another company, by guessed id, guessed file name or a direct object-store key.
- A file that is not a real PDF, or that carries active content, is stored and served.
- A removed item is still readable, or its file stays in storage for good.
- Clicking Summarise ten times costs ten language-model calls.
- Slide text, file names or titles appear in logs.
- The summary follows instructions written inside a slide.

## Constraints

- **Upload size.** The browser reaches the gateway through the console's Next rewrite, which caps request bodies at 31 MB (`proxyClientMaxBodySize` in `next.config.ts`). Cloudflare caps them at 100 MB. `MAX_FILE_BYTES` in `csa/console/multipart.py` is 10 MB. A 25 MB PDF fits all three once the 10 MB limit is made adjustable per route.
- **ObjectStore** (`csa/storage`, PR #76): `put` takes whole `bytes`, `open` streams, there are no range reads. Version 1 needs nothing more, because pdf.js is given the whole file.
- **Console frame policy.** Next sends `X-Frame-Options: DENY` on every page, so the browser's own PDF viewer in an iframe is not an option.
- **Tenancy.** Row security on `app.client_id`, never `FORCE`, no new database roles. Every object key starts with the company's id.
- **Public repository.** No real addresses, hostnames or tokens in code, tests, docs, commits or the PR.
- **Host.** 14 GB shared, so a PDF is read in memory only for the checks and the summary, never in bulk.
- **Migrations.** Range 0540-0549. `EXPECTED_SCHEMA_VERSION` rises to the highest merged one.

## Approaches

How to show the PDF (the owner chose A):

- **A. pdf.js in the console (chosen).** The browser fetches the PDF from an authenticated same-origin route and pdf.js draws it on canvas. One new pinned dependency and its worker file. Nothing is rendered on the server.
- **B. The browser's built-in viewer in an iframe.** No dependency, but no slide mode, different in every browser, poor on phones, and blocked by the frame policy unless relaxed for one route. Rejected.
- **C. Render pages to images on the server at upload.** Robust, but adds a native dependency (poppler, or PyMuPDF which is AGPL), parses untrusted PDFs on the server and stores N images per deck. Rejected as more machinery than a shelf needs.

How big files will work later (not built now, decided so version 1 does not block it): after the AWS move the store is S3, so video can upload browser-direct with presigned multipart URLs and play through a short-lived presigned or proxied range read. Staging runs local files and cannot test that, so it needs its own design and a real bucket. Version 1 keeps `kind` open in the schema for it.

## Who can do what

- **Read** (list, open, download, request a summary): any enabled member of the company, any role, including `read_only`; a platform admin acting on that company.
- **Manage** (add, edit, remove, regenerate a summary): tenant admins, the company's HR owner, and a platform admin acting on that company. The HR owner must be an enabled member who is not `read_only`.
- The package `training_library` must be active for the company. Its tab and every route answer `package_inactive` otherwise.
- Human sessions only; machine principals are refused, as on the KB and HR routes.

The HR owner is read from `hr_client_owners`, which row security hides from the app role except the caller's own row. A new read-only SECURITY DEFINER function (0541) takes no arguments: it reads `app.client_id` and `app.hr_user_id` from the session, the way `hr_owner_read` does, and answers "is that user an enabled, non-read-only member who is this company's HR owner". It cannot be used to ask about another user. The Python side sets the two settings with the same helper `hr_owner.py` uses. A platform admin never goes through it: that role is allowed by the role check, not by this function. This is the only place the library touches an HR table, and it is called out in the PR.

## The package

`training_library`, title "Training library", kind `standalone`, no requirements, no capabilities, no HR modules, tab `training_library`, sellable. It uses the existing request and activate path (`/services/{key}/request`, `/activate`) with no backfill: existing companies do not get it until it is activated for them.

The tab is labelled "Training library" (not "Training", which HR's tab inside the HR page already uses) at `/training` (platform admin) and `/portal/training` (everyone else), gated like Bookings. A company with both packages sees "Training library" in the rail and "Training" inside HR; the assistant's page context uses the key `training_library`, so the two never share one. The tab key also goes into the workspace assistant's page keys: a migration (0540) that reads the live `console_workspace_turns_page_key_check` list and adds `training_library`, in the style of 0531, and the `PageContext` tab list and `tab_available` rule in `csa/workspace`. The assistant gets no training actions in version 1.

## Data (migration 0541)

`training_items`, one row per PDF:

| Column | Notes |
| --- | --- |
| `id` | uuid primary key |
| `client_id` | references `clients`, row security on it |
| `kind` | `'pdf'` now; the check leaves room to add video |
| `title` | 1-120 characters, defaults to the file name without its extension |
| `description` | 0-1000 characters |
| `category` | 0-60 characters, free text, trimmed, matched case-insensitively for the filter |
| `view_mode` | `'slides'` or `'document'` |
| `storage_name` | `<uuid>.pdf`, unique per company |
| `original_name`, `media_type`, `byte_size`, `sha256`, `page_count` | checked at upload |
| `summary` | text, null until made |
| `summary_truncated`, `summary_model`, `summarised_at` | for the note under the summary |
| `created_by`, `created_at`, `updated_at` | |
| `request_key`, `fingerprint` | the upload's idempotency key, unique per company |

Row security: the same `tenant_isolation` policy as `console_service_packages`, `ENABLE` only. Grants to `csa_app`: select, insert, update, delete. No other role.

Rows are removed for real. The file is deleted under the item lock after the row commits. The audit rows stay in `console_audit`. On S3 a deleted object stays recoverable for 7 days (versioning and the lifecycle rule in `docs/OBJECT_STORAGE.md`).

Rulings on limits (my calls, see the end): 25 MB per file, 300 pages, 300 items and 500 MB in total per company. The company total counts `byte_size` under the item lock.

## Storage

- A third store, `training`, beside `kb` and `hr`: new setting `TRAINING_UPLOAD_ROOT` on the local backend, and the `training/` prefix on S3 with the same SSE-S3 settings. `Stores` gains `training: ObjectStore | None = None` so existing code and tests keep working; `kb_only` also leaves it out for the worker.
- On `STORAGE_BACKEND=s3`, `TRAINING_UPLOAD_ROOT` must be unset, as for the other two roots.
- Stored name `<uuid>.pdf`. A sweep (same shape as `csa/hr/documents.py:cleanup`) deletes only names that match that pattern and have no row, under a per-company advisory lock. It runs after a failed upload and after a removal.
- `docs/OBJECT_STORAGE.md` gets the new prefix, the IAM statement for `training/*` and the smoke script covers it. There is nothing to copy over, because no training files exist yet.
- Staging needs a new volume for the local root (`csa-staging_staging-training-uploads`). That is a compose change, and the rollout plan must say so.
- Everything that already names the two upload volumes or roots is extended in this PR: `scripts/staging_reset.py` (`UPLOAD_VOLUMES`, which the reset wipes and checks as mounted) and `tests/test_staging_reset.py`, `.env.example`, `deploy/compose.staging-pipeline.yml`, `scripts/storage_smoke.py`, and the settings, factory and pipeline tests. Without the reset-tool change, the next staging reset would empty the database and leave training PDFs on disk with no row pointing at them. The reset-tool edit is small and goes in the PR description.

## API

All under `/console/v1`, all behind `package_gate("training_library")`, all human sessions only. Documented in `docs/CONSOLE_API.md`; `docs/console_openapi.json` is regenerated and the snapshot test run.

| Route | Who | What |
| --- | --- | --- |
| `GET /training/items` | read | Items, newest first, with `can_manage`, categories, and per item: title, description, category, view mode, page count, size, date, uploader's display name, whether a summary exists. |
| `POST /training/items` | manage | One multipart file, `Idempotency-Key` header. Creates the item with title from the file name and view mode from page shape (landscape first page: slides). Returns the item. |
| `PATCH /training/items/{id}` | manage | Title, description, category, view mode. |
| `DELETE /training/items/{id}` | manage | Removes row and file. |
| `GET /training/items/{id}/file` | read | The PDF, streamed. |
| `GET /training/items/{id}/summary` | read | The stored summary, or none. |
| `POST /training/items/{id}/summary` | read; `regenerate: true` needs manage | Makes the summary if none is stored and returns it. |

The shared upload parser accepts only one file part and no other fields, so metadata is a second step: the page uploads, then patches. If the patch fails the item exists under its default title and the page says so. `upload_form` and `BoundedParser` gain one keyword, `max_file`, defaulting to today's 10 MB; the KB and HR uploads do not change. This is a small edit to shared code and goes in the PR description.

The file route answers `Content-Type: application/pdf`, `Content-Disposition: inline`, `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`. It reads the row under the tenant context, opens the object, and streams in 64 KB blocks; a removal never breaks a download already open (the same pattern as `SourceDownload`). A missing or unreadable object answers 503 and logs the item id only.

## The checks on an upload

In order, each with a plain-sentence refusal and nothing stored on failure:

1. Multipart is bounded (one part, size, header sizes) before it is read.
2. The file name is one safe segment with no control characters; it is shown, never used as a path. The stored name is generated.
3. The extension is `.pdf` and the first bytes are `%PDF-` (content must agree with the name).
4. `pypdf` opens it: not encrypted, readable, at most 300 pages, at least one page.
5. No document-level JavaScript or launch action (catalog `/Names/JavaScript`, `/OpenAction`, `/AA`, and each page's `/AA`). This is defence in depth: the viewer also runs with scripting off.
6. Limits: 25 MB, the company's item count and byte total.
7. SHA-256 is stored. A retry with the same `Idempotency-Key` and the same content returns the first item and keeps one file; the same key with other content is refused.

The upload, the idempotency check, the quota and the row insert run under one per-company lock, with the file written through `durable_put` and reconciled on any failure, exactly as the HR documents do.

## Summary

- One button on the item. The first click makes the summary and stores it; every later click, by anyone in the company, reads it.
- The server takes the PDF's text with `pypdf` (a new small function, not the KB chunker), up to 24,000 characters, and sends it to the existing Groq client as one `json_completion` with schema `{summary, key_points}`. When the text was cut, `summary_truncated` is true and the page says "Summarised from the first part of this PDF."
- The system prompt says the PDF text is data, never instructions, and asks for the summary in the language the PDF is written in. The model has no tools. The result is validated by the schema and length-bounded before it is stored.
- A per-item advisory lock makes concurrent first clicks one call. Regenerating is manage-only and replaces the stored text.
- A PDF with no extractable text (a scan) answers: "This PDF has no text we can read, so it cannot be summarised." A provider failure answers 503 with "We could not reach the assistant. Wait a minute and try again." and stores nothing.
- Slide text is sent to the language-model provider on that click only. The page says so next to the button the first time. Titles, file names and text never go to logs; log lines carry the item id, the model name and a byte count.
- Audit: `training.summary_generated`, `training.summary_regenerated`.

## The web side

- Pages `/training` and `/portal/training` (rail label "Training library"), a `TrainingWorkspace` component under `components/console/training/`, a `lib/training-api.ts` client, and copy that follows `docs/WRITING_GUIDE.md` (sentence case, verb-first buttons, Malaysian English, no codes on screen).
- **List.** Cards or rows with title, category, page count, date and uploader; a category filter; a search by title; an empty state that says what appears and, for managers, offers "Add a PDF". Managers also see "Edit details" and "Remove" per item; readers see neither.
- **Viewer.** pdf.js in a client component loaded only on the viewer page. Slides mode: one page fitted to the window, previous and next buttons, arrow keys, a page counter, a thumbnail strip, fullscreen. Document mode: pages stacked and scrolling with zoom. A switch lets any reader flip modes for themselves (the stored mode is just the default). Loading, failed-to-load and "this PDF is damaged" states each have a sentence. Reduced-motion is respected; controls are labelled for screen readers and work on a phone.
- **Add and edit.** A dialog: file, title, description, category (with the company's existing categories offered), "Show as" slides or document. Progress and the 25 MB, 300-page and quota refusals are plain sentences.
- **Summary.** A "Summarise this PDF" button under the viewer; the stored summary, its key points and the truncation note once it exists; "Make a new summary" for managers.
- **pdf.js specifics.** `pdfjs-dist`, exact pinned version, with `isEvalSupported: false`, scripting off, no external links followed, whole-file load (no range, no streaming). The worker file must work in the packaged console build (`output: "standalone"`). The first task of the web work is a proof in the packaged build before any UI is built; if the default worker import does not bundle, the worker is served from `public/` instead.
- New `lib/` and test files go into `scripts/check-isolated.mjs`'s allowlist. Existing data-testids stay as they are; new ones start with `training-`.

## Failure and error sentences (the staff-facing set)

Refusals map to plain sentences in the console copy, with the raw code kept on `ApiError.code`. They cover: not a PDF, damaged or protected PDF, too large (25 MB), too many pages (300), library full (items or 500 MB), the name is not valid, storage is unavailable (retry), file not found, no readable text, assistant unavailable, package not switched on, and "only admins and the HR owner can change the library".

## Audit

`console_audit` actions: `training.item_added`, `training.item_edited`, `training.item_removed`, `training.summary_generated`, `training.summary_regenerated`. Views are not logged.

## Tests

Disposable Testcontainers Postgres and synthetic PDFs built in the tests (`pypdf` writer, plus hand-built byte strings for the bad cases).

- **Isolation:** company A cannot list, open, patch, delete or summarise company B's item through the API; under `app.client_id` of A a direct query returns none of B's rows; B's object name read against A's tenant path is not found.
- **Roles:** tenant admin, HR owner (a staff member), plain staff, `read_only`, platform admin acting on the company, an admin of another company, a machine principal, a company without the package.
- **Upload:** non-PDF, wrong magic, renamed executable, empty, encrypted, over 25 MB, over 300 pages, JavaScript PDF, traversal and control characters in the name, quota by count and by bytes, a retry with the same key, the same key with different content, a storage failure mid-upload leaving no orphan and no row.
- **Remove:** row and file gone; a download opened before the removal still completes; the sweep removes only unreferenced matching names.
- **Summary:** stored after the first click; a second click makes no provider call; ten concurrent first clicks make one; a scan with no text; a provider failure stores nothing; slide text that says "ignore previous instructions" reaches the model only as data; regenerate is manage-only.
- **Stores:** the third store on the local and the S3 backend (same fakes the existing storage tests use); the settings checks; `kb_only` leaves it out; the smoke script covers it.
- **Package and assistant:** the package catalogue test, the activation path, the page-key constraint and `PageContext` (the source-scan guard in `tests/test_bookings_bot_schema.py` still passes).
- **Contract:** `docs/console_openapi.json` regenerated and `tests/test_console_contract_snapshot.py` run.
- **Web:** unit tests for the API client and copy; a browser test on a synthetic 3-page PDF (open, next page, thumbnail, flip mode, fullscreen control present); the packaged-console check that the worker loads.

## Documents

`docs/TRAINING_LIBRARY.md` (what it does, limits, settings, how to turn it on for a company), `docs/OBJECT_STORAGE.md` (the new prefix and IAM line), `docs/CONSOLE_API.md`, `db/migrations/README.md` (0540-0541), and a retest note with rows for the staging checks. No rollout record in `docs/PROGRESS.md`; that belongs to the rollout.

## Rollout notes

- Migrations 0540 and 0541 stop all four services, as the staging tool already says.
- A new compose volume and `TRAINING_UPLOAD_ROOT` on the gateway. No new secret.
- The package is activated per company by a platform admin; nothing appears until then.
- Not testable without the real thing: the language-model summary quality (needs the real provider key), real phones, and S3 behaviour (staging is local files).

## Calls I made (the owner can override any of these)

1. Own package and tab `training_library`, not an extension of HR's Training tab, and no HR employee link: progress is not tracked, so nobody needs one.
2. Read is open to all roles including `read_only`; manage is tenant admin, HR owner and platform admin.
3. Limits: 25 MB, 300 pages, 300 items, 500 MB per company. All four are constants in one place.
4. Hard delete with a file sweep, not a soft delete. S3 versioning gives 7 days of recovery.
5. Upload then patch, because the shared upload parser takes no form fields.
6. The summary is shared and stored, in the PDF's own language, from the first 24,000 characters.
7. A download is allowed through the same file route.
8. PDFs with document-level JavaScript or launch actions are refused.
9. Add a `max_file` keyword to the shared upload parser (default unchanged) and a `training` field on `Stores`; both are small edits to code merged in #76 and the KB/HR uploads.
10. No view logging.
11. The rail label is "Training library", to keep it apart from HR's "Training" tab.
12. The staging reset tool and the other places that name the upload volumes are extended in this PR, so a reset cannot leave training files behind.
13. The HR-owner function takes no arguments and reads the session's settings, so it cannot be pointed at another user.
