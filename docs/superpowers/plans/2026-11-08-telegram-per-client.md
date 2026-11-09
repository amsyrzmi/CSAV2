# Telegram per client: platform-admin bot setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A platform admin connects, replaces, checks and disconnects one Telegram bot per company, and sets that company's staff chat, from the Services page.

**Architecture:** Three new `TelegramAdapter` calls (`getMe`, `setWebhook`, `getWebhookInfo`); a service module `csa/console/telegram_connect.py` holding all connect logic (no HTTP types); routes added to the existing `csa/console/channels.py` router under `/console/v1/channels/telegram`; migration 0494 adds two partial unique indexes so "one bot per company, one company per bot" is enforced by the database. The web gets a `TelegramPanel` beside `WhatsAppPanel`.

**Tech Stack:** Python 3.12, FastAPI, psycopg (SQL files under `db/sql/`, run through `db.q`), pydantic v2, httpx (`MockTransport` in tests), pytest with testcontainers Postgres, Next.js web console (node `--test` files in `web/tests`).

**Spec:** `docs/superpowers/specs/2026-11-08-telegram-per-client-design.md` (approved 2026-11-08). One departure from it, ruled here: the service lives in `csa/console/telegram_connect.py`, not `csa/channels/`, because it writes audit rows and takes `ConsoleActor`, and `csa.channels` does not import `csa.console`.

## Global Constraints

- Migration number **0494**; `EXPECTED_SCHEMA_VERSION` (`apps/gateway/main.py`) is not changed (highest applied stays 0541; tests import it, never hardcode).
- Routes under `/console/v1/channels/telegram`, same router and dependencies as WhatsApp (`session_only`, `package_gate("customer_support")`). Reads: `require_member(..., write=False)`. Writes: `actor.platform_admin` only, else 403 `platform_admin_only`.
- Token and webhook secret: stored with `crypto.encrypt(..., crypto.credential_aad("channel_credentials", <column>, str(credential_id)))`; the token column is `access_token`, the secret lives in `meta["telegram_webhook_secret_enc"]` with AAD column `"meta.telegram_webhook_secret_enc"` (constants `TELEGRAM_WEBHOOK_SECRET_META_KEY`, `TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN` in `csa/channels/ingress.py`). Never in a log line, audit row, response, URL we log, or exception text.
- `set_webhook` registers `allowed_updates=["message", "callback_query"]`, `drop_pending_updates=False`; `secret_token` from `secrets.token_urlsafe(32)`; `public_id` from `secrets.token_urlsafe(32)`; webhook URL `<origin>/hooks/<public_id>`.
- Webhook origin: `HOOKS_PUBLIC_BASE_URL`, else `WHATSAPP_CALLBACK_BASE_URL`, never `PUBLIC_BASE_URL`; neither set means 503 `hooks_origin_not_configured`, nothing stored.
- Advisory lock key `telegram-bot:<bot id>`. No network call inside a database transaction.
- Error codes (exact): `telegram_token_malformed` (422), `telegram_token_rejected` (422), `telegram_unreachable` (502), `hooks_origin_not_configured` (503), `telegram_bot_in_use` (409), `telegram_bot_replace` (409), `telegram_webhook_in_use` (409), `telegram_connect_in_progress` (409), `telegram_webhook_failed` (502), `telegram_not_connected` (409), `telegram_chat_unreachable` (422, bot not in chat or wrong id), `telegram_chat_forbidden` (422, bot removed or blocked), `telegram_chat_invalid` (422, malformed chat id), `platform_admin_only` (403).
- Audit actions: `channels.telegram.connected`, `.token_replaced`, `.staff_chat_set`, `.disconnected`; target is the credential id string; helper is `csa.console.kb_uploads.audit(conn, actor, client_id, action, target)`.
- The staging script `scripts/staging_test_bot.py` is not modified.
- No real addresses, tokens or chat ids in git; tests use synthetic values. Editor hook reflows `.py` edits to 120 columns and strips imports not yet used: add an import in the same edit as its first use.
- Verify with: focused tests per task; `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` before the final commit. The full suite is CI's job.

