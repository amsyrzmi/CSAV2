# TikTok Shop connect, orders and products (Project 1) design

Date: 27 Oct 2026. Approved by the owner 28 Oct 2026. Its own track, separate from lane A's A2. Branch `feat/tiktok-shop-connect`, based on main `225bfd2`.
Follow-on: Project 2, the TikTok chat channel. It is designed separately and reuses everything here.

## 1. Intent

Fourstacks clients sell on TikTok Shop. The owner's TikTok Shop business status was approved on 27 Oct 2026. That approval removes the gate that held the M6B work offline: in October there was no registered company, so no app could be published.

The owner has created the Partner Center app, which is not published yet:

- Service ID `7695771782398363413`.
- API packages enabled: **Shop Authorized Information**, **Order Information**, **Product Basic** and **Customer Service**. Customer Service is for Project 2.
- The App Key and App Secret are held by the owner and become deployment secrets.

The owner wants three things:

1. A client admin connects their TikTok shop from the console.
2. The client's staff see their customers' TikTok orders in full, and the shop's products and stock.
3. Customers can ask about their order and about stock through the assistant. Staff can ask the workspace assistant the same product questions.

The owner wants us to work from **our own copy** of the shop's orders and products. TikTok's webhooks and API are used only where appropriate, to keep that copy current. The one exception is customer and staff stock questions: they check TikTok live for that one product, because stock must be accurate.

## 2. Goals, non-goals and anti-goals

### Goals

- A client admin clicks **Connect TikTok Shop** on the Business tab, approves on TikTok, and lands back on the Business tab with the shop connected. Reconnect works the same way.
- On first connect, the last 90 days of orders and the full product catalogue are copied in.
- New and changed orders and products then arrive without anyone acting.
- Staff browse and search TikTok orders in full detail on the existing Business orders views. They also browse TikTok products, with variants, price and stock, on the Business products view, labelled by source.
- Each TikTok order and listing shows when it was last synced.
- Customers on Telegram or WhatsApp who give a TikTok order number get its status and tracking. They never get the name, address or items (owner decision).
- Customers asking "is this in stock?" about an item that exists only on TikTok get an answer checked live with TikTok for that product. Items in the client's own catalogue are answered from local stock, as today.
- The workspace assistant summarises an open TikTok order, and answers staff product and stock questions with the same live check.

### Done means

1. A client admin connects a real shop on staging.
2. Within minutes, staff can browse its orders and products.
3. A new TikTok order then appears in our copy with no manual step.
4. The assistant correctly answers one order-number question and one stock question on Telegram or WhatsApp.
5. The workspace assistant answers a staff stock question.

### Non-goals (Project 1)

- Any write to TikTok: shipping, stock, prices, cancellations, refunds or listings.
- TikTok chat (Project 2).
- Linking TikTok buyers to our contacts. That arrives with chat, where TikTok tells us who the buyer is.
- Shopee and Lazada.
- Publishing the app on TikTok's app store. That is an owner step, when other sellers need to find the app.
- Merging TikTok stock into local stock.

### Anti-goals

These would make this a failure even if it technically worked:

- **Wrong-shop binding.** A shop is attached to the wrong client, or one client can read another client's shop.
- **Local stock overwritten.** A TikTok sync changes the client's own `products` stock or reservations, which would break Telegram ordering.
- **Order details leaked.** Name, address or items reach someone who only typed an order number.
- **Tokens or secrets exposed.** App secrets, tokens or raw provider errors appear in logs, pages, responses or chat.
- **Silent staleness.** Our copy is shown as if it were live TikTok.
- **Legacy flow kept.** The unauthenticated legacy connect flow survives alongside the new one.

## 3. Owner decisions (27 Oct 2026)

