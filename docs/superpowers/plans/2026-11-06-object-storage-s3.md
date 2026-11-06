# Object storage (local disk or S3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the knowledge-base and HR document files behind one small storage interface with a local-disk and an S3 implementation, selected by settings, so production can keep files in S3.

**Architecture:** A new `csa/storage/` package defines `ObjectStore` (tenant-scoped put/open/read/delete/list). `LocalObjectStore` keeps today's on-disk behaviour; `S3ObjectStore` stores `kb/<client_id>/<name>` and `hr/<client_id>/<name>` in one private bucket. `csa/console/kb_uploads.py`, `csa/hr/documents.py` and the KB ingest worker call the interface instead of touching paths. Stored names in the database do not change.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, boto3 (new, pinned), moto (new, dev, pinned), pytest, Testcontainers Postgres.

**Spec:** `docs/superpowers/specs/2026-11-04-lightsail-s3-interim-hosting-design.md` (section 2, "Storage interface", plus 7a for credentials). Out of scope here, planned separately: the production compose overlay, the 6-hourly backup script, the capacity-gate script and the cutover runbook.

## Global Constraints

- Keys are tenant-prefixed: `kb/<client_id>/<name>` and `hr/<client_id>/<name>`; HR and KB stay separate prefixes.
- Stored relative paths and `storage_name` values in the database stay unchanged, so existing rows work after files are copied to the same keys.
- A missing or wrong bucket, key or region stops the process at startup. No silent fallback to local disk (AGENTS.md: surface configuration and runtime errors).
- Credentials come from Docker secret files read by `Settings` (`/run/secrets/<FIELD>`); never from source, fixtures or logs.
- New dependencies are pinned exactly and `uv.lock` is updated (AGENTS.md). Use the latest stable release at execution time and record the versions in the commit message.
- S3 requests ask for S3-managed encryption (`ServerSideEncryption="AES256"`); no KMS.
- The app's IAM key may list only under `kb/*` and `hr/*` (condition on `s3:prefix`), so `head_bucket` is not usable for the startup check; list one key under the store's own prefix instead.
- The local backend stays the default for dev and CI. Existing local behaviour and error messages do not change.
- S3 calls are blocking; call them through `asyncio.to_thread` as the file code does today.
- `make lint` (Ruff, Pyright, import-linter) passes; locally run only the changed test files, CI runs the full suite.
- Commits end with the session's `Co-Authored-By` attribution line.

## Review Focus

1. **Name safety.** `../x`, `a/b`, `.`, `..`, empty, NUL and over-255-character names are refused on both backends (Task 1 contract test, run for S3 in Task 2).
2. **Missing or unreachable object on download.** The HTTP result stays 503 (`source file unavailable; storage repair required` for KB, `hr_document_file_unavailable` for HR), never 500; an S3 access-denied is a storage error, not "not found" (Task 2, Task 4, Task 5).
3. **Cleanup scope.** Cleanup deletes only server-generated names inside the caller's own tenant prefix and never touches another tenant's objects, even when tenant ids share leading characters (Task 1, Task 4).
4. **Duplicate-retry HR upload.** The retry's unreferenced copy is deleted from the store, and the first request's file is kept (Task 5).
5. **Size limit on ingest.** The worker reads at most `max_bytes + 1` bytes from the store and still refuses oversize and empty files with the current messages (Task 4).

---

### Task 1: Storage interface and local backend

**Files:**
- Create: `csa/storage/__init__.py`, `csa/storage/base.py`, `csa/storage/local.py`
- Test: `tests/test_storage_contract.py`, `tests/test_storage_local.py`

