# Payment methods: add with a picture, delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin can choose the QR picture while adding a QR method, and can delete a payment method.

**Architecture:** Delete is one new admin route and service function (`DELETE /console/v1/payments/methods/{id}`) that removes the row and its `payment_qr` picture in one transaction under the existing per-company payments lock. The console gets a file chooser in the add form (uploaded right after the method is created) and a confirmed Delete button.

**Tech Stack:** FastAPI, psycopg, Postgres RLS, Next.js/React, pytest, Playwright browser tests.

**Spec:** `docs/superpowers/specs/2026-11-15-pictures-design.md` (pictures) and `docs/superpowers/plans/2026-11-13-payments-1.md` / `csa/payments/settings.py` module docstring (methods). Owner request 2026-11-16: "improve the payment method management, add (include upload) delete, edit if it is not within tasks". Edit already exists (`PATCH /methods/{id}`, `MethodForm`), so it is not rebuilt.

## Global Constraints

- Admins only (platform and tenant admins) change methods; `read_only` and other staff roles get 403 before anything is read (same `admin(request, client_id)` guard as `PATCH /methods/{id}`).
- Repo is PUBLIC: synthetic data only in tests and docs.
- Invoices and payments store `method_kind`, never a method id (`csa/payments/invoices.py`), so deleting a method never changes an invoice or a ledger row. Verify with a test.
- Delete takes the same transaction-long `lock(conn, client_id)` as create/update, and refuses to remove the last active `qr` or `transfer` method of a company with payments switched on: 409 `last_active_method` (reuse `ensure_another_payable_method`). An inactive method, a cash method, or any method while payments are off can always be deleted. 404 `method_not_found` also for another company's id.
- The method's picture (`media_assets` kind `payment_qr`, subject_key = method id) is removed in the same transaction (`csa.media.service.delete_for`) and audited like a picture removal (`media.picture.removed`) only when one was there; the delete itself is audited `payments.method.deleted` with the method id.
- No migration. `payment_methods` already grants DELETE to `csa_app`. `EXPECTED_SCHEMA_VERSION` stays "0610".
- Pictures: JPEG or PNG, 2 MiB, 64-4096 px, longer side at most 10x the shorter, 300 per company (`lib/pictures.ts` has the client checks and refusal sentences; the server stays the authority).
- Web copy: plain sentences, no jargon. The delete confirmation must say that customers stop seeing the method, that its QR picture is removed, and that "Off" keeps it for later.
- CI runs `uv run pyright` on the WHOLE project including tests; run it on every changed Python file. Web: `npm run lint`/typecheck/unit and browser tests as the repo does (browser files need more than 3000 MB free).
- Docs: `docs/CONSOLE_API.md` (the `DELETE /methods/{id}` row; replace "A method is never deleted"), the `csa/payments/settings.py` module docstring (replace "A method is never deleted, only turned off."), `docs/PROGRESS.md` entry.

## Review Focus

- Deleting the only active transfer method while payments are on: refused, nothing removed, picture kept.
- Two admins deleting the same method at once: the second gets 404, never a 500.
- Deleting a QR method whose picture was already removed: succeeds, no `media.picture.removed` audit row.
- Add form: the method is created but the picture upload then fails (too large, unreadable, limit): the method must still exist and the form must say so and show the picture field for a retry, never leave the admin thinking nothing was saved.
- A delete the admin did not mean (double click, Enter key): needs the confirm step, and the Delete control is unreachable while the form has unsaved changes it would hide.

---

### Task 1: Delete route, service, audit, picture cleanup, docs

**Files:**
- Create: `db/sql/payments/method_delete.sql`, `tests/test_payment_method_delete.py`
- Modify: `csa/payments/api.py` (route beside `update_method`), `csa/payments/settings.py` (`delete_method`, module docstring), `docs/CONSOLE_API.md`, `docs/PROGRESS.md`
- Test: `tests/test_payment_method_delete.py`, `tests/test_console_payments.py`, `tests/test_payment_settings.py`, `tests/test_media_api.py`

**Interfaces:**
- Consumes: `settings.lock`, `settings.ensure_another_payable_method`, `settings.list_methods`, `csa.media.service.remove_picture`, `audit`.
- Produces: `settings.delete_method(conn: Conn, client_id: UUID, actor: ConsoleActor, method_id: UUID) -> None`; `DELETE /console/v1/payments/methods/{method_id}` returning 204.