| Decision | Choice |
|---|---|
| Order of work | Project 1 (connect, orders, products) first, then Project 2 (TikTok chat) |
| What an order number alone reveals (Telegram/WhatsApp) | Status and tracking only. Staff in the console see everything |
| Staff order view | Our own database copy, kept current by webhooks and the API |
| Products | Copied the same way as orders; staff and the assistant answer stock questions |
| Stock answers | Check TikTok live for that one product before answering |
| Where the copy lives | Approach C (section 5): orders in the existing `orders` tables, products in a new TikTok listings table |
| Workspace assistant | Its product and stock questions are in Project 1 |
| Turning TikTok on for a client (28 Oct) | The `marketplaces` package becomes sellable, so a platform admin enables it per client on Services. This is a sanctioned one-line exception to the "don't touch `csa/console/packages.py`" rule |
| Whose stock a customer hears (28 Oct) | The client's own catalogue first, because that is what customers can order through us. TikTok is checked live only for items that exist only on TikTok, and the reply says "available on our TikTok Shop". Counts are shown the same way `OrderAgent` shows them today |
| Buyer details while connected (28 Oct) | Cleared from each TikTok order 90 days after it is delivered or cancelled; number, totals and statuses stay. If TikTok's partner data policy sets a shorter limit, that limit wins |
| Token refresh (my call, accepted 28 Oct) | A refresh is a sanctioned extension of M6B (§6.2) |
| Legacy stores (my call, accepted 28 Oct) | Stores written by the old unauthenticated flow are removed by migration (§6.2) |

## 4. Constraints

**Repository rules (AGENTS.md):**
- Isolated dev Postgres and synthetic fixtures only.
- Never contact TikTok from tests.
- No secrets in source, fixtures, logs, docs or prompts.
- Every outbound TikTok call in tests uses `httpx.MockTransport`.

**Program rules** ([client growth program](2026-10-27-client-growth-program-design.md) §5, §9):
- TikTok has its own migration range, `0490`–`0499`, so it never collides with lane A (`0470`–`0479`, A2 support refinements) or lane B (`0480`–`0489`). The first migration also adds this range to `db/migrations/README.md`.
- `EXPECTED_SCHEMA_VERSION` is set at merge to the highest merged version. Main is on `0480` as of `225bfd2`.
- A new console router is registered in `scripts/export_console_openapi.py`, and `docs/console_openapi.json` is regenerated as the last commit.
- Do not touch `csa/console/packages.py`. The one owner-sanctioned exception is setting `marketplaces` to `sellable=True`, along with the test in `tests/test_console_services.py` that asserts it is refused.
- UI text follows `docs/WRITING_GUIDE.md`.

**Tenant isolation:**
- Tenant tables use RLS through `tx(pool, client_id)`, and RLS is never FORCEd.
- Lookups across tenants (webhook to tenant) use the same owner-pool or definer pattern as WhatsApp's phone-number routing.

**M6B rules carried forward:**
These come from the M6B handoffs `docs/archive/handoffs/2026-10-18-m6b-verifier-next-session.md` and the console-source migration plan's M6B sections. Where this design deliberately changes one, it says so (§6.2).
- **App identity.** The app key and secret come from a trusted registry with an exact app ID, revision and enabled state. The mapping is never inferred from credential `meta`.
- **Credential fencing.**
  - Credentials are fenced by `credential_generation`.
  - Every refresh or replacement bumps the generation.
  - Attempts are single-use. The state is stored only as a sha256. The authorization code is burned before the exchange.
- **What a shop match proves.** A matching shop is not evidence of order scope. The order endpoint itself enforces permission.
- **Provider calls.**
  - Signed requests are never retried or logged.
  - There is no retry or fallback after a provider rejects a request or its response is lost.
  - Query strings that carry secrets are kept out of HTTP logging, telemetry and errors.
- **Errors.** Error payloads carry safe codes, never provider text.

## 5. Approaches considered

The main question was where the TikTok copy lives.

| | A. Everything in the existing tables | B. Separate TikTok tables for both | **C. Mixed (chosen)** |
|---|---|---|---|
| Orders | `orders` and `order_items` | new TikTok orders tables | **`orders` and `order_items`**, tagged `tiktok_shop` and linked to the store |
| Products | matched into `products` by SKU, overwriting stock | new TikTok listings table | **new TikTok listings table** |
| Pro | Business tab and assistant almost unchanged | nothing local touched; trivial purge | orders reuse a table designed for marketplace rows (`channel`, `external_order_id`, `external_shop_id`, `ship_to`); local stock is never touched |
| Con | TikTok syncs overwrite local stock and break Telegram reservations; TikTok variants don't fit flat rows | every view and tool reads two places; duplicates `orders` | the products view and its tools read two sources |

