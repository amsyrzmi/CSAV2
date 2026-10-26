# Package Hardening Implementation Plan

**Goal:** Inbox reachable while Customer Support is suspended; staging containers restart automatically; readable copy for gated pages.
**Spec:** docs/superpowers/specs/2026-10-26-package-hardening-design.md
**Execution:** native (single small PR), one fresh reviewer at the end.

### Task 1: Gate statuses
- Create `db/sql/console/service_package_any_in_status.sql`: `SELECT 1 FROM public.console_service_packages WHERE client_id=%s AND package = ANY(%s) AND status = ANY(%s) LIMIT 1`.
- `require_package(pool, client_id, keys, statuses=frozenset({"active"}))`; `package_gate(*keys, statuses=("active",))`.
- `csa/console/inbox.py`: `package_gate("customer_support", statuses=("active", "suspended"))`.
- Test (tests/test_console_services.py, extend `test_platform_admin_suspends_active_package_over_http`): after suspension a staff member gets 200 from `/console/v1/inbox/conversations` and 403 `package_inactive` from `/console/v1/kb/documents`; a tenant with no package still gets 403 on the inbox (existing negative test).

### Task 2: Restart policy
- `deploy/compose.staging-pipeline.yml`: `restart: unless-stopped` on gateway, worker, delivery, staging-embeddings. `deploy/compose.staging-local-proposal.yml`: on frontend and staging-db.
- Test (tests/test_staging_pipeline.py): rendered config has `restart == "unless-stopped"` for those six services and none for one-shots (migrate, embeddings-init, uploads-init, admin-bootstrap, staging-bot-setup).
- Docs: one sentence in deploy/STAGING_PIPELINE.md §2.

### Task 3: Readable copy
- `lib/api.ts`: `ApiError` gains `code` (the raw detail); `decode()` maps `package_inactive` to the sentence.
- Test: `tests/services-api.test.mjs` asserts a 403 `{detail:"package_inactive"}` yields the sentence with `code === "package_inactive"`; `tests/test_services_browser.py` scenario (f) asserts the sentence.

### Task 4: Docs and verification
- CONSOLE_API.md gate bullet, ARCHITECTURE.md sentence, PROGRESS.md entry.
- `NO_LOCAL_ENV=1 ENV=ci make lint`; pytest for test_console_services, test_staging_pipeline, test_staging_compose, test_services_browser, test_console_contract_snapshot; `npm run check:isolated`; `make test`.