## Review Focus

Failure modes the spec implies that no single task's headline tests cover; each has its test in the owning task.

1. A Telegram failure whose exception text or log line contains the bot URL (and so the token): Task 2 and Task 4 tests scan captured logs and `str(exc)`.
2. Existing data or tests that already hold two Telegram credentials for one company or one bot for two (breaks the new indexes): Task 1 runs every test that touches `channel_credentials` and the migration pre-check.
3. `setWebhook` fails after the row is stored, then the admin retries: Task 4 test makes the second attempt succeed with one credential row and one endpoint.
4. Two connects of the same bot for two companies at once: Task 4 test relies on the unique index (409 `telegram_bot_in_use`), not on the lock.
5. Disconnect then reconnect the same bot, and a staff chat id that is a negative supergroup id: Task 5 tests.

---

### Task 1: Migration 0494 (two partial unique indexes)

**Files:**
- Create: `db/migrations/0494_telegram_per_client.sql`
- Test: `tests/test_telegram_connect_schema.py`
- Modify: `db/migrations/README.md` (the `0490`–`0499` line)

**Interfaces:**
- Produces: indexes `channel_credentials_telegram_bot_key` (unique on `(shop_id)` where `channel = 'telegram' AND shop_id IS NOT NULL AND disconnected_at IS NULL`) and `channel_credentials_telegram_client_key` (unique on `(client_id)` where `channel = 'telegram' AND disconnected_at IS NULL`). Tasks 4-5 read these names from the unique violation's constraint name.

- [ ] **Step 1: Write the failing schema test** modelled on `tests/test_bookings_google_schema.py` (same imports and helpers: `_owner_dsn`, `_run_migrations`, `SCHEMA_BASELINE`). Tests: a second active telegram row with the same `shop_id` for another client raises `UniqueViolation` naming `channel_credentials_telegram_bot_key`; a second active row for the same client raises it naming `..._client_key`; after setting `disconnected_at` on the first, both inserts succeed; whatsapp and tiktok rows are not affected; the migration refuses (raises, naming the client id) when duplicates already exist (apply 0494 on a database seeded with two active telegram rows for one client via the baseline-then-migrate helper); `csa_app` can INSERT/UPDATE `channel_credentials` and `webhook_endpoints` and INSERT/UPDATE/DELETE `staff_notification_targets` under a tenant session (assert the grants; if DELETE is missing the migration grants it).
- [ ] **Step 2: Run** `uv run pytest tests/test_telegram_connect_schema.py -q`. Expected: FAIL (indexes missing).
- [ ] **Step 3: Write `0494_telegram_per_client.sql`** with the header style of 0491 (preconditions 0491, rollback = drop both indexes, the query the owner runs on staging first: active telegram rows grouped by `client_id` and by `shop_id` having count > 1). A `DO` block first raises an exception listing offending ids if any group has count > 1, then creates the two indexes. Add the staff-targets grant only if Step 1 showed it missing.
- [ ] **Step 4: Update `db/migrations/README.md`**: the `0490`–`0499` line says TikTok Shop connect and the channels work, with 0494 described in one sentence.
- [ ] **Step 5: Run** `uv run pytest tests/test_telegram_connect_schema.py tests/test_bookings_schema.py tests/test_migrate.py -q` plus `grep -rln "channel_credentials" tests` files that seed telegram rows (`tests/test_ingress*.py`, `tests/test_staging_pipeline.py`, `tests/test_dev_telegram_setup.py`, notifier tests). Expected: PASS; if a seeding test now violates an index, fix the test's seed to use distinct bots per company (not the index).
- [ ] **Step 6: Commit** `feat(telegram): one active bot per company and per bot (migration 0494)`.

---

### Task 2: Adapter calls `get_me`, `set_webhook`, `get_webhook_info`

**Files:**
- Modify: `csa/channels/telegram.py`
- Test: `tests/test_telegram_connect_adapter.py`