The owner picked **C**. Orders are already per-channel, so nothing collides. Products differ: local `products` rows carry stock and reservations that Telegram ordering depends on, so TikTok listings get their own table.

## 6. Design

### 6.1 App configuration and deployment

**Settings** (`csa/core/settings.py`):

| Setting | Type | Purpose | Status |
|---|---|---|---|
| `TIKTOK_APP_KEY` | `str \| None` | app key | exists |
| `TIKTOK_APP_SECRET` | `SecretStr \| None` | app secret; also the webhook HMAC key | exists |
| `TIKTOK_SERVICE_ID` | `str \| None` | goes in the authorize URL | new |
| `TIKTOK_CALLBACK_BASE_URL` | `HttpUrl \| None` | base for the callback URL; the WhatsApp precedent falls back to `PUBLIC_BASE_URL` | new |
| `TIKTOK_APP_ID` | `str` | registry id, default `tiktok_shop` | new |

- The legacy code passes the app key as `service_id`. That is the bug this design removes.
- `TIKTOK_APP_KEY`, `TIKTOK_APP_SECRET` and `TIKTOK_SERVICE_ID` follow the WhatsApp convention: unset, empty or `disabled` means TikTok is off.
- **When TikTok is off:**
  - connect endpoints return 503 `tiktok_unavailable`;
  - the webhook returns 404;
  - sync jobs skip;
  - the Business tab shows Connect disabled with "TikTok Shop isn't set up on this server yet."

**App registry (`load_app`).** This is the first real implementation of `TikTokAppMaterial` loading, which until now existed only in tests.
- It returns the key and secret from settings, paired with the current row in `ecommerce_apps` for `TIKTOK_APP_ID`.
- The row must be `active`. The app revision comes from that row, never from settings.
- **Rotating the secret:**
  1. The operator disables the app row, which bumps the revision and fences every in-flight read.
  2. Swap the secret file.
  3. Recreate the services.
  4. Re-enable the app row, which bumps the revision again.
- Grants carry the revision they were made under. After a rotation, existing grants must keep working without reconnecting. The builder decides how a grant's stored revision is reconciled with the app's new revision, and must ledger that ruling, because the M6B fences currently compare exact revisions.

**Operator command.** A new one-shot, `python -m scripts.tiktok_app register|enable|disable`, wraps the existing `register_app`/`transition_app` helpers.
- Writing to `ecommerce_apps` needs the owner role, because `csa_app` has SELECT only.
- It runs as a compose profile with the same approval-variable guard as `migrate`.

**Staging wiring:**
- `scripts/staging_secrets.py` gains a `tiktok-defaults` action that writes `disabled` placeholders.
- `deploy/compose.staging-pipeline.yml` mounts the three secret files, sets `TIKTOK_CALLBACK_BASE_URL` from `CSA_STAGING_HOOKS_ORIGIN`, and adds the `tiktok-app` profile.
- `deploy/staging-hooks.Caddyfile` forwards `GET /api/v1/channels/tiktok_shop/oauth/callback` and `POST /api/v1/channels/tiktok_shop/webhook` to the gateway.
- `deploy/STAGING_PIPELINE.md` gains a TikTok section with:
  - secret files;
  - the app enable step;
  - Partner Center settings: redirect URL, webhook URL, events;
  - a test connect.

**Partner Center settings** (owner):
- Redirect URL: `https://hooks.fourstacks.my/api/v1/channels/tiktok_shop/oauth/callback`. This path is unchanged.
- Webhook URL: `https://hooks.fourstacks.my/api/v1/channels/tiktok_shop/webhook`.
- Events: order status change, product status/audit change, seller deauthorization.

Exact event names and type numbers must be checked against Partner Center when building.

### 6.2 Connect and reconnect

**Who:**
- `tenant_admin` and `platform_admin` can connect, reconnect and disconnect.
- `tenant_staff` see status only.
- Access requires the `marketplaces` package.

Connecting hands over the shop's data, so it is an admin act.

**Flow:**