- [ ] **Step 1: Write the failing tests** (`tests/test_payment_method_delete.py`, DB tests with the repo's console client fixtures; copy the setup style from the existing method tests):
  - `test_admin_deletes_a_method`: 204, the method is gone from `GET /methods`, one `payments.method.deleted` audit row with the method id as target.
  - `test_delete_unknown_or_other_company_method_is_404` (`method_not_found`; second delete of the same id also 404).
  - `test_delete_last_active_payable_method_with_payments_on_is_409` (`last_active_method`; row and picture still there) and `test_delete_inactive_or_cash_method_or_payments_off_succeeds`.
  - `test_delete_removes_the_qr_picture_in_the_same_transaction` (upload a QR picture, delete the method: no `media_assets` row for the subject; a `media.picture.removed` audit row) and `test_delete_without_picture_writes_no_picture_audit`.
  - `test_delete_does_not_touch_invoices_or_payments` (an invoice and a payment with `method_kind` stay unchanged).
  - `test_read_only_and_staff_get_403` (before anything is read).
- [ ] **Step 2: Run each file one at a time**: FAIL.
- [ ] **Step 3: Implement** `db/sql/payments/method_delete.sql` (DELETE ... WHERE client_id AND id RETURNING id, tenant-scoped like `method_update.sql`), `settings.delete_method` (lock; `method_get.sql` -> 404; if the method is active and of kind qr/transfer, `ensure_another_payable_method`; delete; `remove_picture(conn, client_id, actor, "payment_qr", str(method_id))`; audit `payments.method.deleted`), the route (`admin(...)`, `tx(...)`, `Response(status_code=204)`), and the docs rows.
- [ ] **Step 4: Run** the test files named above, then `ruff check`, `ruff format --check`, `lint-imports`, `pyright` on every changed Python file: PASS.
- [ ] **Step 5: Commit** `feat(payments): delete a payment method`.

### Task 2: Console: file chooser in the add form, confirmed Delete

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/payments-api.ts` (`deleteMethod`), `CSA_WebConsole/csa-console/web/components/console/PaymentsWorkspace.tsx` (`Methods`, `MethodForm`), `CSA_WebConsole/csa-console/web/components/console/PictureField.tsx` (a "choose now, upload after save" mode, or a small sibling component in the same folder), and the unit/browser tests next to the existing method tests (`PaymentsWorkspace` tests, `tests/browser/` payments file).
- Test: the existing payments workspace unit and browser test files plus a new browser case file only if no existing file fits.

**Interfaces:**
- Consumes: Task 1 route; `uploadPicture`/`methodPicturePath`/`pictureErrorText`/`PICTURE_LIMITS` from `lib/pictures.ts` and `lib/payments-api.ts` (read how `PictureField` calls them).
- Produces: `deleteMethod(tenantId: string | null, id: string): Promise<void>` and the UI below.

- [ ] **Step 1: Write the failing tests:**
  - Add form, kind QR: a file chooser "QR picture (optional)" with the limits hint; choosing a bad type or a file over 2 MB shows the `pictureErrorText` sentence at once and does not block saving the method; `Add method` creates the method and then uploads the chosen file; the list shows the picture; transfer and cash kinds show no chooser.
  - Add form, upload refused after creation (server 422/413/409 `picture_limit`): the method is in the list, the form switches to that method's edit form with the notice "The method was added, but the picture could not be saved: <sentence>", and the picture field is there for a retry.
  - Delete: each method's edit form has a "Delete method" button that opens a confirm with the three sentences from Global Constraints; confirming calls `DELETE`, the method disappears and the notice says "Deleted."; cancel changes nothing; a 409 `last_active_method` shows "Payments are on and this is the last active bank transfer or DuitNow method. Turn payments off or add another method first." and keeps the method; a 404 removes the stale row and rereads the list.
  - The Delete button is disabled while the form has unsaved changes (so Save or Cancel comes first), and the confirm cannot be triggered twice (button disabled while the request runs).
- [ ] **Step 2: Run**: FAIL. **Step 3: Implement** with the existing `kit` components, notices and confirm pattern already used for Void and Reject claim in `PaymentsWorkspace.tsx`. The "Methods" intro sentence ("A method can be turned off but not ...") must be reworded to say a method can be turned off or deleted.
- [ ] **Step 4: Run** `npm run lint`, typecheck, the unit tests and the browser tests for the changed files: PASS.
- [ ] **Step 5: Commit** `feat(console): add a payment method with its QR picture, delete a method`.