**Interfaces:**
- Produces (all static methods on `TelegramAdapter`, each `http_client: httpx.AsyncClient | None = None` keyword; errors raise `TelegramPollingError(error_code)` using the existing `_telegram_api_post`, which already never puts the URL in an exception):
  - `get_me(bot_token: str, *, http_client=None) -> TelegramBotProfile` where `TelegramBotProfile(bot_id: int, username: str | None)`; a non-`is_bot` or `ok=false` response raises `TelegramPollingError("telegram_api_rejected")`; HTTP 401 raises `telegram_http_401`.
  - `set_webhook(bot_token: str, *, url: str, secret_token: str, http_client=None) -> None`; payload `{"url", "secret_token", "allowed_updates": ["message", "callback_query"], "drop_pending_updates": False}`; `ok` not true raises `telegram_api_rejected`.
  - `get_webhook_info(bot_token: str, *, http_client=None) -> TelegramWebhookInfo` where `TelegramWebhookInfo(url: str, pending_update_count: int, last_error_message: str | None)` (empty `url` means none).

- [ ] **Step 1: Write failing tests** with `httpx.MockTransport`: each method posts to `/bot<token>/<method>` with the exact payload; a 401 gives `telegram_http_401`; a transport error gives `telegram_transport_error`; an `ok:false` body gives `telegram_api_rejected`; for every failure path `str(exc)`, `repr(exc)`, `exc.__cause__`, `exc.__context__` text and captured structlog output contain neither the token nor `api.telegram.org/bot`.
- [ ] **Step 2: Run** `uv run pytest tests/test_telegram_connect_adapter.py -q`. Expected: FAIL (attributes missing).
- [ ] **Step 3: Implement** the three methods and the two result models next to the existing ones, validating responses with small pydantic models like `_TelegramBooleanResponse`; invalid shape raises `telegram_invalid_response`.
- [ ] **Step 4: Run** the new file plus `tests/test_telegram.py tests/test_telegram_poller.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(telegram): getMe, setWebhook and getWebhookInfo on the adapter`.

---

### Task 3: Hooks origin setting and compose wiring

**Files:**
- Modify: `csa/core/settings.py`, `deploy/compose.staging-pipeline.yml` (gateway environment, next to `WHATSAPP_CALLBACK_BASE_URL`)
- Test: `tests/test_telegram_hooks_origin.py` (and extend `tests/test_staging_compose.py` only if it pins the gateway env list)

**Interfaces:**
- Produces: `Settings.HOOKS_PUBLIC_BASE_URL: HttpUrl | None = None` and property `Settings.hooks_origin -> str | None`: `HOOKS_PUBLIC_BASE_URL` else `WHATSAPP_CALLBACK_BASE_URL`, with trailing `/` stripped, else `None`. Never `PUBLIC_BASE_URL`.

