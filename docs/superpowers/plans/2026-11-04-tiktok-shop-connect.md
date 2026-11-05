# TikTok Shop connect (Project 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Fourstacks Admin connects a real TikTok Shop from the console; its orders and products are copied in and kept current; staff see them in full; the customer and workspace assistants answer order-number and stock questions; and `marketplaces` becomes sellable.

**Architecture:** Build on the finished-but-unwired M6B code in `csa/agents/ecommerce/` (fenced grants, verified authorization, single-use reads). Extend it for first connect, refresh, product reads and a time-window order search. A new `csa/marketplace/` package holds the sync engine (mapping, job queue, processor, webhook, retention). The connect callback lives in the console router, behind the Admin session, like Gmail's. Orders go into the existing `orders` tables; products into a new `marketplace_listings` table.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async, PostgreSQL with RLS, Pydantic, httpx `MockTransport` in tests; Next.js console (`CSA_WebConsole/csa-console/web`) with `node --test` and Playwright browser tests.

**Spec:** `docs/superpowers/specs/2026-10-27-tiktok-shop-connect-design.md` (section 0 is the 4 Nov revision, approved by the owner on 4 Nov). Read it before any task.

## Global Constraints

- **Do not run any test, image build or Testcontainers** until the owner has approved this plan AND said the staging rollout for PR #67 has finished. After that, run only the changed files' tests, one file at a time, plus `make lint` and the web checks (`npm test`, `npm run lint`, `npm run typecheck` in the web directory). Never the full suite (the host has 14 GB, shared). Remove only Testcontainers you started, after checking.
- **No commit and no push unless the owner asks.** Each task ends in a "Checkpoint" step that stages nothing and commits nothing unless the owner has asked; the message is given for when they do. Never rebase or force-push.
- Never contact TikTok from tests: every TikTok call uses `httpx.MockTransport`, with fixtures written from the docs. Synthetic data and disposable Testcontainers only. Never touch the production database, bot tokens or n8n.
- The repository is public: no real email addresses, shop IDs, tokens or secrets in code, tests, docs, commits or PRs. The App Key, App Secret and the real Service ID stay with the owner. Tests use made-up values. Never read `/srv/csa-staging/secrets`.
- Migrations only in `0490`–`0499`. `EXPECTED_SCHEMA_VERSION` (`apps/gateway/main.py`) stays at the highest applied version, `"0530"`, and tests import it, never hardcode it. Never `FORCE` RLS. No new roles.
- psycopg 3 async only; SQL in `db/sql/`; parameterized queries; Pydantic for external data; only `csa/graph/` imports LangGraph; no silent fallbacks.
- Error payloads carry safe codes, never provider text, tokens, codes or exception text. Signed requests are never retried or logged.
- Writes to TikTok are out of scope. TikTok chat (Project 2) and e-invoicing are out of scope; `e_invoicing` stays unsellable.
- UI: the redesign's kit (`components/console/kit`, `ui.tsx`) and theme tokens; every new screen is checked in all three themes and on a phone; plain words per `docs/WRITING_GUIDE.md`; the role is "Admin", never "tenant admin".
- Retention constants: buyer details cleared 90 days after an order is `delivered` or `cancelled`; 30 days after a store disconnects (orders' buyer data cleared, listings deleted). One constant each, in `csa/marketplace/retention.py`.
- `csa/console/packages.py` is touched only in Task 22, the last commit (Bookings edits it too; whoever merges second resolves).

## Review Focus

1. **A seller account with several shops, or the wrong shop** authorizes: connect fails with `multiple_shops` or `shop_mismatch`/`shop_in_use`, stores nothing, and reveals nothing about another client. (Tasks 5, 9.)
2. **Webhooks and syncs arriving twice or out of order:** an older `provider_updated_at` never overwrites a newer row; duplicate triggers fold into one job. (Tasks 11, 12, 13.)
3. **A cleared order synced again:** buyer name, phone and address are never written back after the 90-day clear. (Tasks 11, 14.)
4. **Odd TikTok data:** unknown status, masked or missing address, zero lines, the same SKU on several lines, a negative or huge stock number: maps safely, never crashes the job, never logs buyer data. (Tasks 7, 11.)
5. **The callback with no session, a foreign session, an expired or replayed state, or a TikTok error:** it redirects with a safe `reason` code and changes nothing. (Task 9.)

Also covered where they arise: a deauthorization while a job is running (Task 13), and an ambiguous stock question (Task 19).

---

### Task 1: Settings, app loader and operator command

**Files:**
- Modify: `csa/core/settings.py` (next to `TIKTOK_APP_KEY` at line 107; mirror the WhatsApp "disabled" validators at lines 296–313)
- Create: `csa/agents/ecommerce/app_loader.py`, `scripts/tiktok_app.py`
- Test: `tests/test_tiktok_settings.py`, `tests/test_tiktok_app_loader.py`, `tests/test_tiktok_app_script.py`

**Interfaces:**
- Produces: `Settings.TIKTOK_SERVICE_ID: str | None`; `Settings.TIKTOK_APP_ID: str = "tiktok_shop"`; `Settings.tiktok_enabled -> bool` (key, secret and service id all set and none equal to `disabled`; empty and `disabled` read as unset).
- Produces: `AppLoader = Callable[[str], Awaitable[TikTokAppMaterial | None]]`; `build_app_loader(settings: Settings, pool: AsyncConnectionPool) -> AppLoader`. It returns `None` when TikTok is off, the row is missing or `disabled`, or the requested id is not `TIKTOK_APP_ID`. The revision comes from the `ecommerce_apps` row, never from settings.
- Produces: `scripts/tiktok_app.py` with `main(argv: list[str]) -> int` for `register`, `enable`, `disable`, wrapping `register_app` and `transition_app` on the owner pool.

- [ ] **Step 1: Write failing tests.** `test_tiktok_enabled_off_for_unset_empty_and_disabled` (each of the three secrets set to `""`, `"disabled"`, unset gives `tiktok_enabled is False`; all three real-looking synthetic values give `True`). `test_loader_returns_material_with_row_revision` (active row at revision 3 gives `app.revision == 3`, `app.enabled`). `test_loader_none_when_off_disabled_or_wrong_id`. `test_script_register_enable_disable_cycle` (register leaves state `disabled` at revision 1; enable gives `active` at 2; disable gives `disabled` at 3; each step prints the new state and never a secret).
- [ ] **Step 2: Run each file; expect FAIL** (missing attributes and modules).
- [ ] **Step 3: Implement the settings and loader.** The script refuses to run without the owner DSN setting used by `scripts/staging_migrate.py`, as that script does.
- [ ] **Step 4: Run each file; expect PASS.**
- [ ] **Step 5: Checkpoint.** Files above. Message: `feat(tiktok): service id setting, app loader and app operator command`.

### Task 2: Migrations 0490–0492 and the README range

**Files:**
- Create: `db/migrations/0490_tiktok_legacy_cleanup.sql`, `db/migrations/0491_tiktok_connect_schema.sql`, `db/migrations/0492_marketplace_sync_schema.sql`
- Modify: `db/migrations/README.md` (add "`0490`–`0499`: TikTok Shop connect (own track)")
- Test: `tests/test_tiktok_migrations.py`

**Interfaces (produces; later tasks rely on these exact names):**
- `0490`: deletes every `channel_credentials` row with `channel='tiktok_shop'` that has no `ecommerce_grants` row, with its `webhook_endpoints`; marks legacy TikTok `ingress_events` (`raw->>'channel' = 'tiktok_shop'`, status `received`) as `dead`. Runs before the unique index in `0491`.
- `0491`: `channel_credentials.disconnected_at timestamptz` (the existing fence trigger bumps the generation when it changes, which is wanted); unique index `channel_credentials_tiktok_shop_key ON (shop_id) WHERE channel='tiktok_shop' AND shop_id IS NOT NULL AND disconnected_at IS NULL`; `ecommerce_grants.source` check also allows `verified_token_refresh`; `public.resolve_tiktok_shop_route(p_shop_id text) RETURNS TABLE(client_id uuid, store_id uuid)` (SECURITY DEFINER, owner `csa`, active stores only, `GRANT EXECUTE` to `csa_app`, same pattern as `resolve_whatsapp_route` in `0470`); table `marketplace_store_state(store_id uuid PK, client_id, orders_synced_at, products_synced_at, catch_up_cursor timestamptz, last_error_code text, problem_notified_at timestamptz, updated_at)` with tenant RLS (sync state lives off `channel_credentials` because every content change there fences the credential).
- `0492`: `orders` gains `store_id uuid`, `synced_at`, `provider_updated_at`, `provider_status text`, `buyer_cleared_at timestamptz`, plus unique partial index `orders_marketplace_external_key ON (client_id, channel, external_order_id) WHERE channel IN ('tiktok_shop','shopee') AND external_order_id IS NOT NULL`; `order_items` unchanged; table `marketplace_listings` (columns as spec §6.3, unique `(client_id, channel, external_sku_id)`); table `marketplace_sync_jobs` (columns as spec §6.3, status in `pending|claimed|done|failed|cancelled`, partial unique index `(client_id, store_id, kind, coalesce(target,'')) WHERE status='pending'`). Both new tables: tenant RLS policy `tenant_isolation` exactly as in `0280`, `GRANT SELECT, INSERT, UPDATE, DELETE` to `csa_app`, `SELECT` to `csa_ro`. No `FORCE`.

- [ ] **Step 1: Write failing tests** against a disposable database migrated through `scripts/migrate`: `test_legacy_unverified_tiktok_stores_removed_and_verified_kept` (seed one with a grant, one without, via the owner connection); `test_second_active_store_for_same_shop_rejected` and `test_disconnected_store_frees_the_shop`; `test_resolve_route_returns_active_store_only_and_no_tokens`; `test_new_tables_isolate_tenants` (app role under another client sees zero rows); `test_marketplace_order_upsert_key_is_unique`; `test_max_applied_version_equals_expected` (imports `EXPECTED_SCHEMA_VERSION` from `apps.gateway.main`; passes unchanged at `"0530"`).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Write the three migrations** with header comments in the `0470` style (preconditions, rollback).
- [ ] **Step 4: Run `tests/test_tiktok_migrations.py` only** (no `make db-reset`: the host is short of memory); expect PASS.
- [ ] **Step 5: Checkpoint.** Message: `feat(db): tiktok migrations 0490-0492 and range note`.

### Task 3: Product permission and the refresh grant source

**Files:**
- Modify: `csa/agents/ecommerce/authorization.py` (`TikTokGrantState.source` literal; new scope helper), `db/sql/business/store_connections.sql` (permission list)
- Test: `tests/test_ecommerce_authorization.py` (extend), `tests/test_console_business.py` (extend the store status case)

**Interfaces:**
- Produces: `PRODUCT_SCOPES: frozenset[str]` and `require_tiktok_product_scope(scopes: frozenset[str]) -> None` raising `TikTokAuthorizationError("insufficient_scope")`. The scope key is read from Partner Center when building and recorded in the ledger; tests use the constant. `require_tiktok_scopes` (order and shop) is unchanged, so order reads and the older tests are unaffected.
- Produces: `TikTokGrantState.source: Literal["verified_authorization_exchange", "verified_token_refresh"]`.
- Produces: `store_connections.sql` reports `permissions_missing` when the grant lacks the product scope.

- [ ] **Step 1: Failing tests:** `test_product_scope_required_for_product_reads_only` (grant with order and shop scopes passes `require_tiktok_scopes`, fails `require_tiktok_product_scope`); `test_refresh_source_is_a_valid_grant` (builds a `TikTokGrantState` with the new source and inserts it through `insert_verified_grant`); `test_store_status_permissions_missing_without_product_scope`.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(tiktok): product permission check and refresh grant source`.

### Task 4: Pending stores and the first-connect context

**Files:**
- Modify: `csa/agents/ecommerce/stores.py`, `csa/agents/ecommerce/authorization.py`, `csa/agents/ecommerce/connections.py`
- Create: `db/sql/ecommerce/create_pending_store.sql`, `db/sql/ecommerce/lock_pending_credential.sql`
- Test: `tests/test_ecommerce_connections.py` (extend; follow its `setup`/`app`/`prepare` helpers)

**Interfaces:**
- Produces (my call, narrower than making `StoreBinding.shop_id` optional): `PendingStoreBinding(client_id: UUID, record_id: UUID, platform: Literal["tiktok_shop"])` in `stores.py`; `TikTokFirstConnectContext(binding: PendingStoreBinding, app_id: str, app_revision: int, credential_generation: int)` in `authorization.py`. `StoreBinding`, `TikTokAuthorizationContext` and every reader stay as strict as today: a pending store is never readable.
- Produces: `async def create_pending_store(conn, client_id: UUID) -> UUID` (inserts a `channel_credentials` row, `channel='tiktok_shop'`, `shop_id NULL`, and returns its id).
- Produces: `async def begin_first_authorization(pool, context: TikTokFirstConnectContext) -> StartedAuthorization` (same single-use, sha256-only state, 10-minute lifetime; fences the app and locks the pending row at its generation). `AuthorizationAttempt.context` accepts either context type, told apart by the presence of `binding.shop_id`.

- [ ] **Step 1: Failing tests:** `test_first_connect_attempt_stores_only_state_hash`; `test_pending_store_not_readable_by_dispatch` (`begin_read` against a pending store's record raises `TikTokAuthorizationError`); `test_pending_store_lock_fails_after_generation_change`; `test_existing_reconnect_unchanged` (re-run one existing reconnect assertion).
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(tiktok): pending stores and first-connect attempts`.

### Task 5: First-connect verification and completion

**Files:**
- Modify: `csa/agents/ecommerce/authorization_verifier.py`, `csa/agents/ecommerce/connections.py`, `csa/agents/ecommerce/adapters/tiktok_shop.py`
- Create: `db/sql/ecommerce/complete_first_credential.sql`
- Test: `tests/test_tiktok_authorization_verifier.py`, `tests/test_ecommerce_connections.py` (extend)

**Interfaces:**
- Produces: `VerifiedFirstConnectResult` (the fields of `VerifiedAuthorizationResult`, with `context: TikTokFirstConnectContext` and `shop_id: str`); `TikTokFirstConnectVerifier(load_app, clock)` whose `__call__(context, authorization_code) -> VerifiedFirstConnectResult` exchanges the code once, fetches the authorized shops fresh, and requires **exactly one** shop (`multiple_shops` for several, `no_authorized_shop` for none); requires `require_tiktok_scopes` and `require_tiktok_product_scope`.
- Produces: `async def complete_first_authorization(pool, client_id, attempt_id, *, state, authorization_code, verify, encrypt) -> TikTokGrantState`. In one transaction: same burn-first sequence as `complete_authorization`; sets `shop_id` on the pending row where it is NULL and the generation matches; the unique index turns a shop already active for another client into `TikTokAuthorizationError("shop_in_use")`, revealing nothing about the other client; stores encrypted tokens, grant (`source='verified_authorization_exchange'`) and encrypted shop cipher; bumps the generation.
- The reconnect path (`complete_authorization`) is unchanged: the fresh shop must equal the stored `shop_id`, else `shop_mismatch` (add that safe code if only `binding_mismatch` exists today).

- [ ] **Step 1: Failing tests:** `test_first_connect_binds_the_single_shop`; `test_multiple_shops_fails_and_stores_nothing` (attempt stays consumed, pending row's `shop_id` still NULL, no grant); `test_no_authorized_shop_fails`; `test_missing_product_scope_fails_insufficient_scope`; `test_shop_active_for_another_client_fails_shop_in_use_without_leaking`; `test_reconnect_shop_mismatch_changes_nothing`; `test_first_connect_logs_no_token_or_code` (the existing log-capture pattern).
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(tiktok): first connect binds the single authorized shop`.

### Task 6: Token refresh as a verified event

**Files:**
- Create: `csa/agents/ecommerce/token_refresh.py`, `csa/agents/ecommerce/refresh.py`, `db/sql/ecommerce/list_refresh_due.sql`, `db/sql/ecommerce/mark_store_expired.sql`
- Test: `tests/test_tiktok_token_refresh.py`

**Interfaces:**
- Produces: `async def refresh_tiktok_token(*, app_key: SecretStr, app_secret: SecretStr, refresh_token: SecretStr, now: datetime) -> TikTokTokenResponse` (GET `/api/v2/token/refresh`, `grant_type=refresh_token`; same transport rules as `token_exchange.py`: zero retries, bounded body, no URL logging; the endpoint contract is checked against Partner Center and recorded in the ledger).
- Produces: `RefreshOutcome = Literal["refreshed", "expired", "skipped"]`; `async def refresh_store(pool, client_id: UUID, store_id: UUID, *, load_app: AppLoader, encrypt, decrypt, clock) -> RefreshOutcome`. Sequence: call refresh once; fetch authorized shops fresh with the new token; stored `shop_id` must match exactly (`require_authorized_shop`); then in one transaction replace the encrypted tokens (generation bumps via the fence), insert a new grant (`verified_token_refresh`, scopes from the response, else carried over from the old grant; expiry the earlier of grant and access-token expiry) and a new shop binding. A rejection or shop mismatch calls `mark_store_expired` (store reads as `expired`; no retry). A lost response leaves the old credential untouched.
- Produces: `async def list_refresh_due(conn, client_id, *, within: timedelta) -> list[UUID]`: stores whose access token expires within 24 hours **or** whose active grant's `app_revision` differs from the current app revision (this is how connections survive a secret rotation without reconnecting; grants are immutable, so a refresh at the new revision makes the new grant).

- [ ] **Step 1: Failing tests:** `test_refresh_success_bumps_generation_and_records_refresh_grant`; `test_refresh_rejected_marks_store_expired_without_retry` (mock transport sees exactly one request); `test_refresh_shop_mismatch_marks_expired_keeps_nothing_new`; `test_lost_response_keeps_old_credential_and_next_run_retries`; `test_scopes_carried_over_when_response_omits_them`; `test_rotation_refresh_moves_grant_to_new_app_revision` (disable then enable the app, refresh, new grant carries the new revision); `test_refresh_logs_no_token`.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(tiktok): verified token refresh`.

### Task 7: Extended order snapshot and order reads by time window

**Files:**
- Modify: `csa/agents/ecommerce/tiktok_models.py`, `csa/agents/ecommerce/adapters/tiktok_shop.py` (`_Order`/normalize and `search_order_snapshot_page`), `csa/agents/ecommerce/dispatch.py` (`TikTokReadRequest`), `csa/agents/ecommerce/reader.py`
- Test: `tests/test_tiktok_snapshots.py`, `tests/test_tiktok_order_reads.py`, `tests/test_ecommerce_dispatch.py` (extend)

**Interfaces:**
- Produces: `TikTokOrderSnapshot` gains `create_time: AwareDatetime | None`, `update_time: AwareDatetime | None`, `payment.sub_total: Decimal | None`, `payment.shipping_fee: Decimal | None`, `payment.discount: Decimal | None`, `shipping: TikTokShippingSnapshot | None` (`recipient_name`, `phone`, `address_text`, `region`: all optional strings, because TikTok often masks them), `packages: tuple[TikTokPackageSnapshot, ...]` (`tracking_number`, `carrier`), `paid_at: AwareDatetime | None`. Models stay `extra="ignore"`; all new fields optional so existing fixtures still validate.
- Produces: `TikTokReadRequest` gains `updated_from: AwareDatetime | None` and `updated_to: AwareDatetime | None` for `kind="search"` (mutually valid only with search); the adapter sends them as the update-time filter; paging and `page_token` unchanged.
- Folding of lines is done in Task 11, not here (this layer stays provider-shaped).

- [ ] **Step 1: Failing tests:** `test_snapshot_parses_address_tracking_and_times_from_synthetic_payload`; `test_masked_or_absent_address_still_validates`; `test_search_window_sent_in_request_body` (assert the mock transport's request body contains the window and nothing about secrets in logs); `test_detail_request_rejects_window`.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(tiktok): order snapshots carry address, tracking and times; window search`.

### Task 8: Product, variant and stock reads

**Files:**
- Modify: `csa/agents/ecommerce/tiktok_models.py`, `csa/agents/ecommerce/adapters/tiktok_shop.py`, `csa/agents/ecommerce/dispatch.py`, `csa/agents/ecommerce/reader.py`, `csa/agents/ecommerce/authorization.py`
- Test: `tests/test_tiktok_product_reads.py`, `tests/test_ecommerce_dispatch.py` (extend)

**Interfaces:**
- Produces: `TikTokSkuSnapshot(id, seller_sku, variant_name, price: Decimal, currency, stock_qty: int)`; `TikTokProductSnapshot(id, name, status, image_url | None, skus: tuple[TikTokSkuSnapshot, ...], update_time | None)`; `TikTokProductSnapshotPage(items, next_page_token)`. `stock_qty` is the sum across warehouses and is rejected below 0.
- Produces: `TikTokReadRequest.kind` gains `"product_search"` (limit, `page_token`, optional `updated_from`/`updated_to`) and `"product_detail"` (`product_id`); the verified reader's `read` returns the matching snapshot. Product reads call `require_tiktok_product_scope` in addition to the existing grant checks. Generalize `read_verified_order` into `read_verified(pool, client_id, operation_id, resolve)` and keep `read_verified_order` as a thin alias so existing tests pass.
- A live stock read for one product is `product_detail` (inventory is part of the SKU snapshot); there is no separate inventory kind.

- [ ] **Step 1: Failing tests:** `test_product_page_parses_variants_and_sums_stock`; `test_product_read_requires_product_scope` (grant without it fails closed before any HTTP request); `test_product_detail_requires_product_id`; `test_negative_stock_rejected`; `test_product_reads_never_retry` (one request on a 500).
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(tiktok): verified product and stock reads`.

### Task 9: Console connect, callback and disconnect routes

**Files:**
- Create: `csa/console/tiktok_connect.py`, `db/sql/marketplace/get_store_for_connect.sql`
- Modify: `csa/console/business.py` (include the sub-router inside `build_business_router` so the package gate and `business_actor` apply; admin check added per route)
- Test: `tests/test_console_tiktok_connect.py` (fixtures from `tests/test_console_business.py`: `SessionWorld`, `activate`; the package is activated through the backfill insert the services tests use, because it is not sellable until Task 22)

**Interfaces:**
- Produces: `POST /console/v1/business/stores/tiktok/connect` body `{store_id: UUID | None}` returning `{authorization_url: str}`: `https://services.tiktokshop.com/open/authorize?service_id=<TIKTOK_SERVICE_ID>&state=<state>` (the Malaysia authorize host is checked in Partner Center and recorded in the ledger). With `store_id` it is a reconnect of that store (`begin_authorization`); without it, `create_pending_store` then `begin_first_authorization`. 503 `tiktok_unavailable` when `Settings.tiktok_enabled` is false.
- Produces: the state string sent to TikTok is `<client_id hex 32><attempt_id hex 32><secret>`; only the secret tail is hashed and passed to `complete_*`. The callback splits it.
- Produces: `GET /console/v1/business/stores/tiktok/callback?code&state[&error]` behind the session; allowed for `platform_admin`, or for an Admin whose client equals the state's client. Always answers **303** with `Cache-Control: no-store` to `/portal/business/stores?tiktok=connected` (company users) or `/business/stores?client_id=<id>&tiktok=connected` (platform admins), or the same with `tiktok=failed&reason=<code>`. `<code>` is one of: `tiktok_unavailable`, `provider_denied`, `state_invalid`, `multiple_shops`, `no_authorized_shop`, `shop_mismatch`, `shop_in_use`, `insufficient_scope`, `connect_failed`. On success it also queues the first sync (`backfill_orders` and `backfill_products`) through `enqueue_job` (Task 12). Tasks 12 and 14 come later, so build the two calls (`enqueue_job`, `disconnect_store`) behind small injected functions here, with test doubles, and replace the doubles with the real ones in Task 14's step 3 (that task's tests then cover the real path end to end).
- Produces: `POST /console/v1/business/stores/{store_id}/disconnect` (Admin only) calling `disconnect_store` (Task 14).
- Staff (`tenant_staff`) get 403 on all three; read-only members never reach the router.

- [ ] **Step 1: Failing tests:** `test_connect_denied_for_staff_and_other_tenant_admin`; `test_connect_returns_authorize_url_with_service_id_and_state_and_stores_only_hash`; `test_connect_503_when_tiktok_off`; `test_callback_first_connect_redirects_connected_for_company_user_and_platform_admin` (the two redirect roots); `test_callback_without_session_is_refused`; `test_callback_foreign_client_state_redirects_state_invalid_and_changes_nothing`; `test_callback_replayed_state_redirects_state_invalid`; `test_callback_provider_error_param_redirects_provider_denied`; `test_each_failure_reason_is_a_listed_safe_code` (parametrized over the verifier's error codes); `test_callback_never_echoes_code_state_or_exception_text` (response and captured logs).
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(console): connect, reconnect and disconnect TikTok Shop behind the Admin session`.

### Task 10: Remove the legacy flow

**Files:**
- Delete: `csa/channels/marketplace/oauth.py`, its tests (find with `grep -ln "marketplace.oauth\|build_tiktok_shop_auth_url" tests`)
- Modify: `csa/channels/marketplace/router.py` (delete authorize, callback and the `/webhook/{public_id}` variant; keep `POST /tiktok_shop/webhook` and the Shopee/Lazada drafts), `csa/channels/marketplace/__init__.py`
- Test: `tests/test_marketplace_ingress.py`, a new `tests/test_marketplace_router_legacy_removed.py`

**Interfaces:** Consumes nothing new. Produces: `/api/v1/channels/tiktok_shop/oauth/authorize`, `/oauth/callback` and `/webhook/{public_id}` answer 404; the webhook route stays until Task 13 rewires it.

- [ ] **Step 1: Failing test:** `test_legacy_oauth_and_endpoint_webhook_routes_are_gone` (404 on the three paths; Shopee and Lazada drafts still 501; the plain webhook route still exists).
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Delete and rewire; fix imports; remove the dead settings use of `CREDENTIALS_KEY` state signing if nothing else uses it.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `refactor(tiktok): remove the unauthenticated legacy connect flow`.

### Task 11: Mapping and idempotent upserts

**Files:**
- Create: `csa/marketplace/__init__.py`, `csa/marketplace/mapping.py`, `csa/marketplace/store.py`, `db/sql/marketplace/upsert_order.sql`, `db/sql/marketplace/insert_order_item.sql`, `db/sql/marketplace/upsert_listing.sql`
- Test: `tests/test_marketplace_mapping.py` (pure), `tests/test_marketplace_store.py` (database)

**Interfaces:**
- Produces: `map_status(provider_status: str, *, paid: bool) -> tuple[str, str]` returning `(local_status, payment_status)` per spec §6.3 (`UNPAID`→`pending_confirmation`; `ON_HOLD`/`AWAITING_SHIPMENT`/`AWAITING_COLLECTION`→`processing`; `PARTIALLY_SHIPPING`/`IN_TRANSIT`→`shipped`; `DELIVERED`/`COMPLETED`→`delivered`; `CANCELLED`→`cancelled`; unknown→`processing`; payment `paid` once paid else `unpaid`).
- Produces: `MappedOrder` (Pydantic: order columns, `lines: list[MappedLine]`) and `map_order(client_id, store_id, shop_id, snap: TikTokOrderSnapshot, now) -> MappedOrder`. `id` is `tt_<client_id hex>_<external_order_id>` (tenant-scoped; ruled after Task 11 review). Lines with the same `sku_id` and sale price fold into one line with `qty` = their count (TikTok 202309 lists one line per unit) and unit price from the line's sale price; `contact_id` stays NULL. Money is `Decimal`.
- Produces: `MappedListing` and `map_listings(client_id, store_id, product: TikTokProductSnapshot, now) -> list[MappedListing]` (one per SKU).
- Produces: `async def upsert_order(conn, order: MappedOrder) -> Literal["inserted","updated","stale"]`: `stale` when the stored `provider_updated_at` is newer; when `buyer_cleared_at` is set, `ship_to` stays cleared and never restored. `async def upsert_listings(conn, rows: list[MappedListing]) -> int`. Neither touches `products`, stock or reservations.

- [ ] **Step 1: Failing tests:** `test_status_table` (parametrized over every row, plus unknown→`processing`); `test_same_sku_lines_fold_into_one_qty`; `test_zero_lines_maps_without_error`; `test_masked_address_maps_to_ship_to_as_given`; `test_upsert_is_idempotent_and_newer_wins` (older `provider_updated_at` returns `stale`, row unchanged); `test_cleared_order_never_gets_buyer_data_back`; `test_listing_upsert_never_changes_local_products` (compare `products` rows before and after).
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(marketplace): map TikTok orders and listings into local tables`.

### Task 12: Sync queue and processor

**Files:**
- Create: `csa/marketplace/jobs.py`, `csa/marketplace/processor.py`, `db/sql/marketplace/enqueue_job.sql`, `claim_job.sql`, `requeue_stale_jobs.sql`, `finish_job.sql`, `list_connected_stores.sql`, `set_store_state.sql`
- Modify: `apps/worker/main.py` (start the loop next to `kb_ingest_loop`, only when `Settings.tiktok_enabled`)
- Test: `tests/test_marketplace_jobs.py`, `tests/test_marketplace_processor.py`

**Interfaces:**
- Produces: `JobKind = Literal["backfill_orders","backfill_products","order_refresh","product_refresh","catch_up"]`; `async def enqueue_job(conn, *, client_id, store_id, kind: JobKind, target: str | None = None) -> bool` (False when the pending-unique index folded it).
- Produces: `SyncConfig` (frozen dataclass: `max_attempts`, `stale_after_seconds`, `page_size`, `page_delay_seconds`, `backfill_days=90`); `MarketplaceSyncProcessor(owner_pool, app_pool, *, load_app, encrypt, decrypt, config)` with `async run_once() -> int` (per active tenant: requeue stale claims, claim one job with `FOR UPDATE SKIP LOCKED`, process it, per-store concurrency of 1) and `async refresh_due() -> int` (calls Task 6's `list_refresh_due` and `refresh_store`); `async def marketplace_sync_loop(processor, *, poll_seconds: float) -> None`.
- Processing: every TikTok call goes `begin_read` → `read_verified` (Tasks 7–8). Backfills page by `page_token` and checkpoint their cursor in `marketplace_store_state`, so a crash resumes; orders use the 90-day window by `update_time`. `order_refresh` and `product_refresh` fetch one detail and upsert. `catch_up` searches both since `catch_up_cursor`. On success set `orders_synced_at`/`products_synced_at`. Retries use backoff to `max_attempts`, then `failed` with `last_error_code` (a safe code) and **one** `notify` per store per failure episode (`problem_notified_at`), never per job. A store that is disconnected or expired cancels its pending jobs and skips.

- [ ] **Step 1: Failing tests:** `test_duplicate_triggers_fold_into_one_pending_job`; `test_backfill_pages_and_resumes_from_checkpoint_after_crash`; `test_order_refresh_upserts_one_order`; `test_failed_job_notifies_once_per_store`; `test_expired_store_cancels_pending_jobs`; `test_stale_claim_is_requeued`; `test_processor_makes_no_request_for_pending_or_disconnected_store`; `test_listing_sync_leaves_local_stock_untouched`.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(marketplace): sync queue and processor`.

### Task 13: Webhook rewrite

**Files:**
- Create: `csa/marketplace/webhook.py`
- Modify: `csa/channels/marketplace/router.py` (route calls the new handler), delete `csa/channels/marketplace/ingress.py` and its writes to `ingress_events`
- Test: `tests/test_marketplace_webhook.py`; remove or replace `tests/test_marketplace_ingress.py`

**Interfaces:**
- Produces: `class TikTokWebhookHandler(pool, app_secret: str | None)` with `async receive(self, *, raw_body: bytes, signature: str | None) -> WebhookReceipt(accepted: bool, queued: bool)`. Order: size limit; signature (HMAC with `TIKTOK_APP_SECRET`, header `Authorization`) before any parsing; parse; `resolve_tiktok_shop_route(shop_id)`; order event → `enqueue_job(order_refresh, target=order_id)`; product event → `product_refresh`; deauthorization → `disconnect_store(reason="deauthorized")`; unknown event or unknown shop → accepted and dropped (200, so TikTok stops retrying); no `ingress_events` row for any case. Duplicate delivery is idempotent through the job index. The handler only verifies, resolves and enqueues, so it answers fast. TikTok off: the route answers 404. Exact event names and numbers are checked in Partner Center and kept in one constant table, `EVENT_KINDS`.

- [ ] **Step 1: Failing tests:** `test_bad_or_missing_signature_rejected_before_parsing`; `test_order_event_queues_one_refresh_even_if_delivered_twice`; `test_product_event_queues_product_refresh`; `test_deauthorization_disconnects_store_and_cancels_jobs`; `test_deauthorization_during_running_job_stops_it` (job finishes as cancelled, no write after disconnect); `test_unknown_shop_and_unknown_event_are_acknowledged`; `test_no_ingress_events_row_written`; `test_webhook_404_when_tiktok_off`.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(marketplace): webhook queues refresh jobs and drops raw payloads`.

### Task 14: Disconnect and retention

**Files:**
- Create: `csa/marketplace/retention.py`, `csa/marketplace/disconnect.py`, `db/sql/marketplace/disconnect_store.sql`, `purge_orders.sql`, `purge_stores.sql`, `schedule_catch_up.sql`
- Modify: `csa/jobs/runner.py` (`build_default_registry`: register `marketplace_purge` every 6 hours and `marketplace_catch_up` every 3 hours, both `per_client=True`)
- Test: `tests/test_marketplace_retention.py`, `tests/test_jobs_registry.py` (extend the registry assertion)

**Interfaces:**
- Produces: `async def disconnect_store(pool, client_id: UUID, store_id: UUID, *, reason: Literal["admin","deauthorized","expired"]) -> None`: sets `disconnected_at` (the fence bumps the generation and disconnects the grant), nulls the encrypted tokens, cancels the store's pending jobs, keeps orders and listings read-only.
- Produces: `BUYER_DATA_DAYS = 90`, `DISCONNECT_PURGE_DAYS = 30`; `async def run_marketplace_purge(conn, client_id, clock, *, batch_size=100) -> dict[str, int]`: (a) clears `ship_to` and sets `buyer_cleared_at` on TikTok orders 90 days past `delivered`/`cancelled`; (b) for stores disconnected 30 days or more, clears buyer data on their orders and deletes their listings; (c) deletes abandoned pending stores (no `shop_id`, no pending attempt left). Keeps number, totals, statuses, dates and items.
- Produces: `async def run_marketplace_catch_up(conn, client_id, clock, *, batch_size=100) -> dict[str, int]`: enqueues one `catch_up` job per connected store. An `expired` store is shown as such for 30 days, then disconnected with reason `expired`.

- [ ] **Step 1: Failing tests:** `test_disconnect_nulls_tokens_cancels_jobs_and_keeps_orders`; `test_purge_clears_buyer_data_90_days_after_delivery_keeps_totals`; `test_purge_after_30_days_disconnected_deletes_listings_clears_orders`; `test_reconnect_within_30_days_resumes_without_purge`; `test_abandoned_pending_store_removed_but_one_with_live_attempt_kept`; `test_catch_up_enqueues_one_job_per_connected_store`; `test_default_registry_has_both_jobs`.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(marketplace): disconnect, retention and catch-up jobs`.

### Task 15: Local order flows ignore synced orders

**Files:**
- Modify (only if the audit finds a gap): `db/sql/jobs/order_draft_expiry.sql`, `db/sql/jobs/orphan_draft_expiry.sql`, `db/sql/orders/*.sql`, `csa/jobs/reconcile.py`
- Test: `tests/test_marketplace_local_flows.py`

**Interfaces:** none produced. The audit lists every `orders` writer and reader (`grep -rn "orders" db/sql csa --include=*.sql --include=*.py`), and the test pins that TikTok-channel rows with `contact_id NULL` are untouched.

- [ ] **Step 1: Failing tests:** `test_draft_expiry_jobs_leave_tiktok_orders_alone`; `test_reservation_reconcile_ignores_tiktok_orders`; `test_order_agent_status_never_returns_tiktok_orders` (a customer with a contact sees only own orders, even by typing a TikTok number); `test_order_confirm_and_cancel_refuse_tiktok_orders`.
- [ ] **Step 2: Run; expect FAIL or PASS.** Any failure is a gap to fix; if all pass, record the audit result in the task checkpoint and keep the tests.
- [ ] **Step 3: Fix gaps.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `test(marketplace): local order flows never touch synced TikTok orders`.

### Task 16: Business read APIs

**Files:**
- Modify: `csa/console/business.py`, `db/sql/business/list_orders.sql`, `get_order.sql`, `store_connections.sql` (sync state), `csa/agents/ecommerce/stores.py` (contracts)
- Create: `db/sql/business/list_listings.sql`, `db/sql/business/get_order_detail.sql`
- Test: `tests/test_console_business.py` (extend), `tests/test_console_contract_snapshot.py` (refresh the snapshot), `docs/console_openapi.json` regenerated at the end of Task 22

**Interfaces:**
- Produces: `BusinessOrder` gains `provider_status: str | None`, `synced_at: datetime | None`, `buyer_cleared: bool`; new `BusinessOrderDetail(BusinessOrder)` adding `lines: list[OrderLineOut]` (`sku`, `name`, `qty`, `unit_price`), `subtotal`, `shipping_fee`, `discount`, `ship_to: dict | None`. `GET /console/v1/business/orders/{id}` returns `BusinessOrderDetail`; items and ship-to are filled **only for `tiktok_shop` orders** (local orders stay as today, and the existing test's "not-for-console" assertion still holds).
- Produces: `OrderPage.marketplace_sync` and `ProductPage.marketplace_sync` become `Literal["connected","not_connected"]` (derived from an active TikTok store); `StoreRecord` gains `sync: StoreSync | None` (`orders_synced_at`, `products_synced_at`, `problem: Literal["sync_failing"] | None`) and `can_manage: bool`; the `Literal["unavailable"]` constants on `StoreConnection`, `StoreRecord` and `StorePage` are replaced with real values (`live_integration` and `provider_reads`: `"available"` once `authorization_recorded`, else `"unavailable"`).
- Produces: `GET /console/v1/business/listings` returning `ListingPage(items: list[BusinessListing], next_cursor)` where `BusinessListing` = `id, product_name, variant_name, seller_sku, price, currency, stock_qty, product_status, image_url, synced_at, same_sku_product: str | None` (the matching local product's name, computed at read time on equal SKU, never stored); filters `store_id`, `q`.

- [ ] **Step 1: Failing tests:** `test_tiktok_order_detail_has_lines_and_ship_to_for_staff`; `test_local_order_detail_still_hides_ship_to`; `test_cleared_order_shows_cleared_flag_and_no_ship_to`; `test_listings_page_isolates_tenants_and_matches_same_sku_at_read_time`; `test_listings_need_the_package_and_a_staff_or_admin_role`; `test_store_page_reports_sync_times_and_failing_problem`; `test_read_only_member_denied`.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `feat(console): TikTok order detail, listings and real store sync state`.

### Task 17: Store connections page

**Files:**
- Create: `web/components/console/business/StoreConnections.tsx` (moves `StoreRecords`/`StorePage` out of `BusinessWorkspace.tsx`), `web/lib/tiktok-connect.ts`
- Modify: `web/components/console/BusinessWorkspace.tsx`, `web/lib/business-view.ts` (store status words), `web/components/console/business/cards.tsx`
- Test: `web/tests/tiktok-connect.test.mjs`, `web/tests/business-view.test.mjs`, `tests/test_business_browser.py` (extend)
  (all paths relative to `CSA_WebConsole/csa-console/web/`)

**Interfaces:**
- Produces in `lib/tiktok-connect.ts`: `connectResult(search: URLSearchParams): { tone: "good" | "bad"; text: string } | null` (the one-line result, from `?tiktok=connected|failed&reason=<code>`, one plain sentence per code in Task 9's list); `syncLine(label: "Orders" | "Products", at: string | null, now: Date): string` ("Orders synced 3 min ago", "Orders not synced yet").
- Behavior: Admins and platform admins see **Connect TikTok Shop**, **Reconnect** and **Disconnect** (confirm text: "Stop syncing this shop? Orders stay visible for 30 days, then customer details are removed."); Staff see status only. Status words: Connected, Waiting for TikTok approval (reuses `missing_shop_id`), Needs reconnecting (`expired`, `revoked`, `app_unavailable`, `permissions_missing`, `binding_missing`), Disconnected. With TikTok off the Connect button is disabled with "TikTok Shop isn't set up on this server yet." The old "saved records only / coming soon / Partner Center eligibility" copy is replaced with true statements. After the result line shows, the query is cleared (platform admins keep `client_id`). `business-stores-list` and the other existing test ids stay.

- [ ] **Step 1: Failing tests:** node: `connectResult` for every reason code and for no query; `syncLine` at 3 minutes, 1 hour, never; `storeSummary` words for each status. Browser (Playwright, stubbed console calls, all three themes): admin sees Connect and Disconnect, staff sees neither; clicking Connect navigates to the returned URL; `?tiktok=failed&reason=multiple_shops` shows the sentence and clears the query; the pending store reads "Waiting for TikTok approval".
- [ ] **Step 2: Run `npm test` for the file, then the browser file; expect FAIL.** **Step 3: Implement with the kit (`Fold`, `StatusChips`, `DetailsRow`, `Notice`, `Panel`).** **Step 4: Run; expect PASS; then `npm run lint` and `npm run typecheck`.**
- [ ] **Step 5: Checkpoint.** Message: `feat(console): connect TikTok Shop from Store connections`.

### Task 18: Orders and Products pages

**Files:**
- Modify: `web/components/console/BusinessWorkspace.tsx`, `web/components/console/business/cards.tsx`, `web/lib/business-view.ts`, `web/lib/business.ts`
- Create: `web/components/console/business/ListingCard.tsx`
- Test: `web/tests/business-view.test.mjs`, `tests/test_business_browser.py` (extend), `tests/test_packaged_console.py` stubs if any new console call is added (run it before pushing)

**Interfaces:**
- Produces: order cards for `channel === "tiktok_shop"` show a "TikTok" badge, TikTok's status beside ours (`provider_status`), "Synced 3 min ago" (reuse `syncLine`), and a detail (the existing `DetailsRow`) that fetches `GET /orders/{id}` and shows lines, totals breakdown, ship-to as given, tracking, and "Customer details removed after 90 days" when `buyer_cleared`. The "Local update … not a marketplace sync timestamp" line is replaced for TikTok rows. The "About this page" and "What this page does not do yet" folds are rewritten to what is true.
- Produces: Products & stock gets a source switch (`StatusChips`, single mode): **Your catalogue** (today's view, unchanged) and **TikTok Shop** (`ListingCard`: product, variant, SKU, price, stock, status, "Synced …", "Same SKU as your product X"). No "check live stock" button (the assistants cover live checks).
- Produces: the open order is published as page context: `usePublishContext({ tab: "business", section, record: { kind: "order", id } })` when a card is open (see Task 20 for the server side).

- [ ] **Step 1: Failing tests:** node: badge and sync-line helpers; listing row text for a masked and a matched SKU. Browser (three themes, phone width): TikTok order card shows badge, both statuses and "Synced"; opening detail shows lines and ship-to; cleared order shows the notice; source switch shows listings and keeps the local list unchanged; the source filter still lists TikTok Shop.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS; then `npm run lint`, `npm run typecheck`.**
- [ ] **Step 5: Checkpoint.** Message: `feat(console): TikTok orders and listings on the Business pages`.

### Task 19: Customer assistant, read-only

**Files:**
- Modify: `csa/agents/ecommerce/agent.py`, `csa/agents/ecommerce/models.py` (`EcommerceInput`), `csa/turn/deps.py` (capability text, wiring), `csa/graph/policy/plan_rules.py` and the planner prompt in `csa/decide/llm.py` (route "TikTok order number" and "is X in stock" to the agent when the tenant has a connected store), `csa/console/agents.py` (E-commerce readiness text), `csa/agents/orders/agent.py` (stock fall-through, only if the audit in Task 15 shows it is needed)
- Create: `csa/marketplace/reads.py`, `db/sql/marketplace/order_status_by_number.sql`, `db/sql/marketplace/find_listings.sql`
- Test: `tests/test_ecommerce_agent.py` (rewrite for the new surface), `tests/test_marketplace_reads.py`, `tests/test_console_agents.py` (extend)

**Interfaces:**
- Produces: `EcommerceInput` becomes `action: Literal["order_status", "stock_check"]` with `order_number: str | None` and `query: str | None`; **every write action and `Shopee`/`Lazada` reach is removed**, so a customer message can never plan a TikTok write (`ACTION_RISK` has only `read` entries). The agent is constructed with a `MarketplaceReader` (Protocol: `order_status(ctx, order_number)`, `stock_check(ctx, query)`).
- Produces: `async def order_status_by_number(conn, client_id, order_number) -> OrderStatusFact | None` where `OrderStatusFact` holds only `status_text` (plain words), `tracking_no`, `carrier`, `shipped_or_delivered_on`: **never** name, address, items, totals or ship-to. Not found: "I couldn't find that TikTok order number. Please check it, or ask our staff." Limit: 10 lookups per conversation per hour, then "Please ask our staff for more help."
- Produces: stock flow: `OrderAgent`'s local answer comes first, exactly as today; only with **no local match** does the question go to `find_listings(conn, client_id, query, limit=5)`. One clear match: a live `product_detail` read through the fenced reader, updating the local copy as a side effect, reply with counts, price and "available on our TikTok Shop". Several matches: ask which variant, listing up to 5. Live read fails: if the copy is under 6 hours old, "8 were available as of 2 hours ago."; otherwise "I can't check right now. Our staff will confirm." (no staff handoff in v1; my call).
- Produces: the Agents page says E-commerce is ready when a shop is connected, and "Connect a TikTok Shop in Business management to use this." otherwise, replacing "No marketplace adapters are wired".

- [ ] **Step 1: Failing tests:** `test_input_model_has_no_write_actions` (validating `fulfill_order`, `sync_inventory`, `cancel_order`, `approve_refund` all raise); `test_order_status_reply_contains_no_pii_fields` (serialize the facts; assert absence of name, address, items, total strings seeded in the fixture); `test_unknown_order_number_reply`; `test_eleventh_lookup_in_an_hour_is_refused`; `test_local_product_answered_locally_without_calling_tiktok` (mock transport sees zero requests); `test_tiktok_only_item_checked_live_and_copy_updated`; `test_ambiguous_variants_prompt_lists_up_to_five`; `test_live_failure_with_fresh_copy_and_with_stale_copy`; `test_agent_needs_the_marketplaces_package` (capability gate denies without it); `test_agents_page_text_by_connection_state`.
- [ ] **Step 2: Run each file; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.** Run `tests/test_assistant_smoke.py` is NOT run (it calls a model); note in the checkpoint that the planner prompt changed and the owner runs the smoke after rollout.
- [ ] **Step 5: Checkpoint.** Message: `feat(assistant): read-only TikTok order status and stock answers for customers`.

### Task 20: Workspace assistant (staff)

**Files:**
- Modify: `csa/workspace/contracts.py` (`RecordRef.kind` adds `"product"`; `PageContext`), `csa/workspace/service.py` (`_RECORD_GATES["product"] = _TAB_GATES["business"]`; `_summarize` branch; stock read), `csa/workspace/planner.py` (a product read proposal and the `business_products` page hint), `web/lib/workspace-api.ts`, `web/lib/assistant-view.ts`
- Test: `tests/test_workspace_contracts.py`, `tests/test_workspace_planner.py`, `tests/test_console_workspace.py` (extend; each run one file at a time); `web/tests/assistant-view.test.mjs`

**Interfaces:**
- Produces: `RecordRef(kind="product", id=<listing id>)`; `_summarize` for an open TikTok order says "Order <redacted id>: status …, payment …, total … TikTok · synced <relative>" (ids and states only, as today); for a product it names the product and variant count, never raw ids beyond the redacted form.
- Produces: a read-only `product_stock_read` proposal: finds the listing in the local copy, then reads live with the same fenced reader as Task 19; reply gives exact per-variant counts with "checked live just now" or "as of …" when the live read fails. No proposal ever writes to TikTok.
- Keep `tests/test_workspace_contracts.py`'s navigation regex happy (children written href-first).

- [ ] **Step 1: Failing tests:** `test_product_record_gated_by_business_package`; `test_summarize_tiktok_order_is_redacted_and_says_synced`; `test_stock_read_live_and_fallback_wording`; `test_no_workspace_proposal_can_write_to_tiktok` (enumerate proposal kinds); `test_read_only_member_cannot_open_product_context`; node: `assistant-view` handles the product record kind.
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS; then `npm test` for the file, lint and typecheck.**
- [ ] **Step 5: Checkpoint.** Message: `feat(workspace): staff can ask about TikTok orders, products and stock`.

### Task 21: Deployment wiring and runbook

**Files:**
- Modify: `scripts/staging_secrets.py` (new `tiktok-defaults` action next to `whatsapp-defaults`), `deploy/compose.staging-pipeline.yml` (three secret files for gateway, worker and the `tiktok-app` one-shot profile), `deploy/staging-hooks.Caddyfile` (add `POST /api/v1/channels/tiktok_shop/webhook`; the WhatsApp matcher stays), `deploy/STAGING_PIPELINE.md` (a TikTok section), `docs/PROGRESS.md`
- Test: `tests/test_staging_secrets.py` (extend; find with `ls tests | grep staging_secrets`), `tests/test_staging_pipeline_files.py` if one exists for compose or Caddy shape

**Interfaces:**
- Produces: `provision_tiktok_defaults(secret_dir: Path, runtime_uid: int) -> list[str]` writing `disabled` into `pipeline/staging_tiktok_app_key`, `staging_tiktok_app_secret` and `staging_tiktok_service_id` when missing, never overwriting; the CLI action name is `tiktok-defaults`. `TIKTOK_SERVICE_ID` is mounted as a secret file like the others (it is public, but one handling path is simpler).
- The runbook section gives the owner **numbered, copy-ready commands**: (1) `tiktok-defaults`; (2) `migrate`; (3) recreate gateway, worker, delivery and frontend, TikTok still off; (4) write the three real values with `read -s` into the secret files (the owner does this; the assistant never reads them); (5) recreate services, then `python -m scripts.tiktok_app register` and `enable` through the `tiktok-app` profile; (6) the live hooks Caddy edit (one edit that also adds the still-missing WhatsApp matcher); (7) Partner Center: redirect URL `https://staging-console.fourstacks.my/console/v1/business/stores/tiktok/callback`, webhook URL on the hooks host, events (names checked in Partner Center); (8) confirm the console session cookie survives the return from TikTok (SameSite Lax, as Gmail's does); (9) confirm whether a non-owner shop may authorize an unpublished in-house app; (10) enable Marketplaces for the test company, connect the owner's own shop, run the done-means list.
- `docs/PROGRESS.md` gets the ledger entry: the contract facts verified in Partner Center (endpoint versions, event names, Malaysia authorize host, product scope key, refresh endpoint, rate limits seen), the rotation ruling from Task 6, the first-connect ruling from Tasks 4–5.

- [ ] **Step 1: Failing tests:** `test_tiktok_defaults_writes_disabled_and_never_overwrites`; `test_tiktok_defaults_modes_and_ownership_match_whatsapp`; compose and Caddy shape tests if present (`webhook` path forwarded, nothing else for TikTok).
- [ ] **Step 2: Run; expect FAIL.** **Step 3: Implement.** **Step 4: Run; expect PASS.**
- [ ] **Step 5: Checkpoint.** Message: `chore(deploy): tiktok secrets, compose, hooks route and runbook`.

### Task 22: Make `marketplaces` sellable (last commit) and final checks

**Files:**
- Modify: `csa/console/packages.py` (`marketplaces`: `sellable=True`; summary becomes "Connects your TikTok Shop so your assistant can check orders and stock for customers and your team."), `tests/test_console_packages.py:33`, `tests/test_console_services.py` (the loop at line 257 drops `marketplaces`; the `can_request` assertion at line 309 flips; the backfill-suspend case at lines 276–290 moves to `e_invoicing`), `tests/test_staging_roster.py` (the `_unsellable_package` case uses `e_invoicing`), `tests/test_services_browser.py:253` and `tests/test_services_redesign_browser.py:138,609,944` (stubs follow), `docs/console_openapi.json` (regenerated with `scripts/export_console_openapi.py`)
- Test: the files above

**Interfaces:** consumes everything. Produces the sellable package, nothing else. `e_invoicing` stays `sellable=False`.

- [ ] **Step 1: Update the tests first** (they now assert `marketplaces` can be requested and activated, and `e_invoicing` is refused); run `tests/test_console_packages.py`; expect FAIL.
- [ ] **Step 2: Flip the flag and rewrite the summary.** Run each changed test file one at a time; expect PASS.
- [ ] **Step 3: Regenerate `docs/console_openapi.json`** and run `tests/test_console_contract_snapshot.py`; expect PASS.
- [ ] **Step 4: Final checks:** `make lint`; the web checks (`npm test`, `npm run lint`, `npm run typecheck`); `tests/test_services_browser.py`, `tests/test_services_redesign_browser.py`, `tests/test_business_browser.py` and `tests/test_packaged_console.py`, one at a time. Report plainly what ran and what did not (the full suite is CI's job).
- [ ] **Step 5: Checkpoint.** Message: `feat(console): the Marketplaces package is now sellable`. Then the owner does the rollout steps in `deploy/STAGING_PIPELINE.md` and the staging proof (spec §2 "done means") before any client is given the package.

---

## Self-review (spec coverage)

- Connect and reconnect, Admin only, callback and redirects: Tasks 4, 5, 9, 17. Multiple shops, mismatch, in-use: 5. Legacy removal: 2 (data), 10 (code). Refresh and rotation: 6. Settings, registry, operator command, staging wiring: 1, 21.
- Data model, status mapping, line folding, idempotent upserts, retention: 2, 11, 14. Jobs, backfill, webhook, catch-up, freshness, deauthorization: 12, 13, 14. Local flows untouched: 15 (and the stock-never-touched test in 11, 12).
- Business pages, order detail, listings, store state: 16, 17, 18. Customer assistant (status only, own catalogue first, live check, limits, read-only): 19. Workspace assistant (order context, product kind, stock read): 18, 20.
- Sellable flip last: 22. Rollout, Partner Center, secrets with `read -s`, Caddy: 21.
- Not in the plan, by design: any TikTok write, TikTok chat, Shopee/Lazada, linking buyers to contacts, publishing the app, a staff "check live stock" button, a staff handoff on a failed stock check.
- Type names used across tasks: `TikTokFirstConnectContext`, `PendingStoreBinding`, `VerifiedFirstConnectResult`, `begin_first_authorization`, `complete_first_authorization`, `refresh_store`, `list_refresh_due`, `AppLoader`, `read_verified`, `MappedOrder`, `upsert_order`, `enqueue_job`, `disconnect_store`, `BusinessOrderDetail`, `BusinessListing`, `OrderStatusFact` (each defined once, in the task that produces it).