**Interfaces:**
- Produces, in `csa/storage/base.py`:
  - `class StorageError(Exception)`; `class StorageNotFound(StorageError, FileNotFoundError)`; `class InvalidObjectName(StorageError, ValueError)`.
  - `class ObjectStore(Protocol)` with sync methods `put(tenant: UUID, name: str, data: bytes) -> None` (atomic and durable), `open(tenant: UUID, name: str) -> BinaryIO` (caller closes), `read(tenant: UUID, name: str, limit: int) -> bytes` (at most `limit` bytes), `delete(tenant: UUID, name: str) -> None` (missing is not an error), `list_names(tenant: UUID) -> list[str]`, `check() -> None` (raises `StorageError` when unusable).
  - `def validate_name(name: str) -> str`: returns the name or raises `InvalidObjectName` (non-empty, at most 255 characters, no `/`, no `\`, no NUL, not `.` or `..`).
  - `async def durable_put(store: ObjectStore, tenant: UUID, name: str, data: bytes) -> None`: runs `store.put` in a thread and finishes it even if the awaiting task is cancelled (same shield-then-await behaviour as today's `durable_publish`).
- Produces, in `csa/storage/local.py`: `class LocalObjectStore(root: Path)` implementing `ObjectStore`.

- [ ] **Step 1: Write the contract tests** in `tests/test_storage_contract.py` against a fixture `store` (parametrized over backends; only `local` now, `tmp_path` root). Tests: `test_put_then_read_returns_same_bytes`, `test_put_leaves_only_the_final_name` (`list_names == [name]`), `test_open_streams_in_blocks` (`open().read(4)` returns the first 4 bytes), `test_missing_object_raises_storage_not_found` (for `open` and `read`), `test_delete_missing_is_not_an_error`, `test_delete_then_read_raises_not_found`, `test_list_names_is_per_tenant` (two tenants, including ids that share a long common prefix), `test_invalid_names_are_refused` (the Review Focus 1 list, on `put`, `open`, `read`, `delete`), `test_read_returns_at_most_limit_bytes`, `test_durable_put_completes_when_cancelled`.
- [ ] **Step 2: Write the local-only tests** in `tests/test_storage_local.py`: `test_tenant_directory_is_created_with_mode_0700`, `test_symlinked_tenant_directory_is_refused` (raises `StorageError`), `test_read_accepts_an_absolute_path_inside_the_tenant_directory`, `test_read_refuses_a_path_outside_the_tenant_directory`, `test_list_names_includes_part_files` (cleanup relies on seeing `.name.part`).
- [ ] **Step 3: Run them and confirm they fail** (`pytest tests/test_storage_contract.py tests/test_storage_local.py -q`; expect import errors).
- [ ] **Step 4: Implement `base.py` and `local.py`.** Move the logic of `tenant_directory`, `publish` (temp file, fsync, `os.replace`, directory fsync) and `resolve_upload_path` from `csa/console/kb_uploads.py` and `csa/agents/kb/extract.py` into `LocalObjectStore`; for `read`/`open` reuse `resolve_upload_path` and translate its `KbFileError` into `StorageNotFound` keeping the original message. Do not delete the originals yet.
- [ ] **Step 5: Run the tests; expect PASS.**
- [ ] **Step 6: Commit** (`feat(storage): ObjectStore interface and local backend`).

### Task 2: S3 backend

**Files:**
- Create: `csa/storage/s3.py`
- Modify: `pyproject.toml` (boto3 in `dependencies`, moto in the `dev` group, both pinned), `uv.lock`
- Test: `tests/test_storage_s3.py`; extend the fixture in `tests/test_storage_contract.py` with an `s3` param

**Interfaces:**
- Consumes: Task 1's `ObjectStore`, `validate_name`, error classes.
- Produces: `class S3ObjectStore(bucket: str, prefix: str, client: Any, list_page_size: int = 1000)` implementing `ObjectStore`, and `S3ObjectStore.create(bucket: str, region: str, access_key_id: str, secret_access_key: str, prefix: str) -> S3ObjectStore` that builds the boto3 client with `signature_version="s3v4"`, standard retries (3 attempts), connect timeout 5 s, read timeout 30 s.

- [ ] **Step 1: Add the pinned dependencies** (`uv add --exact boto3`, `uv add --exact --group dev "moto[s3]"`, or the equivalent that writes exact pins) and confirm `uv.lock` changed.
- [ ] **Step 2: Extend the contract fixture** so every contract test also runs against `S3ObjectStore` on a moto bucket (prefix `kb`).
- [ ] **Step 3: Write S3-only tests** in `tests/test_storage_s3.py`: `test_key_layout_is_prefix_tenant_name` (the raw client sees `kb/<tenant>/<name>`), `test_put_requests_aes256_encryption` (`head_object` shows `AES256`), `test_list_names_paginates` (page size 2, five objects), `test_check_fails_for_a_missing_bucket`, `test_check_lists_under_its_own_prefix_only` (a Stubber asserts the call is `list_objects_v2` with `Prefix="kb/"` and `MaxKeys=1`, and that `head_bucket` is never called), `test_access_denied_is_a_storage_error_and_not_not_found` (Stubber returns a 403 on `get_object`; expect `StorageError` that is not `StorageNotFound`).
- [ ] **Step 4: Run and confirm failure**, then implement `s3.py`: `NoSuchKey`/404 maps to `StorageNotFound`; every other `ClientError` and connection error maps to `StorageError` without including credentials in the message; `open` returns the response body (`read(n)` and `close()`); `read` uses `get_object` with a `Range` header of `bytes=0-<limit-1>`.
- [ ] **Step 5: Run `tests/test_storage_contract.py tests/test_storage_s3.py`; expect PASS.**
- [ ] **Step 6: Commit** (`feat(storage): S3 backend`, versions of boto3 and moto in the body).

### Task 3: Settings, factory and startup check

**Files:**
- Modify: `csa/core/settings.py`
- Create: `csa/storage/factory.py`
- Test: `tests/test_settings.py` (additions), `tests/test_storage_factory.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: new `Settings` fields `STORAGE_BACKEND: Literal["local", "s3"] = "local"`, `S3_BUCKET: str | None`, `S3_REGION: str | None`, `S3_ACCESS_KEY_ID: SecretStr | None`, `S3_SECRET_ACCESS_KEY: SecretStr | None`; in `factory.py`: `@dataclass(frozen=True) class Stores: kb: ObjectStore | None; hr: ObjectStore | None`, `def build_stores(settings: Settings) -> Stores`, `async def verify_stores(stores: Stores) -> None` (runs each `check()` in a thread).

- [ ] **Step 1: Write the settings tests**: `test_s3_backend_requires_bucket_region_and_both_keys` (one case per missing field, message `STORAGE_BACKEND=s3 requires S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY`), `test_s3_backend_refuses_local_upload_roots` (message names the root that was set), `test_s3_backend_ingest_does_not_need_kb_upload_root` (prod, `KB_INGEST_ENABLED=True`, TEI set, S3 values set: valid). The existing KB/HR root tests must pass unchanged.
- [ ] **Step 2: Write factory tests**: `test_build_stores_local_returns_none_for_unset_roots`, `test_build_stores_local_uses_the_configured_roots`, `test_build_stores_s3_gives_kb_and_hr_separate_prefixes` (writes through `kb` land under `kb/`, through `hr` under `hr/`), `test_verify_stores_raises_for_a_missing_bucket` (moto, no bucket created).
- [ ] **Step 3: Run and confirm failure; implement** the fields, the validators (the KB-ingest validator requires `KB_UPLOAD_ROOT` only for the local backend) and the factory.
- [ ] **Step 4: Call `await verify_stores(stores)`** in the gateway lifespan (`apps/gateway/main.py`, before the first request is served) and in the worker's startup (`apps/worker/main.py`, before the ingest loop starts), so a bad bucket stops the process. Add one test that builds the gateway the way `tests/test_console_identity_gateway.py` does and shows startup fails with a missing bucket.
- [ ] **Step 5: Run the changed test files; expect PASS.** Commit (`feat(storage): settings, factory and startup check`).

### Task 4: Knowledge-base wiring

**Files:**
- Modify: `csa/console/kb_uploads.py`, `csa/console/router.py`, `csa/agents/kb/ingest_jobs.py`, `apps/gateway/main.py`, `apps/worker/main.py`, `scripts/dev_kb_ingest.py`
- Modify tests: `tests/test_console_kb_uploads.py`, `tests/test_console_sessions.py`, `tests/test_kb_ingest.py`, `tests/test_dev_kb_ingest.py`
- Test (new cases): `tests/test_console_kb_uploads.py`

**Interfaces:**
- Consumes: `ObjectStore`, `durable_put`, `Stores` from Tasks 1 and 3.
- Produces: `build_upload_router(store: ObjectStore | None) -> APIRouter`; `cleanup_storage(pool, store: ObjectStore, client_id: UUID) -> int`; `build_router(..., kb_store: ObjectStore | None = None)` replacing `kb_upload_root`; `KbIngestConfig.store: ObjectStore` replacing `upload_root`; `read_source(store: ObjectStore, client_id: UUID, path: str, max_bytes: int) -> bytes` in `ingest_jobs.py` (raises `KbFileError`, keeping today's messages for oversize and empty files).

- [ ] **Step 1: Update the existing tests to the new signatures** (pass `LocalObjectStore(root)`; the patched `jobs.read_upload` becomes `jobs.read_source`). They must fail first, because the code still takes paths.
- [ ] **Step 2: Write new tests**: `test_kb_upload_with_s3_store_is_ingested_end_to_end` (upload through the API on a moto-backed store, run `KbIngestProcessor` on the same store, a document exists), `test_kb_download_streams_from_s3`, `test_kb_download_of_a_missing_object_is_503`, `test_kb_cleanup_removes_only_unreferenced_server_names_in_its_tenant_prefix` (an object under another tenant and a foreign-named object in this tenant both survive), `test_ingest_refuses_oversize_and_empty_objects_with_current_messages`.
- [ ] **Step 3: Implement.** `kb_uploads.py` uses `durable_put`, `store.open`, `store.delete` and `store.list_names` (keep the `\.?[a-f0-9-]{36}\.(pdf|docx|csv)(\.part)?` sweep filter); `ingest_jobs.py` reads through `read_source`, mapping `StorageNotFound` to the existing "source file was deleted or is unavailable" result; gateway and worker take their store from `build_stores(settings)`; the dev script keeps working on the local backend. Leave `tenant_directory`, `publish` and `durable_publish` in `kb_uploads.py` for Task 5.
- [ ] **Step 4: Run the four modified test files; expect PASS.** Commit (`feat(kb): knowledge-base files go through ObjectStore`).

### Task 5: HR documents wiring and cleanup of dead helpers

**Files:**
- Modify: `csa/hr/documents.py`, `csa/hr/api.py`, `apps/gateway/main.py`, `csa/console/kb_uploads.py` (delete helpers)
- Modify tests: `tests/test_hr_people_records.py`; test for `HR_UPLOAD_ROOT` in `tests/test_staging_pipeline.py` must still pass untouched
- Test (new cases): `tests/test_hr_people_records.py`

**Interfaces:**
- Consumes: `ObjectStore`, `durable_put`, `StorageError`.
- Produces: `build_hr_router(hr_store: ObjectStore | None = None) -> APIRouter` (replacing `hr_upload_root`); `HrDocuments(core: HrService, store: ObjectStore)`; `cleanup(pool, store: ObjectStore, client_id: UUID) -> int`.

- [ ] **Step 1: Update the existing HR test** (`build_hr_router(hr_store=LocalObjectStore(root))`) and add: `test_hr_document_roundtrip_with_s3_store`, `test_duplicate_retry_deletes_the_unreferenced_copy_and_keeps_the_first` (S3 store; the object list shows one file), `test_hr_download_of_a_missing_object_is_503`, `test_hr_cleanup_never_touches_kb_prefix_objects` (same bucket, `kb/` object survives an HR cleanup).
- [ ] **Step 2: Run and confirm failure; implement.** Replace the directory handling in `upload`, `download`, `remove` and `cleanup` with the store calls; keep the `_STORED` sweep pattern and the `_LOCK` serialisation; an `OSError`/`StorageError` on open still gives `HTTPException(503, "hr_document_file_unavailable")`.
- [ ] **Step 3: Delete `tenant_directory`, `publish` and `durable_publish` from `kb_uploads.py`** (now unused); `FileRecord` and `SourceDownload` stay. Run `make lint`.
- [ ] **Step 4: Run the changed test files plus `tests/test_staging_pipeline.py`; expect PASS.** Commit (`feat(hr): HR documents go through ObjectStore`).

### Task 6: Real-bucket smoke script, settings docs

**Files:**
- Create: `scripts/storage_smoke.py`, `docs/OBJECT_STORAGE.md`
- Modify: `.env.example`
- Test: `tests/test_storage_smoke.py`

**Interfaces:**
- Consumes: `build_stores`, `verify_stores`, `Settings`.
- Produces: `python -m scripts.storage_smoke`: exit 0 when every check passes, 1 on a failed check, 2 on a setup error; one `PASS|FAIL <check>` line per check; never prints keys or object contents.

- [ ] **Step 1: Write the test** `test_smoke_passes_against_moto_and_prints_no_secrets` (checks present: put/read, list, open stream, delete, delete-missing, read-missing is `StorageNotFound` or `StorageError`; output contains no key text) and `test_smoke_exits_2_without_settings`.
- [ ] **Step 2: Implement the script** using a random tenant id under both prefixes and removing what it wrote. It is meant for the owner to run with the real app key on the instance.
- [ ] **Step 3: Write `docs/OBJECT_STORAGE.md`**: the five settings and where the two key files go (`/run/secrets/S3_ACCESS_KEY_ID`, `/run/secrets/S3_SECRET_ACCESS_KEY`), the IAM policy used in production (list only under `kb/*` and `hr/*`; get/put/delete on `kb/*` and `hr/*`; put-only on `backups/*`), how to copy existing local upload volumes to `kb/<client_id>/...` and `hr/<client_id>/...` with `aws s3 sync` under an admin profile, and the rule that local and S3 backends are never mixed. Add the commented settings to `.env.example` with the backend defaulting to `local`.
- [ ] **Step 4: Run `tests/test_storage_smoke.py` and `make lint`; expect PASS.** Commit (`feat(storage): smoke script and docs`).

---

## Self-review notes

- **Spec coverage:** interface with local and S3 implementations (1, 2); three touch points KB, HR and ingest (4, 5); settings and secret files (3); startup stop on a bad bucket (3); pinned dependency (2); tenant-prefixed keys and unchanged database paths (global constraints, 4, 5); S3-managed encryption (2); owner copy of existing files (6, docs). Production compose overlay, backups, capacity gate and cutover are in a separate plan.
- **Open for the executor to confirm against the real bucket (Task 6 smoke):** whether S3 answers 404 or 403 for a missing key under the app key's prefix-conditioned `ListBucket`. Either maps to a 503 for downloads, so no code change is expected.