- [ ] **Step 1: Write failing tests**: with only `PUBLIC_BASE_URL` set `hooks_origin` is `None`; with `WHATSAPP_CALLBACK_BASE_URL` set it returns that origin without a trailing slash; with both hooks settings the Telegram one wins; the compose file's gateway environment contains `HOOKS_PUBLIC_BASE_URL` fed from `CSA_STAGING_HOOKS_ORIGIN`.
- [ ] **Step 2: Run** `uv run pytest tests/test_telegram_hooks_origin.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the field and property; add `HOOKS_PUBLIC_BASE_URL: ${CSA_STAGING_HOOKS_ORIGIN:?approved staging hooks HTTPS origin required}` to the gateway.
- [ ] **Step 4: Run** the new file plus `tests/test_staging_compose.py tests/test_settings*.py -q` (the settings tests that exist). Expected: PASS.
- [ ] **Step 5: Commit** `feat(telegram): hooks origin setting, wired on staging`.

---

### Task 4: Connect service (connect, replace token, status)

**Files:**
- Create: `csa/console/telegram_connect.py`, `db/sql/console/telegram_get.sql`, `telegram_insert.sql`, `telegram_endpoint_insert.sql`, `telegram_replace_token.sql`, `telegram_mark_registered.sql`
- Test: `tests/test_telegram_connect.py`

**Interfaces:**
- Consumes: `TelegramAdapter.get_me/set_webhook/get_webhook_info`; `Settings.hooks_origin`; `audit`; `crypto`; `tx(pool, str(client_id))`.
- Produces (module `csa.console.telegram_connect`):
  - `class TelegramConnectError(Exception)` with attributes `status: int`, `code: str`.
  - `class TelegramStaffChat(Contract)`: `address: str`, `verified: bool`.
  - `class TelegramStatus(Contract)`: `connected: bool` (active row with token and webhook registered), `bot_id: str | None`, `bot_username: str | None`, `webhook_registered: bool`, `connected_at: datetime | None`, `staff_chat: TelegramStaffChat | None`, `hooks_ready: bool`.
  - `async def get_status(pool, client_id: UUID) -> TelegramStatus` (database only).
  - `async def connect(pool, actor: ConsoleActor, client_id: UUID, token: str, *, replace_webhook: bool = False, http_client: httpx.AsyncClient | None = None) -> TelegramStatus`.
  - Token shape regex `^[0-9]{5,20}:[A-Za-z0-9_-]{30,64}$`; failure raises 422 `telegram_token_malformed` before any network call.

Algorithm for `connect` (the order is the requirement):
1. Shape check, then hooks origin check (503), then `get_me` (outside any transaction; `telegram_http_401`/`_404` map to 422 `telegram_token_rejected`, any other `TelegramPollingError` to 502 `telegram_unreachable`).
2. Read tx: the company's active telegram row. Same company, different `shop_id` than the bot id: 409 `telegram_bot_replace`. Expected URL is `<origin>/hooks/<existing public_id>` or none.
3. `get_webhook_info`; a non-empty `url` different from the expected URL is foreign: 409 `telegram_webhook_in_use` (detail carries host only) unless `replace_webhook`.
4. Write tx: advisory lock `telegram-bot:<bot id>` (`pg_advisory_xact_lock(hashtext(...))`), re-read the active row; none: insert credential (`shop_id = str(bot_id)`, sealed token, meta `{"bot_username", "telegram_webhook_secret_enc"}`) and endpoint (new `public_id`, name `Telegram @<username>`); same bot: replace sealed token and write a **new** sealed secret, keep endpoint, clear `telegram_webhook_registered_at`. A `UniqueViolation` is mapped by `exc.diag.constraint_name`: `channel_credentials_telegram_bot_key` is 409 `telegram_bot_in_use` (message never names another company); `..._client_key` is 409 `telegram_connect_in_progress`.
5. `set_webhook(url=<origin>/hooks/<public_id>, secret_token=<plain secret>)`; any failure: 502 `telegram_webhook_failed`; the row stays unregistered.
6. Second write tx: `meta.telegram_webhook_registered_at = now` and audit `connected` (new) or `token_replaced` (same bot).
7. Return `get_status`.

- [ ] **Step 1: Write failing tests** (docker Postgres; fixtures copied from `tests/test_console_channels.py`: `sessions`, platform-admin and tenant sessions, `activate(..., "customer_support")`, `crypto._key` monkeypatch, `csa.console.telegram_connect.get_settings` patched to a `SimpleNamespace(hooks_origin="https://hooks.example.test")`; a `TelegramStub` `MockTransport` answering `getMe`, `setWebhook`, `getWebhookInfo`, recording requests): (a) connect stores an encrypted token (ciphertext decrypts with the row's AAD, plaintext absent from the row), a sealed secret in meta, one endpoint, `shop_id` equal to the stub's bot id, status `connected` with `@username`; the `setWebhook` request carries the URL `https://hooks.example.test/hooks/<public_id>`, the secret, and `allowed_updates == ["message","callback_query"]`; (b) same bot again with a new token: same credential id and `public_id`, new secret, `token_replaced` audit row; (c) different bot id for the company: 409 `telegram_bot_replace`, nothing changed; (d) another company already holds the bot: 409 `telegram_bot_in_use` and the body never contains that company's id or name; (e) bot whose `getWebhookInfo` url is foreign: 409 `telegram_webhook_in_use`, no row, then success with `replace_webhook=True`; (f) bad token shape: 422 and zero requests to the stub; `getMe` 401: 422 `telegram_token_rejected`, no row; `getMe` outage: 502 `telegram_unreachable`; no hooks origin: 503 and no row, no `getMe` call; (g) `setWebhook` fails: 502 `telegram_webhook_failed`, status `webhook_registered` false and `connected` false; the retry with the same token succeeds with exactly one credential row and one endpoint for the company; (h) two companies connect the same bot concurrently (`asyncio.gather`): exactly one succeeds, the other 409 `telegram_bot_in_use`; (i) no audit row, log line, or exception text anywhere contains the token or the plaintext secret.
- [ ] **Step 2: Run** `uv run pytest tests/test_telegram_connect.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the module and SQL files per the algorithm. Use `crypto.encrypt` with the AAD constants from `csa/channels/ingress.py`. Keep network calls outside every `tx(...)` block.
- [ ] **Step 4: Run** `uv run pytest tests/test_telegram_connect.py tests/test_telegram_connect_schema.py tests/test_ingress.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(telegram): connect and replace a company's bot, verified before stored`.