1. **Start.** The admin clicks **Connect TikTok Shop**, or **Reconnect** on an existing store.
   - The console calls `POST /console/v1/business/stores/tiktok/connect`. For a reconnect it sends the `store_id`.
   - The server calls `begin_authorization` under the tenant's transaction.
   - It returns the TikTok authorize URL:
     - `https://services.tiktokshop.com/open/authorize?service_id=<TIKTOK_SERVICE_ID>&state=<state>`;
     - the state is random and stored only as a sha256, as today;
     - the attempt lifetime stays 10 minutes;
     - the authorize host for the Malaysia market must be checked when building.
   - The frontend navigates the browser to that URL.
2. **Approve.** The seller approves on TikTok. TikTok redirects to the callback with `code` and `state`.
3. **Callback.** `GET /api/v1/channels/tiktok_shop/oauth/callback` is unauthenticated by nature: the state is the authority.
   - It finds the attempt by the state hash and calls `complete_authorization` with the real `TikTokAuthorizationVerifier`. The attempt is burned before the token exchange.
   - The verifier exchanges the code once and fetches the authorized shops fresh.
   - The persistence step then, atomically:
     - stores encrypted tokens;
     - stores the grant and its scopes;
     - stores the shop binding with an encrypted shop cipher;
     - bumps `credential_generation`.
   - The response is a **303 redirect** to `{PUBLIC_BASE_URL}/business?tiktok=connected` or `?tiktok=failed&reason=<safe_code>`. No HTML page is rendered, which removes the unescaped-HTML and leaked-exception defects.
4. **Result.** The Business tab shows the outcome in plain words and queues the first sync (§6.4).

**First connect (deliberate extension of M6B).** M6B assumes the store row and its `shop_id` already exist and checks the fresh shop against them. A brand-new client has no store row and doesn't know its shop ID, so:

- A first connect creates a **pending store**: a `channel_credentials` row with `channel='tiktok_shop'` and `shop_id` NULL, shown as "Waiting for TikTok approval". The attempt context binds to that row.
- At completion, if the fresh authorized-shops response contains **exactly one** shop, that shop is bound and the row's `shop_id` is set in the same transaction.
- If it contains **several** shops, completion fails with `multiple_shops`: "This TikTok account has more than one shop. Contact support to connect the right one." Choosing among several shops is deferred.
- **Reconnect keeps M6B's rule:** the fresh shop must exactly match the stored `shop_id`. Otherwise the result is `shop_mismatch`, and nothing already stored changes.
- **A shop belongs to one client at a time.** Add a global unique index on active TikTok `shop_id`. Connecting a shop that is already active for another client fails with `shop_in_use`, which reveals nothing about the other client.
- Abandoned pending stores, whose attempts expired with no binding, are removed by the purge job (§6.6).

**Token refresh.** This is new, and it deliberately extends M6B. A scheduled per-client job refreshes access tokens that expire within the next 24 hours.
- It uses the refresh token: GET `/api/v2/token/refresh` with `grant_type=refresh_token`. The exact contract must be checked when building, the same way the M6B exchange contract was.
- **Why M6B needs extending.** Under the current rules a refresh cannot keep a usable grant:
  - any content change to `channel_credentials` fires `fence_ecommerce_credentials` (0320), which bumps the generation and disconnects the active grant;
  - grants are bound to one generation;
  - `ecommerce_grants.source` allows only `verified_authorization_exchange`.
- **The sanctioned extension.** A refresh becomes a verified event of its own:
  1. Call the refresh endpoint once.
  2. With the new access token, fetch the authorized shops fresh. The stored `shop_id` must match exactly, and the cipher check follows the existing `require_authorized_shop` rule.
  3. Then, in one transaction under the attempt-free credential fence:
     - replace the encrypted tokens and bump the generation;
     - record a **new grant** with source `verified_token_refresh` (a new allowed value), scopes from the refresh response (or carried over from the previous grant if the response omits them; the builder verifies which), and expiry as the earlier of grant and access-token expiry;
     - record a new shop binding.

  The previous grant ends as `superseded` or `disconnected` through the existing lifecycle. Reads in flight under the old generation fail their fence and are not retried.
