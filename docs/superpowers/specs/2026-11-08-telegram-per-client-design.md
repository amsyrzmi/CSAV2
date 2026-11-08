# Telegram per client: platform-admin setup of one bot per company

Date: 2026-11-08. Status: draft for owner approval. Migration block: 0494 (the 0490-0499 channels block).

## Intent

Each client company that has customer support gets its own Telegram bot. WhatsApp is waiting on
business verification, so Telegram is the live channel for now. The platform admin (the owner's team),
not the client, sets the bot up: they paste the client's BotFather token and the client's staff chat,
and the console proves both work before saving anything. The same code must work on production, where
today only a staging-only operator script can register a bot.

A working setup has two halves, and both are in scope:

1. **Customer bot**: the company's bot receives customers' messages at a webhook that resolves to
   that company, and replies as that bot.
2. **Staff chat**: escalations, approval requests, order notices and staff button taps go to a staff
   Telegram chat (usually a group), sent by the same bot. Nothing in the console sets this up today
   (only `db/sql/ingress/seed_dev_staff_target.sql`, a dev seed), so a bot alone is not a usable setup.

## Goals

- A platform admin, acting on one company, can connect, replace the token of, check, and disconnect
  that company's bot from the Services page, and set that company's staff chat.
- One company has at most one active bot, and one bot serves at most one company, enforced by the
  database, not by a lock in a script.
- The bot token and webhook secret are stored encrypted, bound to their row and column (AAD), and
  never logged, returned, or echoed back to the browser after saving.
- Nothing is stored until Telegram has accepted the token (`getMe`). A connect that cannot register
  its webhook never shows as connected.
- A client admin sees the status read-only, with no token field.
- The staging operator script keeps working unchanged (see "Staging script").

## Non-goals

- Client self-service (the routes are written so opening writes to a client admin later is one
  permission change, but that is not this project).
- TikTok Shop chat. It is the next design in this session and is blocked on which Partner Center app
  serves client shops.
- Capturing the staff chat id automatically from `/start`. The platform admin pastes the id.
- More than one bot per company, Telegram group customer conversations, bot profile management
  (name, picture, commands), and moving conversation history between companies.
- Changing how messages are processed once received (ingress, agents, delivery).

## Anti-goals (a failure even if the code works)

- A bot stored but silently receiving nothing, shown as "Connected".
- Any path where company A can end up receiving company B's customers, or a stranger claiming a bot
  that is already in use (the 409 must not name the other company).
- The token in a log line, an exception message, an audit row, an API response, or a URL we log.
- A staff chat marked verified without a delivered test message.
- The connect path ever relying on a lock in a script instead of database-level uniqueness.

## Constraints from the code as it is

- Routing is already per company. `channel_credentials` (client_id, channel='telegram',
  shop_id = bot id, encrypted `access_token`, `meta.telegram_webhook_secret_enc`) plus
  `webhook_endpoints` (`public_id`, unique per credential) resolve `/hooks/<public_id>` to the
  company. Ingress (`csa/channels/ingress.py`) verifies the secret header with the AAD
  `meta.telegram_webhook_secret_enc` and returns 404 for a disabled endpoint.
- The staff notifier (`csa/notify/sender.py`, `db/sql/notify/get_telegram_credential.sql`) picks the
  latest Telegram credential **of the client** with a token. With two active rows for one company it
  would pick whichever was updated last. Hence the per-company unique index below.
- `channel_credentials.disconnected_at` exists (0491, added for TikTok). There is no uniqueness on
  Telegram `shop_id` across companies: staging relies on an advisory lock in the script, production
  has nothing.
- `scripts/staging_test_bot.py` is stricter than this design needs: `bind_bot` refuses a bot with a
  different or ambiguous tenant binding (a bot cannot move tenants,
  `tests/test_staging_pipeline.py::test_bot_binding_is_atomic_encrypted_and_cannot_move_tenants`),
  refuses a rotated token ("rotation requires a separate procedure"), and refuses any existing
  webhook unless `--replace-webhook`. Its `validate_target` only matches the Compose names
  (`staging-db`, `csa_staging`, `csa`), and production is stacked on the same Compose files, so it is
  an operator convention, not a production guard.
- `TelegramAdapter` has `send_message`, `get_updates`, `delete_webhook` but no `getMe`, `setWebhook`
  or `getWebhookInfo`.
- The WhatsApp connect (`csa/console/channels.py`, routes under `/console/v1/channels/whatsapp`) is
  the pattern: `session_only` and `package_gate("customer_support")` dependencies, `require_member`
  (a platform admin passes it for any company), verify with the provider before storing, `audit(...)`,
  and a 409 for a duplicate.
- `staff_notification_targets` has UNIQUE (client_id, channel, address), `verified_at`, `is_enabled`,
  default `events`, default `min_priority='normal'`.
- Telegram allows one webhook per bot, requires HTTPS, and `secret_token` of 1-256 characters from
  `A-Za-z0-9_-`.
- Repo rules: no real addresses in git, tests import `EXPECTED_SCHEMA_VERSION` (stays at the highest
  applied migration), the console OpenAPI file and `docs/CONSOLE_API.md` are regenerated.

## Approaches considered

- **A. Console panel plus one shared service (chosen by the owner).** Routes under
  `/console/v1/channels/telegram`, a service module holding all Telegram logic, the staging script
  reduced to a wrapper. Trade-off: new adapter calls and a webhook-origin setting, but the
  least-privileged and most auditable path, and the one production can use.
- **B. Extend the operator script to production.** Smallest change, but every onboarding needs a
  shell on the production host, the token passes through a terminal, there is no status, replace or
  staff-chat step for support, and the script has no real production guard to lean on. Rejected.
- **C. Client admins paste their own token.** Rejected by the owner's rule that the platform admin
  sets it up; the same routes can open to client admins later.

## Design

### Database (migration 0494)

Two partial unique indexes on `channel_credentials`, both `WHERE channel = 'telegram' AND
disconnected_at IS NULL`:

- `(shop_id)`: a bot is active for one company across the deployment.
- `(client_id)`: a company has one active bot.

The migration first checks for existing violations and raises with the offending ids if any exist,
instead of silently disconnecting rows; its header comment gives the owner the query to run on staging
first. No new tables. Rollback: drop both indexes. Number 0494 sits in the TikTok block because that
track is in this same session (TikTok chat takes 0495 onward); `db/migrations/README.md` is updated to
say the block holds TikTok Shop connect and the channels work.

Because the console runs under row-level security, the service cannot see another company's row. The
"bot in use" 409 therefore comes from the unique violation, told apart by index name (the WhatsApp
connect does the same for its number), not from a pre-read.

### Adapter (`csa/channels/telegram.py`)

New static methods on `TelegramAdapter`, in the style of `send_message` (secret-free error codes,
URL never in an exception):

- `get_me(token)` returning bot id, username, `is_bot`.
- `set_webhook(token, url, secret_token)` with `allowed_updates=["message", "callback_query"]` (what
  the staging script registers today; an edited message is not something ingress should turn into a
  new customer message) and `drop_pending_updates=False`.
- `get_webhook_info(token)` returning the registered url, pending count, and the last error text.
- `delete_webhook` stays as is.

### Service (`csa/channels/telegram_connect.py`, new)

One module, no HTTP types, used by the routes and by the staging script. Operations:

- `connect(pool, client_id, token, *, replace_webhook, staff_chat_id=None)`
- `set_staff_chat(pool, client_id, chat_id)`
- `check(pool, client_id)` (live `getWebhookInfo`, compared with the url we expect)
- `disconnect(pool, client_id)`

Rules:

1. Token shape check first (`<digits>:<url-safe string>`), then `getMe`; a rejected token is a 422,
   a Telegram outage a 502, nothing stored either way.
2. No network call inside a database transaction. Order: getMe, then a short transaction that takes
   the advisory lock `telegram-bot:<bot id>` and writes the encrypted rows, then `setWebhook`, then a
   second short transaction recording `meta.telegram_webhook_registered_at`. A connect that fails at
   `setWebhook` leaves a row with no registered timestamp: status shows "Webhook not registered",
   and calling connect again with the same token completes it (idempotent).
3. **Same company, same bot id** is a replace of the token (the owner regenerated it in BotFather):
   update the encrypted token, issue a **new** webhook secret, keep the endpoint and `public_id`,
   re-register the webhook. **Same company, different bot id** is a 409 `telegram_bot_replace`
   (disconnect first). **Another company has this bot active** is a 409 `telegram_bot_in_use` (the
   unique index is the authority; the check only gives the friendly error).
4. Before `setWebhook`, call `getWebhookInfo`. If the bot already has a webhook at a different URL and
   `replace_webhook` is false, refuse with 409 `telegram_webhook_in_use` carrying the host only. The
   panel shows a confirm ("this bot is wired to something else, replace it?") that sends
   `replace_webhook=true`. This mirrors the staging script's `--replace-webhook`.
5. Webhook URL is `<origin>/hooks/<public_id>` where origin is the new setting
   `HOOKS_PUBLIC_BASE_URL`, falling back to `WHATSAPP_CALLBACK_BASE_URL` (already documented as the
   shared hooks address), and **never** to `PUBLIC_BASE_URL`: on staging and production that is the
   console origin and does not forward `/hooks/*`, so a webhook registered there would receive nothing.
   If neither hooks setting is set, connect returns 503 `hooks_origin_not_configured` and stores
   nothing. `public_id` and the secret come from `secrets.token_urlsafe(32)`.
   Advisory lock key: `telegram-bot:<bot id>`.
6. Disconnect: best-effort `deleteWebhook` (a failure is reported, not fatal), then in one transaction
   set `disconnected_at`, null `access_token` and drop the encrypted secret from meta, disable the
   endpoint (`is_enabled=false`, `disabled_reason='disconnected'`), and disable the company's Telegram
   staff targets (not delete). Reconnecting makes a new credential and endpoint. The bot is then free
   for any company to connect, which is the way to move a bot.
7. Staff chat: `chat_id` is an integer string (negative for groups and supergroups). The service
   sends a test message through the company's bot first; only a delivered message writes the
   `staff_notification_targets` row with `verified_at = now()` and default events and priority.
   Setting a new chat replaces the company's Telegram staff target(s) after the new one is verified.
   Telegram's `400 chat not found` becomes "the bot is not in that chat, or the id is wrong"; `403`
   becomes "the bot was removed or blocked". Staff chat requires a connected bot.
8. Audit rows through `csa.console.kb_uploads.audit`: `channels.telegram.connected`,
   `.token_replaced`, `.staff_chat_set`, `.disconnected`. The helper takes only a target string, so the
   target is the credential id (the bot id is on that row); never the token.
9. Logging: error codes only. The token must not appear in any logger call or in a chained
   exception's text (the adapter raises `from None` where the cause carries the URL).

### Ingress and notifier hardening

- `get_telegram_credential.sql` gains `AND disconnected_at IS NULL` so a disconnected row can never
  be picked for staff notices.
- Ingress already returns 404 for a disabled endpoint; a test pins that a disconnected company's
  webhook is 404 and sends no message.

### Console API (under `/console/v1/channels/telegram`)

Same dependencies as WhatsApp (`session_only`, `package_gate("customer_support")`). Reads use
`require_member(..., write=False)`. Writes additionally require `actor.platform_admin`; a client
admin gets 403 `platform_admin_only`.

| Route | Who | Purpose |
|---|---|---|
| `GET /telegram` | members | status: connected, bot username and id, webhook registered, connected at, staff chat (address, verified, last test) |
| `PUT /telegram` | platform admin | body `{bot_token, staff_chat_id?, replace_webhook?}`; connect or replace token |
| `PUT /telegram/staff-chat` | platform admin | body `{chat_id}`; verify with a test message and save |
| `POST /telegram/check` | platform admin | live `getWebhookInfo`: url matches, pending updates, last error text |
| `DELETE /telegram` | platform admin | disconnect |

The token is a `SecretStr` in the request model and is never in any response. `GET` is database only.

### Web (Services page, next to the WhatsApp panel)

A `TelegramPanel` component and `lib/channels-api.ts` additions. A platform admin sees: token field
(password type, cleared after saving), staff chat id field with two lines of help (add the bot to the
staff group, then paste the group id), buttons Connect or Replace token, Send test message, Check
webhook, Disconnect (with confirm). A client admin sees the status read-only and the line "Ask the
platform team to change this". Error codes map to plain sentences. No token ever shows after saving,
only `@username`.

### Staging script

`scripts/staging_test_bot.py` is **left untouched in this PR**. Its rules differ from the service's
(no rotation, any existing webhook refused, its own lock key `staging-telegram:<bot id>`), moving it
onto the service would rewrite its tests for no user-visible gain, and the panel is the path for
production. Rows the script writes are valid under the new indexes (`disconnected_at` null, one per
bot). A bot the script bound can be freed by disconnecting that company from the panel. A later
cleanup can retire the script once the panel is proven on staging.

## Testing

- Adapter: `get_me`, `set_webhook`, `get_webhook_info` against an `httpx.MockTransport`, including a
  failure whose exception text does not contain the token.
- Service and routes with a fake Telegram: connect, replace token (same bot), different bot (409),
  bot in use by another company (409), foreign webhook (409, then OK with replace), webhook failure
  then retry, disconnect, reconnect, staff chat verified only on delivery, each Telegram error mapped.
- Database: the two partial unique indexes refuse a second active row and allow one after disconnect;
  tenant isolation (company A's session cannot read B's row).
- Authorization: client admin gets 403 on writes and 200 on read; platform admin acts on a chosen
  company; package gate refusal.
- Ingress: webhook of a disconnected company is 404; a rotated secret rejects the old one.
- Notifier: ignores a disconnected credential.
- Secrets: a test that the audit row, logs and responses contain no token or secret.
- Web: component test for both roles; OpenAPI and `docs/CONSOLE_API.md` regenerated.

## Rollout and risks

- One migration (0494); under the staging tool any migration stops gateway, frontend, worker and
  delivery while it runs. The owner checks the duplicate query first (above).
- New setting `HOOKS_PUBLIC_BASE_URL`. Staging needs no manual step: the gateway already gets
  `WHATSAPP_CALLBACK_BASE_URL` from `CSA_STAGING_HOOKS_ORIGIN` (`deploy/compose.staging-pipeline.yml`),
  which Telegram falls back to; the plan also adds `HOOKS_PUBLIC_BASE_URL` to compose from the same
  variable. On production it must be the public HTTPS address that forwards `/hooks/*` to the gateway
  (set in the production overlay, `deploy/PRODUCTION.md`); until then connect refuses cleanly.
- Real Telegram behaviour is unverified until the owner connects a test bot on staging: a retest row
  goes into `docs/ASSISTANT_RETEST.md`.
- Staging's current dev bot is bound to a test company by the script. Connecting the same bot from the
  panel to another company returns `telegram_bot_in_use` until the script's binding is disconnected.

## Left to the builder

- Exact SQL file names, response model names, and error-code spelling beyond those above.
- How the panel locates the company a platform admin is acting on (copy WhatsApp's `tenant`
  dependency and the Services page's existing company switch).

## Calls I made (for the owner to overrule)

- Routes sit under `/console/v1/channels/telegram`, matching WhatsApp, not `/console/v1/telegram`.
- Replacing a token for the same bot rotates the webhook secret (the old token is presumed
  compromised) while keeping the `public_id`.
- Disconnect nulls the stored token and keeps the row, rather than deleting it.
- The migration refuses on pre-existing duplicates instead of fixing them.
- A foreign webhook on the bot is refused unless the admin confirms.
- The staging script is not refactored onto the service (it is stricter and has its own tests); this
  departs from the earlier proposal that it become a thin wrapper.
- The webhook origin never falls back to the console origin; connect refuses until a hooks origin is set.
- Webhook updates are message and callback_query only.
- A real production guard for the staging script is out of scope here; flagged as a pre-existing gap.
- Migration 0494 in the TikTok block.