---

### Task 5: Staff chat, check, disconnect, and notifier hardening

**Files:**
- Modify: `csa/console/telegram_connect.py`, `db/sql/notify/get_telegram_credential.sql`
- Create: `db/sql/console/telegram_disconnect.sql`, `telegram_staff_target_replace.sql`, `telegram_staff_targets_disable.sql`
- Test: `tests/test_telegram_connect.py` (extend), plus a notifier test beside the existing sender tests (find with `grep -rn get_telegram_credential tests`)

**Interfaces:**
- Consumes: Task 4 module, `TelegramAdapter.send_message`, `TelegramAdapter.delete_webhook`, `TelegramDeliveryError.error_code`.
- Produces:
  - `async def set_staff_chat(pool, actor, client_id, chat_id: str, *, http_client=None) -> TelegramStatus`. `chat_id` matches `^-?[0-9]{1,20}$` and is not `0`, else 422 `telegram_chat_invalid`. No active connected bot: 409 `telegram_not_connected`. Decrypts the token (AAD as stored), `send_message` a fixed test text; `telegram_http_400` becomes 422 `telegram_chat_unreachable`, `telegram_http_403` becomes 422 `telegram_chat_forbidden`, other failures 502 `telegram_unreachable`. Only after delivery: one tx deletes the company's telegram `staff_notification_targets`, inserts the new row with `verified_at = now()` (default events and priority), audits `staff_chat_set`.
  - `class TelegramCheck(Contract)`: `webhook_url_matches: bool`, `pending_update_count: int`, `last_error: str | None`; `async def check(pool, client_id, *, http_client=None) -> TelegramCheck` (live `getWebhookInfo`; 409 `telegram_not_connected` when none).
  - `async def disconnect(pool, actor, client_id, *, http_client=None) -> None`: read row and decrypt token; best-effort `delete_webhook` (failure logged by code, not raised); one tx sets `disconnected_at = now()`, `access_token = NULL`, removes the secret from meta, disables the endpoint (`is_enabled=false`, `disabled_reason='disconnected'`) and sets `is_enabled=false` on the company's telegram staff targets; audit `disconnected`. No active row: no-op, no audit.
  - `get_telegram_credential.sql` gains `AND disconnected_at IS NULL`.

- [ ] **Step 1: Write failing tests**: staff chat verified only after a delivered test message (stub `sendMessage` 200: target row has `verified_at`; the message went out under the company's own token, not another's); a negative supergroup id (`-1001234567890`) is accepted and stored as given; `400` and `403` map to the two 422 codes and leave any previous target untouched; replacing the chat deletes the old target; staff chat before connect is 409 `telegram_not_connected`; `check` reports a matching URL, pending count and last error text from the stub, and a foreign URL as not matching; disconnect calls `deleteWebhook`, nulls the token, disables the endpoint and the staff targets, still succeeds when `deleteWebhook` fails, and is a no-op twice; after disconnect the same bot can be connected by another company, and by the same company with a **new** `public_id` and endpoint, and the old `public_id` stays disabled; after disconnect `TelegramIngress` answers 404 for the old `public_id` and enqueues nothing (follow how `tests/test_ingress.py` drives `TelegramIngress`); the notifier credential query returns no row for a disconnected company.
- [ ] **Step 2: Run** `uv run pytest tests/test_telegram_connect.py -q` and the notifier test file. Expected: FAIL.
- [ ] **Step 3: Implement** the three operations and the SQL (including the one-line notifier query change).
- [ ] **Step 4: Run** `uv run pytest tests/test_telegram_connect.py tests/test_ingress.py tests/test_notify*.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(telegram): staff chat, webhook check and disconnect; notifier skips disconnected bots`.