- If the provider rejects the refresh, or the shop no longer matches, the store is marked `expired` and the admin sees **Reconnect**.
- There are no retries after a rejection. A lost response leaves the old credential, which keeps working until it expires, and the refresh is tried again on the next run. The builder confirms whether TikTok rotates the refresh token so that a lost response cannot strand the store.

**Legacy removal:**
- `csa/channels/marketplace/oauth.py` is deleted: `build_tiktok_shop_auth_url`, `exchange_tiktok_shop_token`, `save_tiktok_shop_credentials`, `create/verify_oauth_state`.
- The legacy authorize route and the `/webhook/{public_id}` variant are deleted from `router.py`, and the legacy tests go with them.
- The callback path is kept, rewired to the flow above.
- Shopee/Lazada draft routes are untouched.
- **Existing legacy stores.** The old callback took `client_id` from an unauthenticated query parameter, so any TikTok store it wrote was never verified.
  - A migration deletes every `channel_credentials` row with `channel='tiktok_shop'` that has no verified grant, together with its `webhook_endpoints` rows. It runs before the new global unique index on `shop_id` is created.
  - Rows that do have a verified grant are kept. None are expected, since the M6B flow was never wired.
  - A store that is needed again is connected fresh through the new flow.

### 6.3 Data model (migrations 0490+)

**Orders.** These go into the existing `public.orders` and `public.order_items` tables.
- `channel='tiktok_shop'`.
- `external_order_id` is TikTok's order ID and `external_shop_id` is the shop.
- New `store_id uuid` references the `channel_credentials` row.
- New `synced_at timestamptz` and `provider_updated_at timestamptz`.
- `orders.id` for synced rows is a server-generated text ID; the builder picks the format.
- A unique partial index on `(client_id, channel, external_order_id)` for marketplace rows makes upserts idempotent.
- `contact_id` stays NULL in Project 1.

Mapping:
- `ship_to` takes whatever TikTok provides; TikTok often masks it.
- `items` and `order_items` come from the line items.
- Money is stored as Decimal.
- `tracking_no` and `carrier` come from the package or line data.

**Status mapping.** TikTok to local, to be checked against current docs when building:

| TikTok status | Local `status` |
|---|---|
| `UNPAID` | `pending_confirmation` |
| `ON_HOLD`, `AWAITING_SHIPMENT`, `AWAITING_COLLECTION` | `processing` |
| `PARTIALLY_SHIPPING`, `IN_TRANSIT` | `shipped` |
| `DELIVERED`, `COMPLETED` | `delivered` |
| `CANCELLED` | `cancelled` |

- `payment_status` is `paid` once TikTok reports payment, otherwise `unpaid`.
- The raw TikTok status is kept in a new `provider_status text` column, so staff see TikTok's own word next to ours.
- An unknown status maps to `processing` and stays visible as `provider_status`; the builder may refine this.
- Synced TikTok orders are excluded from local order flows: draft expiry, reservations, confirm/cancel and Telegram `OrderAgent` reads. The builder audits each `orders` writer and job for this.

**Products.** A new table `marketplace_listings` with tenant RLS. Grants: `csa_app` gets SELECT/INSERT/UPDATE/DELETE as needed, `csa_ro` gets SELECT. One row per **SKU**:
- `client_id`, `store_id`, `channel` (`'tiktok_shop'`);
- `external_product_id`, `external_sku_id`;
- `product_name`, `variant_name` (for example "Black / M"), `seller_sku`;
- `price` (Decimal), `currency`;
- `stock_qty` (TikTok total available across warehouses);
- `product_status`, `image_url`;
- `provider_updated_at`, `synced_at`.

Further rules:
- Unique on `(client_id, channel, external_sku_id)`.
- An optional match to a local product is computed at read time on equal `seller_sku`, and shown as "Same SKU as your product X". It is never stored and never changes local stock.
- Delisted or deleted products are kept with their status, until a purge or disconnect.

**Sync jobs.** A new table `marketplace_sync_jobs` with tenant RLS. Columns:
- `id`, `client_id`, `store_id`;
- `kind`: `backfill_orders`, `backfill_products`, `order_refresh`, `product_refresh` or `catch_up`;
- `target` (an order or product ID, or NULL);
- `status`;
- `attempts`, `next_attempt_at`, `claimed_at`, `last_error_code`, `created_at`.

