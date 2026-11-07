# Training library, version 1: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staff can open a "Training library" tab, read their company's PDFs as slides or as a document, and tenant admins and the HR owner can add, edit and remove them, with a stored one-click summary.

**Architecture:** A new `csa/training/` package (PDF checks, service, router) on a third object store (`training/` prefix, `TRAINING_UPLOAD_ROOT` locally), one row-secured table `training_items`, and a new standalone package `training_library`. The console shows it through pdf.js, which draws the authenticated PDF bytes on canvas. The summary reuses the existing Groq client and `pypdf`.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, Postgres 16 row security, `pypdf` (pinned already), Next.js console (`CSA_WebConsole/csa-console/web`), `pdfjs-dist` (new, exact pin), pytest + Testcontainers, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-06-training-library-design.md` (read it first; this plan only records decisions the spec leaves to the implementer).

## Global Constraints

- Limits, in one module `csa/training/limits.py`: 25 MB per file, 300 pages, 300 items and 500 MB in total per company, 24,000 characters of text for a summary. Title 1-120, description 0-1000, category 0-60 characters.
- Migrations 0540 and 0541 only (range 0540-0549). `EXPECTED_SCHEMA_VERSION` in `apps/gateway/main.py` becomes `"0541"`; tests import the constant.
- Row security: `ENABLE` only, never `FORCE`; policy on `app.client_id`; no new database roles; grants to `csa_app` only.
- Any migration touching the assistant's page-key constraint reads the live list (`pg_get_constraintdef`), never redefines it (style of `0531_booking_assistant_action.sql`; `tests/test_bookings_bot_schema.py` scans for this).
- Public repository: synthetic data only; no real addresses, hostnames, tokens in code, tests, docs, commits or the PR.
- Console copy follows `docs/WRITING_GUIDE.md`: sentence case, verb-first buttons, Malaysian English, no codes on screen (raw code stays on `ApiError.code`).
- Refusals are `HTTPException(status, <snake_case code>)`; the web copy maps each code to a sentence.
- Existing `data-testid`s stay; new ones start with `training-`. New `lib/` and test files go into `scripts/check-isolated.mjs` (file list near line 64 and 150, test runs near line 273).
- Python edits: write through a heredoc or script, run `ruff format`, then `git diff` for unrelated reflow (an editor hook reflows to 120 columns).
- Testing on this host: run only the test files you change, one invocation each, through the scratchpad wrapper that exports `NO_LOCAL_ENV=1`, `ENV=ci`, `PATH=/tmp/fourstacks-toolbin:$PATH` then `exec "$@"`; no `ENV=` prefixes or compound commands. Web checks: `npm run lint`, `npm run typecheck` (with a dummy `CSA_CONSOLE_API_ORIGIN`), `npm test`; and `make lint`. The full suite runs only in PR CI.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Failure modes the spec implies that no single task's tests would catch by themselves; each has its test in the named task.

1. Two uploads race for the last quota slot: exactly one succeeds, the other gets `training_library_full`, no orphan file (Task 7).
2. An item is removed while its summary is being made or its file is open: the summary call answers 404 and never recreates the row; an open download completes (Tasks 7, 8).
3. The HR owner is moved or removed: the old owner loses manage rights on the next request, with no cached decision (Task 7).
4. Text extraction on a large PDF stops at the character cap instead of reading every page, and a decompression-heavy page cannot hang the request (Task 8).
5. The viewer meets a damaged, password-protected or missing file at display time (the checks passed at upload, the object later fails): each shows its own sentence, not a blank page or a console error (Tasks 7, 11).

---

### Task 1: Third object store, setting and smoke script

**Files:**
- Modify: `csa/core/settings.py` (near lines 99-106, 285-335), `csa/storage/factory.py`, `scripts/storage_smoke.py`, `.env.example`, `docs/OBJECT_STORAGE.md`
- Test: `tests/test_storage_factory.py`, `tests/test_settings.py`, `tests/test_storage_smoke.py`

**Interfaces:**
- Produces: `Settings.TRAINING_UPLOAD_ROOT: Path | None = None`; `Stores.training: ObjectStore | None = None` (a defaulted last field); `build_stores(settings)` returns it (local: `LocalObjectStore(TRAINING_UPLOAD_ROOT)`; s3: `S3ObjectStore` with prefix `"training"`); `kb_only(stores)` leaves it `None`; `verify_stores` also checks it.

- [ ] **Step 1: Write failing tests** in the three files: `test_training_root_blank_is_unset`; `test_training_root_must_not_overlap_kb_or_hr_root` (same message shape as the existing HR overlap test, covering both directions and all three pairs); `test_s3_backend_refuses_training_root` (message `STORAGE_BACKEND=s3 conflicts with TRAINING_UPLOAD_ROOT; unset TRAINING_UPLOAD_ROOT`); `test_build_stores_local_has_training_store`; `test_build_stores_s3_uses_training_prefix` (a key written lands under `training/<tenant>/`); `test_kb_only_drops_training`; `test_verify_stores_checks_training`; smoke script test asserts a `PASS` line for the training store.
- [ ] **Step 2: Run each file; expect FAIL** (unknown field/attribute).
- [ ] **Step 3: Implement** in settings (add the field, extend `_blank_upload_root_is_unset`, the s3 conflict loop and the overlap check to all three roots), factory (`Stores` field, both backends, `kb_only`, `verify_stores`), smoke script (one more store in its loop), `.env.example` (blank `TRAINING_UPLOAD_ROOT=`).
- [ ] **Step 4: Update `docs/OBJECT_STORAGE.md`:** the setting row, layout line `training/<client_id>/<name>`, the `training/*` statements in the IAM policy (ListBucket prefix list and the object actions), a note that no existing files need copying.
- [ ] **Step 5: Run the three test files and `make lint`; expect PASS.**
- [ ] **Step 6: Commit** `feat(storage): training object store`.

### Task 2: Staging volume, reset tool and rollout plumbing

**Files:**
- Modify: `deploy/compose.staging-pipeline.yml`, `scripts/staging_reset.py:73` (`UPLOAD_VOLUMES`), `tests/test_staging_reset.py` (lines 194, 445, 462, 574, 811 pin the two-volume tuple), `tests/test_staging_pipeline.py`

**Interfaces:**
- Produces: volume `csa-staging_staging-training-uploads` mounted at `/var/lib/csa/training-uploads` on the gateway with `TRAINING_UPLOAD_ROOT=/var/lib/csa/training-uploads`; `UPLOAD_VOLUMES = ("csa-staging_staging-uploads", "csa-staging_staging-hr-uploads", "csa-staging_staging-training-uploads")`.

- [ ] **Step 1: Update the pinned tests first** to expect three volumes (the tuple assertion, the parametrised wipe test, the mounted-volume sorted comparison, the tuple unpacking at line 811), and a pipeline test asserting the gateway has the new volume and env. Run `tests/test_staging_reset.py` and `tests/test_staging_pipeline.py`; expect FAIL.
- [ ] **Step 2: Implement** the compose volume, mount and env, and the tuple. Follow how the HR volume is declared (mount mode, uid).
- [ ] **Step 3: Run both test files; expect PASS.** Check `git diff scripts/staging_reset.py` is one line.
- [ ] **Step 4: Commit** `feat(staging): training uploads volume and reset coverage`. Note in the commit body that the destructive reset tool's volume list changed.

### Task 3: Per-route upload size in the shared parser

**Files:**
- Modify: `csa/console/multipart.py`
- Test: `tests/test_console_multipart.py`

**Interfaces:**
- Produces: `upload_form(request, *, max_files=3, max_body=None, field="files", max_file: int = MAX_FILE_BYTES)`; `BoundedParser(..., max_file: int)`; the 413 message reads `file exceeds {n} MB` computed from `max_file` (10 MB callers see the same text as today).

- [ ] **Step 1: Failing tests:** `test_max_file_default_is_unchanged_and_message_says_10_mb`; `test_max_file_raises_the_per_part_limit` (a 12 MB part passes with `max_file=25*2**20`); `test_max_file_still_refuses_larger_part` (message `file exceeds 25 MB`).
- [ ] **Step 2: Run `tests/test_console_multipart.py`; expect FAIL.**
- [ ] **Step 3: Implement** the keyword end to end; replace the module constant use in `on_part_data` with `self.max_file`.
- [ ] **Step 4: Run it again, then `tests/test_console_kb_uploads.py` once; expect PASS** (callers unchanged).
- [ ] **Step 5: Commit** `feat(uploads): per-route file size limit`; the commit and PR text say this edits shared code.

### Task 4: Migrations 0540 and 0541, schema tests, assistant tab key

**Files:**
- Create: `db/migrations/0540_training_page_key.sql`, `db/migrations/0541_training_library.sql`, `tests/test_training_schema.py`
- Modify: `db/migrations/README.md`, `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION = "0541"`), `csa/workspace/contracts.py` (`TabKey` adds `"training_library"`), `csa/workspace/rules.py` (`TAB_GATES["training_library"] = (frozenset({"training_library"}), ACTIVE)`, `TAB_LABELS["training_library"] = "Training library"`)
- Test: `tests/test_workspace_contracts.py` and `tests/test_bookings_bot_schema.py` (run, update only if they enumerate tabs)

**Interfaces:**
- Produces: table `public.training_items(id uuid pk default gen_random_uuid(), client_id uuid not null references clients(id), kind text not null check (kind in ('pdf')), title text not null check (char_length between 1 and 120), description text not null default '' check (char_length <= 1000), category text not null default '' check (char_length <= 60), view_mode text not null check (view_mode in ('slides','document')), storage_name text not null, original_name text not null, media_type text not null, byte_size bigint not null check (byte_size > 0), sha256 text not null, page_count int not null check (page_count > 0), summary text, summary_truncated boolean not null default false, summary_model text, summarised_at timestamptz, created_by uuid, created_at timestamptz not null default now(), updated_at timestamptz not null default now(), request_key uuid not null, fingerprint text not null, unique (client_id, storage_name), unique (client_id, request_key))`; policy `tenant_isolation` as in `0450`; `GRANT SELECT, INSERT, UPDATE, DELETE ... TO csa_app` after `REVOKE ALL ... FROM PUBLIC, csa_ro, csa_identity`.
- Produces: `public.training_is_hr_owner() RETURNS boolean`, SECURITY DEFINER, `SET search_path=pg_catalog,public,pg_temp`, no arguments; true only when `app.client_id` and `app.hr_user_id` are set and `hr_client_owners` has that pair and the user and membership are enabled and the role is not `read_only`; `REVOKE ALL ... FROM PUBLIC; GRANT EXECUTE ... TO csa_app`.

- [ ] **Step 1: Write `tests/test_training_schema.py`** (Testcontainers, helpers from `tests/test_bookings_schema.py` and `tests/conftest.py`): `test_table_is_row_secured_and_not_forced`; `test_company_b_cannot_see_or_write_company_a_rows` (select returns none under B's `app.client_id`; insert with A's id under B fails); `test_checks_refuse_bad_values` (title empty/121, view_mode `'video'`, byte_size 0); `test_request_key_unique_per_company`; `test_hr_owner_function_true_for_owner_false_for_others` (owner true; non-owner false; owner whose membership is `read_only` false; owner disabled false; settings unset false); `test_hr_owner_function_pinned_to_csa_app` (PUBLIC has no EXECUTE); `test_page_key_check_keeps_every_earlier_key_and_adds_training_library` (reads the live constraint, asserts `bookings`, `business_orders`, `workspace_home` still present).
- [ ] **Step 2: Run it; expect FAIL** (no table).
- [ ] **Step 3: Write 0540** reading the live `console_workspace_turns_page_key_check` definition and adding `'training_library'` to its list (copy the approach in 0531, including the rollback comment); **write 0541** to the interface above.
- [ ] **Step 4: Update `TabKey`, `TAB_GATES`, `TAB_LABELS`, the README range lines and `EXPECTED_SCHEMA_VERSION`.**
- [ ] **Step 5: Run `tests/test_training_schema.py`, `tests/test_bookings_bot_schema.py`, `tests/test_workspace_contracts.py`, each once; expect PASS** (`test_workspace_contracts.py` compares tab labels with `lib/navigation.ts`: if it fails on the label, finish Task 10's navigation entry first or stub the label there, then rerun).
- [ ] **Step 6: Commit** `feat(training): schema, HR-owner check and assistant tab key`.

### Task 5: The `training_library` package

**Files:**
- Modify: `csa/console/packages.py`; tests that count or list packages (`tests/test_console_packages.py`, `tests/test_console_services.py`, `tests/test_staging_roster.py`, `tests/test_seed_staging.py`: run each, fix only counts and lists)

**Interfaces:**
- Produces: `Package(key="training_library", title="Training library", summary="Keeps your company's training slides in one place, so your team can read them in the browser.", kind="standalone", requires=(), capabilities=(), hr_modules=(), email=False, tabs=("training_library",), sellable=True)`, placed after `bookings`.

- [ ] **Step 1: Failing test** in `tests/test_console_packages.py`: `test_training_library_is_a_sellable_standalone_with_no_capabilities` and `test_training_library_activates_without_customer_support` (via the request/activate path, no backfill: an existing company has no row until activated).
- [ ] **Step 2: Run it; expect FAIL. Step 3: add the entry. Step 4: run each affected file once; expect PASS.**
- [ ] **Step 5: Commit** `feat(training): training_library package`.

### Task 6: PDF checks and text extraction

**Files:**
- Create: `csa/training/__init__.py` (empty docstring), `csa/training/limits.py`, `csa/training/pdf.py`, `tests/test_training_pdf.py`

**Interfaces:**
- Produces in `limits.py`: `MAX_FILE_BYTES = 25 * 1024 * 1024`, `MAX_PAGES = 300`, `MAX_ITEMS = 300`, `MAX_TOTAL_BYTES = 500 * 1024 * 1024`, `SUMMARY_MAX_CHARS = 24_000`.
- Produces in `pdf.py`: `class PdfRefused(Exception)` with `.code: str`; `@dataclass(frozen=True) class PdfInfo: page_count: int; landscape: bool`; `def check_pdf(data: bytes, file_name: str) -> PdfInfo`; `def extract_text(data: bytes, max_chars: int) -> tuple[str, bool]` (text, truncated).
- Refusal codes: `training_name_invalid`, `training_not_pdf`, `training_pdf_damaged`, `training_pdf_protected`, `training_pdf_too_many_pages`, `training_pdf_active_content`.

- [ ] **Step 1: Failing tests** (synthetic PDFs from the `pypdf` writer; hand-built byte strings for bad cases): `test_valid_pdf_reports_pages_and_landscape`; `test_portrait_is_not_landscape`; `test_wrong_extension_refused`; `test_pdf_extension_with_exe_bytes_refused` (`MZ` header named `x.pdf` gives `training_not_pdf`); `test_truncated_pdf_is_damaged`; `test_encrypted_pdf_is_protected`; `test_301_pages_refused`; `test_javascript_in_names_tree_refused`; `test_open_action_javascript_refused`; `test_page_additional_actions_refused`; `test_name_with_slash_backslash_nul_control_or_over_255_refused`; `test_unicode_name_with_spaces_accepted`; `test_extract_text_stops_at_cap_and_reports_truncated` (300 pages of text, spy shows pages after the cap are not read); `test_extract_text_scan_returns_empty_string`.
- [ ] **Step 2: Run; expect FAIL. Step 3: implement** (`pypdf.PdfReader(io.BytesIO(data))`, strict off; catalog checks on `/Root`; page loop for `extract_text` breaks once `max_chars` is reached; every pypdf exception becomes `PdfRefused("training_pdf_damaged")`).
- [ ] **Step 4: Run `tests/test_training_pdf.py`, `make lint`; expect PASS. Step 5: commit** `feat(training): PDF checks and text extraction`.

### Task 7: Service, router, wiring (list, add, edit, remove, file)

**Files:**
- Create: `csa/training/models.py`, `csa/training/access.py`, `csa/training/service.py`, `csa/training/api.py`, `db/sql/training/{list,get,insert,patch,delete,usage,names,find_key,lock}.sql`, `tests/test_training_api.py`
- Modify: `csa/console/router.py` (`build_router(..., training_store: ObjectStore | None = None)` and `router.include_router(build_training_router(training_store))` beside the KB line), `apps/gateway/main.py` (pass `training_store=stores.training`), `tests/test_console_sessions.py` (a `training_store` fixture = `LocalObjectStore(tmp_path / "training")`, added to the `sessions` fixture and passed to `build_router`)

**Interfaces:**
- Produces in `models.py`: `ItemOut(id: UUID, title: str, description: str, category: str, view_mode: Literal["slides","document"], page_count: int, byte_size: int, created_at: AwareDatetime, uploaded_by_name: str | None, has_summary: bool)`; `ItemList(items: list[ItemOut], categories: list[str], can_manage: bool)`; `ItemPatch(title: str | None, description: str | None, category: str | None, view_mode: ... | None)` (`extra="forbid"`, same length limits, title trimmed and non-empty).
- Produces in `access.py`: `async def can_manage(conn, actor: ConsoleActor, client_id: UUID) -> bool` (platform admin and tenant admin true; staff true only if `SELECT public.training_is_hr_owner()` after setting `app.hr_user_id` with `console/invite_facts_context.sql`; `read_only` false).
- Produces in `service.py`: `class TrainingLibrary(pool: AsyncConnectionPool, store: ObjectStore)` with `list_items(actor, client_id) -> ItemList`, `add_item(actor, client_id, request, key: UUID) -> ItemOut`, `patch_item(actor, client_id, item_id, patch) -> ItemOut`, `remove_item(actor, client_id, item_id) -> None`, `open_file(client_id, item_id) -> StreamingResponse`; `async def cleanup(pool, store, client_id) -> int` (deletes only names matching `[a-f0-9-]{36}\.pdf(\.part)?` with no row, under the company lock).
- Produces in `api.py`: `build_training_router(store: ObjectStore | None) -> APIRouter` with `Depends(package_gate("training_library"))`, human sessions only, routes exactly as in the spec's API table (summary routes come in Task 8). Detail codes: `training_storage_unavailable` (503), `training_not_found` (404), `training_forbidden` (403), `training_file_too_large` (413), `training_library_full` (409), `training_key_conflict` (409), plus the PDF codes from Task 6 as 422.
- Consumes: Task 3 `upload_form(..., max_file=MAX_FILE_BYTES)`, Task 4 table and function, Task 6 `check_pdf`.

- [ ] **Step 1: Write failing tests** (world from `tests.test_console_sessions`, `activate(world, tenant, "training_library")`, synthetic PDFs from Task 6 helpers): `test_every_route_needs_the_package` (`package_inactive` before activation); `test_roles_matrix` (admin, HR-owner staff via `INSERT INTO hr_client_owners`, plain staff, read_only, platform admin with `client_id`, admin of another company, machine token: manage routes 200/201/204 or 403 `training_forbidden`; reading 200 for the human roles; machine token refused); `test_list_shape_and_can_manage_flag`; `test_upload_stores_row_and_file_and_audits` (`training.item_added` in `console_audit`, file at `<training root>/<tenant>/<uuid>.pdf`); `test_title_defaults_to_file_stem_and_landscape_picks_slides`; `test_patch_changes_fields_and_audits_item_edited`; `test_upload_refusals` (parametrised over the Task 6 bad files, 25 MB + 1, empty, two files, a non-multipart body: status and code each; nothing stored, no row); `test_retry_same_key_returns_first_item_one_file`; `test_same_key_other_content_conflicts`; `test_storage_failure_leaves_no_row_and_no_orphan` (store whose `put` raises `StorageError`: 503 `training_storage_unavailable`, list_names empty); `test_quota_by_count_and_by_bytes` (constants monkeypatched small); `test_last_slot_race_one_wins` (two `asyncio.gather`ed uploads: one 201, one 409, one file); `test_remove_deletes_row_and_file_and_audits`; `test_open_download_survives_removal`; `test_file_route_headers` (`application/pdf`, `inline`, `private, no-store`, `nosniff`, `sandbox`); `test_missing_object_answers_503_and_logs_id_only` (captured log has the item id and no title or file name); `test_company_b_cannot_read_patch_delete_or_open_company_a_item` (404 each); `test_removed_hr_owner_loses_manage_on_next_request`; `test_cleanup_removes_only_unreferenced_matching_names` (a stray `notes.txt` survives).
- [ ] **Step 2: Run `tests/test_training_api.py`; expect FAIL.**
- [ ] **Step 3: Implement** models, access, SQL, service and router, following `csa/hr/documents.py` (upload, lock, `durable_put`, reconcile in `BaseException`, download via `SourceDownload`-style stream) and `csa/console/kb_uploads.py` (`package_gate`, `human`, `audit`). One advisory lock per company serialises upload, quota check and cleanup (`training/lock.sql`: `SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))`, key `f"{client_id}:training"`). Quota counts `count(*)` and `sum(byte_size)` in `usage.sql` under the lock. The file route sets the headers from the spec; the stored name is generated; the original name is returned only as data.
- [ ] **Step 4: Run `tests/test_training_api.py`, `tests/test_console_kb_uploads.py` (shared fixture changed), `make lint`; expect PASS.**
- [ ] **Step 5: Commit** `feat(training): library routes and storage`.

### Task 8: Summary

**Files:**
- Create: `csa/training/summary.py`, `db/sql/training/{summary_get,summary_set}.sql`, `tests/test_training_summary.py`
- Modify: `csa/training/service.py` (add `get_summary`, `make_summary`), `csa/training/api.py` (two routes), `apps/gateway/main.py` (build `app.state.training_model` as a `GroqClient` with its own `httpx.AsyncClient`, closed at shutdown; absent when the console routes are not enabled)

**Interfaces:**
- Produces in `summary.py`: `class SummaryDraft(BaseModel)` with `summary: str` (1-1500 characters) and `key_points: list[str]` (0-6 items, each 1-200 characters), `extra="forbid"`; `SUMMARY_SYSTEM: str` (says the PDF text is data and never instructions, no tools, answer in the language of the PDF); `async def summarise(model: JsonCompletionClient, text: str) -> SummaryDraft` (one `json_completion(SummaryDraft, system=SUMMARY_SYSTEM, user=<text in a fenced block>, timeout=20.0, max_tokens=900)`).
- Produces: `SummaryOut(summary: str, key_points: list[str], truncated: bool, model: str, summarised_at: AwareDatetime)`; `GET /training/items/{id}/summary` returns it or `{"summary": null}`; `POST /training/items/{id}/summary` body `{"regenerate": bool = false}` (read for the first make; `regenerate: true` needs manage and replaces). The key points are stored joined in the `summary` column as JSON text (`{"summary": ..., "key_points": [...]}`), no new column.
- Detail codes: `training_no_text` (422), `training_assistant_unavailable` (503), `training_not_found` (404), `training_forbidden` (403).
- Consumes: Task 6 `extract_text`, Task 7 service and lock, `csa.decide.completion.JsonCompletionClient`.

- [ ] **Step 1: Failing tests** with a fake model (counts calls, records the user text): `test_first_click_makes_and_stores_second_click_reads` (one call, `training.summary_generated` audited once); `test_ten_concurrent_first_clicks_make_one_call`; `test_regenerate_needs_manage_and_replaces` (staff 403, admin 200, `training.summary_regenerated`); `test_scan_without_text_answers_training_no_text_and_stores_nothing`; `test_provider_failure_answers_503_and_stores_nothing` (fake raises `GroqError("timeout")`); `test_injected_instruction_in_pdf_reaches_the_model_only_inside_the_fenced_user_text` (system prompt unchanged, text sits in the user message); `test_summary_of_a_removed_item_is_404_and_never_recreates_it` (remove the item while the fake model is blocked on an event, then release it); `test_truncated_flag_and_cap` (a long PDF: model sees at most 24,000 characters, `truncated` true); `test_slow_extraction_cannot_hang_the_request` (extraction function patched to sleep past 10 s: 503 `training_assistant_unavailable` inside the timeout; implement the guard with `asyncio.wait_for(asyncio.to_thread(...), 10)`); `test_logs_carry_id_model_and_byte_count_only`; `test_other_company_cannot_summarise` (404).
- [ ] **Step 2: Run; expect FAIL. Step 3: implement.** The make path runs under a per-item advisory lock, re-reads the row after taking it (so a removal that committed first yields 404), writes with `summary_set.sql` that `UPDATE`s only an existing row, and never inserts.
- [ ] **Step 4: Run `tests/test_training_summary.py`, `tests/test_training_api.py`, `make lint`; expect PASS. Step 5: commit** `feat(training): stored one-click summary`.

### Task 9: API contract and docs

**Files:**
- Modify: `docs/console_openapi.json` (generated), `docs/CONSOLE_API.md`
- Create: `docs/TRAINING_LIBRARY.md`

- [ ] **Step 1: Run `.venv/bin/python -m scripts.export_console_openapi`** from the main checkout's interpreter with the worktree as cwd; review that only `/training/...` paths and schemas were added.
- [ ] **Step 2: Add a "Training library" section to `docs/CONSOLE_API.md`** (routes, who may call, codes, limits, the two-step upload) in the style of the Bookings section; write `docs/TRAINING_LIBRARY.md` (what it does, limits, settings `TRAINING_UPLOAD_ROOT`, how a platform admin turns it on, the HR-owner dependency, what is out of scope).
- [ ] **Step 3: Run `tests/test_console_contract_snapshot.py`; expect PASS.**
- [ ] **Step 4: Commit** `docs(training): API contract and guide`.

### Task 10: Web foundation: pdf.js proof, client, copy, navigation, pages

**Files:**
- Create: `lib/training-api.ts`, `lib/training-view.ts`, `lib/training-slides.ts`, `tests/training-api.test.mjs`, `tests/training-view.test.mjs`, `tests/training-slides.test.mjs`, `app/(admin)/training/page.tsx`, `app/(admin)/training/[id]/page.tsx`, `app/portal/training/page.tsx`, `app/portal/training/[id]/page.tsx` (all under `CSA_WebConsole/csa-console/web/`)
- Modify: `package.json`, `package-lock.json`, `lib/navigation.ts` (`NavKey` adds `"training_library"`; `gate.training_library = ["training_library"]`; entry `{ key: "training_library", label: "Training library", href: to("training") }` after Bookings), `scripts/check-isolated.mjs`, `lib/workspace-api.ts` and `lib/page-context*.ts` if they enumerate tab keys (grep `"bookings"`), `tests/nav-visibility.test.mjs`

**Interfaces:**
- Produces in `training-api.ts`: types `TrainingItem` (mirrors `ItemOut`), `TrainingList` (mirrors `ItemList`), `TrainingSummary`; `listItems(): Promise<TrainingList>`, `uploadItem(file: File, key: string): Promise<TrainingItem>` (multipart through the existing `FormData` path in `lib/api.ts`, `Idempotency-Key` header), `patchItem(id, patch)`, `removeItem(id)`, `fileUrl(id): string`, `getSummary(id)`, `makeSummary(id, regenerate = false)`. Platform admins pass `client_id` the way `bookings-api.ts` does.
- Produces in `training-view.ts`: `refusalCopy(code: string): string` (one sentence per code in the spec's list, fallback "We could not do that. Try again in a minute."), `sizeText(bytes)`, `pagesText(n)`, `categoriesOf(items): string[]` (case-insensitive, first spelling wins, empty category shown as "Uncategorised"), `filterItems(items, {category, query})`, `uploadProblem(file): string | null` (not `.pdf`, empty, over 25 MB, before sending), `summaryNote(s: TrainingSummary): string` (the truncation sentence and the "sent to the language-model provider" notice).
- Produces in `training-slides.ts` (pure, tested): `clampPage(page, total)`, `nextPage`, `previousPage`, `keyToAction(key: string): "next" | "previous" | "first" | "last" | "fullscreen" | null`.

- [ ] **Step 1: Prepare an isolated `node_modules`:** the worktree's is a symlink to the main checkout's, which other sessions use. Replace it with a real install in the worktree (`rm` the symlink, `npm ci --ignore-scripts --no-audit --no-fund`), so adding a dependency never changes the main checkout.
- [ ] **Step 2 (proof gate): add `pdfjs-dist`** with `npm install --save-exact --ignore-scripts pdfjs-dist@<newest release at least two weeks old, Apache-2.0>`. Add a throwaway client page that imports `pdfjs-dist` and its worker the way its docs describe for a bundler (`new URL(".../pdf.worker.min.mjs", import.meta.url)`), run `npm run build` (the `standalone` build), and confirm the worker file is emitted under `.next/static` and the build passes. If the default import does not bundle, copy the worker into `public/` and set `GlobalWorkerOptions.workerSrc` to that path; record which in a code comment. Remove the throwaway page. Commit `chore(web): pin pdfjs-dist`. Stop and report to the owner if neither works.
- [ ] **Step 3: Failing tests:** `training-view.test.mjs` (`refusalCopy` covers every code in the spec and never contains an underscore or a digit-code; `categoriesOf` merges `"HR"`, `"hr "` and `""`; `filterItems` by category and by title case-insensitively; `uploadProblem` for `.docx`, a 0-byte file, 25 MB + 1; `summaryNote` includes the truncation sentence only when truncated); `training-slides.test.mjs` (clamp at both ends, one-page deck, key mapping for arrows, Home, End, `f`, ignores modifier keys); `training-api.test.mjs` (the request paths, the `Idempotency-Key` header, `client_id` for platform admins, `ApiError.code` preserved), following `tests/bookings-api.test.mjs`. Run `npm test`; expect FAIL.
- [ ] **Step 4: Implement** the three libs, the navigation entry, and the four page files (each `requireIdentity()` then the workspace component of Task 12; until then a placeholder heading). Add every new lib and test file to `scripts/check-isolated.mjs` (file list and test-run entries).
- [ ] **Step 5: Run `npm run lint`, `npm run typecheck` (dummy `CSA_CONSOLE_API_ORIGIN`), `npm test`; expect PASS.** Then `tests/test_workspace_contracts.py` once (tab label parity).
- [ ] **Step 6: Commit** `feat(web): training client, copy and navigation`.

### Task 11: The PDF viewer

**Files:**
- Create: `components/console/training/PdfViewer.tsx`, `components/console/training/Thumbnails.tsx`

**Interfaces:**
- Produces: `PdfViewer({ itemId, title, mode: "slides" | "document", onMode })` (client component). It loads `fileUrl(itemId)` with `credentials: "same-origin"` into an `ArrayBuffer`, opens it with pdf.js using `isEvalSupported: false` and scripting off, renders to canvas. Slides: one fitted page, buttons "Previous slide" and "Next slide", counter "Slide 3 of 12", keys through `keyToAction`, thumbnail strip, "Full screen" via the Fullscreen API. Document: pages stacked, zoom buttons. Test ids: `training-viewer`, `training-page-canvas`, `training-prev`, `training-next`, `training-counter`, `training-thumb-<n>`, `training-fullscreen`, `training-mode-slides`, `training-mode-document`, `training-viewer-error`.
- Consumes: Task 10 `lib/training-slides.ts`, `lib/training-view.ts` (`refusalCopy`), the pdf.js setup chosen in Task 10.

- [ ] **Step 1: Implement** with these states, each a sentence: loading ("Opening the PDF…"), network or 503 ("We could not open this PDF. Try again in a minute."), 404 ("This PDF is no longer in the library."), damaged ("This PDF is damaged, so we cannot show it. Ask the person who added it to upload it again."), password-protected ("This PDF is protected, so we cannot show it."). Respect `prefers-reduced-motion`, give every control an accessible name, make the thumbnail strip and buttons reachable by keyboard and usable on a phone. Cancel the pdf.js loading task on unmount.
- [ ] **Step 2: Run `npm run lint`, `npm run typecheck`; expect PASS.** Behavioural proof comes with the browser test in Task 13.
- [ ] **Step 3: Commit** `feat(web): pdf.js slide and document viewer`.

### Task 12: Library page, add and edit, summary panel

**Files:**
- Create: `components/console/TrainingWorkspace.tsx`, `components/console/training/ItemList.tsx`, `components/console/training/ItemDialog.tsx`, `components/console/training/SummaryPanel.tsx`
- Modify: the four page files of Task 10 (use `TrainingWorkspace`, with `[id]` pages passing `itemId`)

**Interfaces:**
- Produces: `TrainingWorkspace({ me, itemId? })`: without `itemId` the list (category filter, title search, empty state "PDFs your company adds will appear here." plus, for managers, a button "Add a PDF"); with `itemId` the viewer page (title, description, category, `PdfViewer`, `SummaryPanel`, a "Download PDF" link built as a blob with the title as file name). Managers see "Add a PDF", "Edit details" and "Remove PDF" (with a confirm that says "This removes the PDF for everyone in your company."); readers see none. Test ids: `training-list`, `training-item-<id>`, `training-filter`, `training-search`, `training-add`, `training-edit`, `training-remove`, `training-summarise`, `training-summary`, `training-regenerate`.
- Produces: `ItemDialog` (file for a new item, title, description, category with the existing categories offered, "Show as" slides or document; uploads then patches, and if the patch fails says "The PDF was added under its file name. Edit its details to rename it."), progress and every refusal through `refusalCopy`, retry reuses the same idempotency key.
- Consumes: Task 10 libs and Task 11 `PdfViewer`; `me.role` and the list's `can_manage`.

- [ ] **Step 1: Implement** following `BookingsWorkspace` and its dialogs for structure, `lib/api.ts` for requests and error handling. First summarise click shows the provider notice from `summaryNote` before sending.
- [ ] **Step 2: Run `npm run lint`, `npm run typecheck`, `npm test`; expect PASS.**
- [ ] **Step 3: Commit** `feat(web): training library page, dialogs and summary`.

### Task 13: Browser test, packaged-console check, retest note

**Files:**
- Create: `tests/test_training_browser.py`
- Modify: `tests/test_packaged_console.py` (a case that the standalone build serves the pdf.js worker), `docs/ASSISTANT_RETEST.md` or a new `docs/TRAINING_RETEST.md` (rows for the staging checks)

- [ ] **Step 1: Write the browser test** with the harness of `tests/test_knowledge_browser.py` and a synthetic 3-page landscape PDF: `test_manager_adds_a_pdf_and_it_opens_as_slides` (upload through the dialog, list shows it, opening it renders `training-page-canvas`, counter "Slide 1 of 3", next gives "Slide 2 of 3", thumbnail 3 jumps, mode switch to document shows three canvases, fullscreen control present); `test_plain_staff_sees_the_list_but_no_manage_buttons`; `test_damaged_file_shows_the_damaged_sentence` (object replaced with junk after upload); `test_missing_file_shows_the_not_in_library_sentence`; `test_phone_width_has_no_horizontal_scroll`.
- [ ] **Step 2: Run `tests/test_training_browser.py`; expect PASS** (it runs the real viewer in Chromium, which is the pdf.js proof for the standalone build).
- [ ] **Step 3: Add the packaged-console case and run `tests/test_packaged_console.py -k training` once** (slow; CI runs it anyway).
- [ ] **Step 4: Write the retest rows:** add one PDF as admin, read as plain staff, edit, summarise (needs the real provider key), remove, then confirm the file is gone from the volume; list what staging cannot show (real phone behaviour, S3, summary quality).
- [ ] **Step 5: Commit** `test(training): browser, packaged build and retest rows`.

## Self-review notes

Spec coverage: package and tab (4, 5, 10), data and RLS (4), storage and reset (1, 2), API (7, 8), upload checks (6, 7), summary (8), web (10-12), tests (each task, 13), documents (1, 9, 13), rollout (2, retest rows in 13; the PR text in finishing). Names used across tasks match: `check_pdf`, `extract_text`, `can_manage`, `build_training_router`, `TrainingLibrary`, `refusalCopy`, `keyToAction`, `fileUrl`.