---

### Task 6: Console routes, contract and API docs

**Files:**
- Modify: `csa/console/channels.py`, `docs/CONSOLE_API.md`, `docs/console_openapi.json` (regenerated, not hand-edited)
- Test: `tests/test_console_telegram.py`; `tests/test_console_contract_snapshot.py` must pass

**Interfaces:**
- Consumes: Task 4-5 service functions; `TelegramConnectError` mapped to `HTTPException(status, code)`.
- Produces (added to `build_channels_router`, prefix `/console/v1/channels`):
  - `GET /telegram -> TelegramStatus` (any member, `require_member(write=False)`).
  - `PUT /telegram` body `TelegramConnectIn(bot_token: SecretStr (min 20, max 200), staff_chat_id: str | None = None, replace_webhook: bool = False)` -> `TelegramStatus`. Connect first; if `staff_chat_id` is given, then `set_staff_chat`; a staff-chat failure raises its own error after the bot is connected (the panel reloads status).
  - `PUT /telegram/staff-chat` body `{chat_id: str}` -> `TelegramStatus`.
  - `POST /telegram/check -> TelegramCheck`.
  - `DELETE /telegram -> 204`.
  - Helper `require_platform_admin(request, client_id) -> ConsoleActor` in `channels.py`: `require_member(write=False)` then 403 `platform_admin_only` unless `actor.platform_admin`.
  - The HTTP client is `getattr(request.app.state, "telegram_http", None)`.

- [ ] **Step 1: Write failing tests** (fixtures as in `tests/test_console_channels.py`; the router is added with `sessions.app.include_router(build_channels_router())`): platform admin connects company A (200, `connected` true) and the response, `GET`, audit details and captured logs contain no token; a tenant admin gets 200 on `GET` and 403 `platform_admin_only` on `PUT`, `PUT staff-chat`, `POST check`, `DELETE`; a tenant admin of company B reading company A is refused as WhatsApp's is; a platform admin without `client_id` gets the existing 400; company without the `customer_support` package is refused by the gate; the machine token is refused (`session_only`); `PUT` with `staff_chat_id` sets both, and with a bad chat leaves the bot connected and returns 422 `telegram_chat_unreachable`; every `TelegramConnectError` code reaches the client as its status and code string.
- [ ] **Step 2: Run** `uv run pytest tests/test_console_telegram.py -q`. Expected: FAIL (404).
- [ ] **Step 3: Implement** the models, helper and five routes in `csa/console/channels.py`; add a short "Telegram bot" section to `docs/CONSOLE_API.md` (routes, who may call, the error code list, "token never returned"); run `uv run python -m scripts.export_console_openapi` and commit the regenerated `docs/console_openapi.json`.
- [ ] **Step 4: Run** `uv run pytest tests/test_console_telegram.py tests/test_console_channels.py tests/test_console_contract_snapshot.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(telegram): console routes for a platform admin to set up a company's bot`.

---

### Task 7: Web client and TelegramPanel

**Files:**
- Modify: `CSA_WebConsole/csa-console/web/lib/channels-api.ts`, `components/console/ServicesWorkspace.tsx` (render `TelegramPanel` after `WhatsAppPanel`, same visibility rule: `customer_support` active)
- Create: `components/console/TelegramPanel.tsx`
- Test: `web/tests/telegram-transport.test.mjs` (same harness as `tests/channels-transport.test.mjs`)