A partial unique index on `(client_id, store_id, kind, target)` for `status='pending'` folds duplicate webhook triggers into one job. It follows the `kb_ingest_jobs` pattern (§6.4).

**Store sync state.** On `channel_credentials` or a small side table (builder's call): `orders_synced_at`, `products_synced_at` and `catch_up_cursor` (the last provider `update_time` seen).

### 6.4 Sync engine

- **Worker loop.** A `MarketplaceSyncProcessor` runs in the worker process, modelled on `KbIngestProcessor`.
  - It iterates active clients and claims jobs with `FOR UPDATE SKIP LOCKED` under RLS.
  - It requeues stale claims.
  - It retries with backoff up to a cap, then marks the job `failed` and raises one `notify` per store, not per job.
  - Every TikTok call goes through the verified, fenced reader path (`TikTokStoredCredentialResolver` → `BoundTikTokReader` → `dispatch_read`). M6B built that path for order reads; it gains operations for:
    - order search;
    - product search;
    - product detail;
    - inventory read.

    These operations follow the same evidence and fence rules.
- **Backfill.** After a successful connect or reconnect, the connect code queues `backfill_orders` (last 90 days, by `update_time`) and `backfill_products` (full catalogue). Backfills page through the results and checkpoint their cursor, so a crash resumes where it stopped.
- **Webhooks.** `POST /tiktok_shop/webhook` verifies the HMAC with `TIKTOK_APP_SECRET` as today, then resolves `shop_id` to a client and store.
  - **Order event:** queue `order_refresh(order_id)`. The job fetches that order's full detail and upserts it, because the webhook only says something changed.
  - **Product event:** queue `product_refresh(product_id)`.
  - **Deauthorization:** mark the store `disconnected` (§6.6).
  - **Unknown event:** acknowledge and drop.
  - **Unknown shop:** acknowledge with 200 and drop, so TikTok doesn't retry.
  - **Raw-payload storage:** the webhook **no longer writes `ingress_events`**. Today those rows reach the turn worker, fail "no valid envelope" and dead-letter. Already-stored legacy TikTok rows are cleaned by a one-off migration step (§8).
  - **Ack time:** acks must stay fast, so the handler only verifies, resolves and inserts the job.
- **Catch-up.** A scheduled per-client job (`marketplace_catch_up`, every 3 hours, owner's call) queues one `catch_up` job per connected store. That job searches orders and products updated since the store's cursor, in case webhooks were missed, and upserts them.
- **Rate limits.** The processor respects TikTok's API limits with a per-store concurrency of 1 and a small delay between pages. The builder sets the constants.
- **Freshness.** `synced_at` is written on every upsert. Store-level `orders_synced_at`/`products_synced_at` update when a backfill or catch-up completes.

### 6.5 Business tab (console)

Changes go in `CSA_WebConsole/csa-console/web/components/console/BusinessWorkspace.tsx` and `csa/console/business.py`, following the writing guide.

**Stores card:**
- The disabled "Connect unavailable" and "Reconnect unavailable" buttons become working **Connect TikTok Shop** and **Reconnect** buttons for admins.
- **Disconnect** appears for admins, behind a confirm: "Stop syncing this shop? Orders stay visible for 30 days, then customer details are removed."
- Each store shows:
  - plain status: Connected, Waiting for TikTok approval, Needs reconnecting, Disconnected;
  - "Orders synced 3 min ago" and "Products synced 1 h ago";
  - a problem line when sync is failing.
- The "Live integration: unavailable" constant and the local-only notices are replaced with true statements.
- Returning from TikTok with `?tiktok=…` shows a one-line result, then clears the query.

**Orders list and detail** (existing views):
- TikTok rows show a "TikTok" source badge, TikTok's status beside ours and "Synced 3 min ago".
- The detail shows everything we hold: items, totals, ship-to as TikTok provides it, tracking.
- A source filter (All / Telegram / TikTok …) is added if the list lacks one.

**Products list:**
- Gains a source switch: **Your catalogue** (today's local view, unchanged) and **TikTok Shop** (listings: product, variant, SKU, price, stock, status, "Synced …", and "Same SKU as …" when matched).
- `ProductPage.source` and `marketplace_sync` stop being constants.
- The listing detail shows all variants of the product. A **Check live stock** button for staff is the builder's call; the workspace assistant covers this need either way.

### 6.6 Disconnect and data retention

A store becomes disconnected when:
- an admin clicks Disconnect;
- TikTok sends deauthorization; or
- a refresh is rejected and nobody reconnects within 30 days, in which case the store is shown as `expired` until then.

What happens:
- Syncing stops at once.
- Pending jobs for the store are cancelled.
- Tokens are deleted. The grant is revoked through the existing lifecycle, and the generation is bumped.
- Orders and listings stay visible and read-only, marked "Disconnected on …".

After **30 days** disconnected (owner's call), a scheduled per-client purge job:
- removes buyer personal data from that store's orders: `ship_to` and any buyer name or phone fields;
- deletes the store's listings;
- keeps order number, totals, statuses and dates.

Reconnecting the same shop within 30 days resumes, with a catch-up from the cursor. The purge job also removes abandoned pending stores (§6.2).

**While connected** (owner decision): the same purge job clears buyer personal data from each TikTok order **90 days after it reaches `delivered` or `cancelled`**. That covers `ship_to` and any buyer name or phone fields. Order number, totals, statuses, dates and items stay.
- A cleared order shows "Customer details removed after 90 days" in its detail.
- Later syncs of that order must not write the cleared fields back.
- If TikTok's partner data policy sets a shorter limit, the shorter limit applies. The 90 days is one constant.

### 6.7 Customer assistant

- **Where it lives.** The existing `EcommerceAgent` spoke answers TikTok questions. It is gated by the `marketplaces` package (already mapped in `csa/console/packages.py`; no change there).
  - Its unverified adapter path is replaced. It is wired today with no adapters and always returns `unsupported_platform`.
  - The coordinator and planner learn to route "TikTok order number" and "is X in stock" questions to it when the tenant has a connected TikTok store.
  - `OrderAgent` and its contact-scoped reads are unchanged.
- **Order lookup by number.**
  - The order number is matched against `external_order_id` for the tenant's `tiktok_shop` orders in our copy.
  - The reply holds status (plain words), tracking number and carrier, and the shipped or delivered date. It never includes name, address, items, totals or ship-to (owner decision).
  - Not found: "I couldn't find that TikTok order number. Please check it, or ask our staff."
  - Abuse limit: at most 10 order-number lookups per conversation per hour (my call), then "Please ask our staff for more help."
- **Stock question** (owner decision: the client's own catalogue first).
  1. `OrderAgent` keeps answering from local `products` exactly as today, with exact counts. That is the stock Telegram and WhatsApp orders are taken against.
  2. Only when the item has **no local match** does the question go to TikTok. The agent finds candidate listings in our copy by name, variant or SKU.
  3. If there is one clear match, it reads that listing's stock **live** from TikTok through the verified reader, and updates our copy as a side effect.
  4. If the match is ambiguous, it asks which variant, listing up to 5.
  - The planner routes stock questions to the local catalogue first, and falls through to the TikTok path when nothing matches. The builder decides the mechanism: one tool that checks both, or a routed fallback.
- **The reply for a TikTok-only item:**
  - It shows counts the same way `OrderAgent` does today ("8 available"), plus the price, and says "available on our TikTok Shop".
  - If the live check fails and our copy is under 6 hours old: "8 were available as of 2 hours ago." Otherwise: "I can't check right now. Our staff will confirm." The builder decides whether to raise a staff handoff here.
- **Inside TikTok chat (Project 2)** the buyer is known, so full answers become possible there. This design does not cover that.

### 6.8 Workspace assistant

**Orders.** These come almost for free: `get_local_order` already reads `orders`, so an open TikTok order summarises as today, plus "TikTok · synced 3 min ago". The frontend publishes the open order as page context, `RecordRef(kind="order")`, which `BusinessWorkspace.tsx` doesn't do today.

**Products.** The changes are:
- Add a `"product"` record kind. TikTok listings are referenced as `kind="product"` with source `tiktok_shop`; the builder picks the reference shape.
- Add a `business_products`/`business_product` page hint and a product read proposal.
- Add `_RECORD_GATES["product"]` with the business gate, and a `_summarize` branch.
- Add a **stock read** that finds the listing in our copy, then reads live with the same verified reader as §6.7.
- Staff get exact counts per variant, with "checked live just now" or "as of …" when the live read fails.
- The relevant spots are listed in `csa/workspace/contracts.py`, `service.py` and `planner.py`, and in the frontend's `workspace-api.ts`, `assistant-view.ts` and `BusinessWorkspace.tsx`.
- The workspace assistant is read-only for TikTok. No proposals that write to TikTok.

## 7. Testing

- **Synthetic fixtures only.** Every TikTok call uses `httpx.MockTransport` with recorded-shape fixtures written from docs, not from live captures containing real data. Testcontainers only.
- **Connect:**
  - start → callback → redirect, for first connect and reconnect;
  - `multiple_shops`, `shop_mismatch` and `shop_in_use`;
  - burned-state replay;
  - a non-admin denied;
  - TikTok off returns 503 or 404;
  - redirect reasons are safe codes;
  - no secret or provider text in responses or logs, using the existing log-capture pattern.
- **Refresh:** success bumps the generation; a rejection marks the store expired; a lost response keeps the old credential.
- **Sync:**
  - backfill paging and checkpoint resume;
  - duplicate webhook triggers fold into one job;
  - order upsert is idempotent;
  - status mapping;
  - unknown shop and unknown event are acknowledged;
  - no `ingress_events` row is written;
  - TikTok orders are untouched by local order jobs;
  - local `products` stock is untouched by listing sync.
- **Retention:** disconnect stops jobs and deletes tokens; the purge after 30 days clears PII and keeps totals; abandoned pending stores are removed.
- **Assistants:**
  - an order-number reply contains no PII fields;
  - the lookup limit;
  - the stock live path, the live-failure fallback and the ambiguous-variant prompt;
  - the workspace product summary and stock read;
  - package gate denial.
- **Browser:**
  - Business tab: connect button visibility by role, statuses, products source switch;
  - update the `test_services_browser` and `test_packaged_console` stubs for any new console calls. The A1 lesson is to run both before pushing.

## 8. Rollout

1. Merge with migrations `0490+`. Set the expected schema version to the highest merged version.
2. On staging:
   1. Run `tiktok-defaults`.
   2. Run `migrate`.
   3. Recreate the gateway, worker, delivery and frontend. TikTok stays off.
   4. One-off: mark legacy TikTok `ingress_events` rows as dead or done, so they never reach the turn worker. The builder chooses whether that is a migration or a runbook step.
   5. Owner: write the real secret files, recreate the services, then `tiktok-app register` and `enable`. A platform admin enables the **Marketplaces** package for the client on Services.
   6. Owner: confirm in Partner Center whether a shop that isn't the owner's can authorize the app while it is unpublished, or whether the app must first be published or the shop allow-listed. Do that step before connecting the client's shop.
   7. Owner: set the Partner Center redirect URL, webhook URL and events.
   8. Connect the owner's own shop, or a test shop, first.
   9. Check the "done means" list (§2), then connect the client's shop.
3. Record the rollout in `docs/PROGRESS.md`.

## 9. Left to the builder

- SQL file names.
- Pydantic field names not fixed above.
- Retry and backoff constants.
- API page sizes.
- Order ID format.
- Whether store sync state lives on `channel_credentials` or a side table.
- The UI layout within the writing guide.
- Whether a staff **Check live stock** button ships.
- Whether a failed customer stock check raises a staff handoff.
- Exact TikTok endpoint versions, event type numbers, status names and the Malaysia authorize host. Verify each against current Partner Center docs and record the source in the ledger.
- Any change to who may call what, to retention, or to what customers see goes back to the owner.

## 10. Sources

- [TikTok Shop authorization overview](https://partner.tiktokshop.com/docv2/page/authorization-overview-202407) (token exchange contract verified in M6B, 18 Oct 2026)
- [Apideck: TikTok Shop configuration guide](https://developers.apideck.com/apis/ecommerce/tiktok/configuration) (Manage API package names)
- M6B handoff: `docs/archive/handoffs/2026-10-18-m6b-verifier-next-session.md`