**Interfaces:**
- Produces in `lib/channels-api.ts`: types `TelegramStatus`, `TelegramCheck`; functions `getTelegram(tenantId)`, `connectTelegram(tenantId, {bot_token, staff_chat_id?, replace_webhook?})`, `setTelegramStaffChat(tenantId, chat_id)`, `checkTelegram(tenantId)`, `disconnectTelegram(tenantId)`, all using the existing `path`-style helper for `/console/v1/channels/telegram[...]` with `?client_id=` only when `tenantId` is set; and `telegramErrorLine(code: string): string`, a plain-words sentence for each error code in Global Constraints (unknown codes get a generic line).
- `TelegramPanel({ tenantId, platform })`: `platform` is the platform-admin flag the Services page already passes (`canManage` is not used: client admins are read-only here).

- [ ] **Step 1: Write failing node tests**: `getTelegram(null)` and `getTelegram("a b/1")` build the right paths; the bot token appears only in the body of the `PUT` and in no URL, header or `console.log`; `connectTelegram` sends `replace_webhook` only when set; `telegramErrorLine` returns a distinct non-empty sentence for each listed code and a fallback for an unknown one; no returned sentence contains the word "token" followed by a value.
- [ ] **Step 2: Run** the web node test as earlier branches did (`node --test tests/telegram-transport.test.mjs` with node from `/home/amsyrzmi/.cache/pyright-python/nodeenv/bin`, run from the web directory). Expected: FAIL.
- [ ] **Step 3: Implement** the client functions and the panel. Platform admin view: token field (`type="password"`, cleared after any save), staff chat id field with two lines of help (add the bot to the staff group, then paste the group id), buttons Connect or Replace token, Send test message (saves the staff chat), Check webhook (shows match, pending count, last error), Disconnect (via `ConfirmAction`). A `telegram_webhook_in_use` error shows a confirm ("This bot is wired to something else. Replace it?") that retries with `replace_webhook: true`. Client-admin view: status only (`@username`, webhook state, staff chat verified) and "Ask the platform team to change this". When `hooks_ready` is false the platform admin sees "Telegram isn't available yet: set the hooks address" and no form. Use `Panel`, `Notice`, `field`, `action` from `./ui` like `WhatsAppPanel`. Add `data-testid`s `telegram-connected`, `telegram-token`, `telegram-staff-chat`, `telegram-connect`, `telegram-disconnect`.
- [ ] **Step 4: Run** the new node test, the existing `tests/channels-transport.test.mjs` and `tests/theme-literals.test.mjs`, then `node scripts/check-isolated.mjs`, the TypeScript check and eslint as `package.json` defines them. Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): Telegram panel on Services for the platform admin`.

---

### Task 8: Runbook, retest rows, and final verification

**Files:**
- Modify: `deploy/STAGING_PIPELINE.md` (new section: connecting a client's Telegram bot, the hooks origin, the staging script is separate), `docs/ASSISTANT_RETEST.md` (rows), `docs/PROGRESS.md` (one entry, in that file's style)

- [ ] **Step 1: Write the runbook section**: what the platform admin needs per client (BotFather token; the staff group with the bot added; the group id), the order (connect, then staff chat, then Check webhook), what each error means, how to move a bot (disconnect first), that `HOOKS_PUBLIC_BASE_URL` on production must be the public HTTPS hooks address, and that connecting from the panel does not touch a bot bound by `scripts/staging_test_bot.py` unless disconnected.
- [ ] **Step 2: Add retest rows** to `docs/ASSISTANT_RETEST.md` (manual): connect a real test bot, message it, see the Inbox thread; staff chat test message arrives; an escalation lands in the staff chat; replace token; disconnect then message (bot stays silent); a client admin sees no token field.
- [ ] **Step 3: Run the checks**: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports`; then all focused suites from Tasks 1-7 together plus `tests/test_staging_pipeline.py tests/test_staging_compose.py tests/test_dev_telegram_setup.py tests/test_whatsapp_browser.py -q`. Expected: all pass, no warnings added.
- [ ] **Step 4: Commit** `docs(telegram): runbook, retest rows and progress entry`.
