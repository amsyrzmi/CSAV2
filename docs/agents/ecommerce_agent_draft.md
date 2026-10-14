# E-Commerce Operations Agent & Service (Draft)

## 1. Overview & Objective
The **E-Commerce Operations Agent** (`ecommerce_agent`) automates multi-channel commerce operations across Southeast Asian marketplaces:
- **TikTok Shop** (primary, currently registered / active target)
- **Shopee** (planned wave adapter)
- **Lazada** (planned wave adapter)

It integrates into CSA as a first-class spoke agent (following LG05 / LG13 contracts) and as a background synchronization worker (following LG10 scheduled jobs).

---

## 2. Multi-Marketplace Architecture

```
                  ┌──────────────────────────────────────────────┐
                  │            CSA Coordinator Graph             │
                  └───────────────────────┬──────────────────────┘
                                          │ calls
                                          ▼
                  ┌──────────────────────────────────────────────┐
                  │               ecommerce_agent                │
                  │   (implements Agent protocol, returns Facts) │
                  └───────────────────────┬──────────────────────┘
                                          │
                  ┌───────────────────────┴──────────────────────┐
                  │        EcommercePlatformAdapter Protocol     │
                  └───────┬───────────────────┬──────────────────┘
                          │                   │                  │
                          ▼                   ▼                  ▼
               ┌─────────────────────┐  ┌───────────┐      ┌───────────┐
               │  TikTokShopAdapter  │  │ShopeeAdap.│      │LazadaAdap.│
               │  (Open API 202309)  │  │ (draft)   │      │ (draft)   │
               └──────────┬──────────┘  └─────┬─────┘      └─────┬─────┘
                          │                   │                  │
                          ▼                   ▼                  ▼
                   TikTok Shop API       Shopee Open API    Lazada Open API
```

### Core Responsibilities
1. **Catalog & Product Sync**:
   - Query product details, variants, prices, and status from marketplace APIs.
   - Map external marketplace product/SKU identifiers to internal CSA `products.sku`.
2. **Omnichannel Inventory Management**:
   - Real-time stock update: When an order is placed on any channel (Telegram, Web, TikTok Shop), update stock in CSA and sync available quantity to all connected marketplaces.
   - Reconcile reserved and available quantities using the LG10 scheduled reconciliation runner (`reconcile_marketplace_inventory`).
3. **Order Ingestion & Status Tracking**:
   - Ingest new orders via platform webhooks (or polling fallback) into CSA `orders` (`channel='tiktok_shop'`).
   - Query order status, shipment tracking number, delivery state, and cancellations.
4. **Fulfillment & Shipping**:
   - Fetch package shipping documents (airway bills / shipping labels).
   - Mark orders as shipped / dispatch carrier tracking codes.
5. **Returns & Cancellations**:
   - Query cancellation and return requests.
   - High-value refund requests require Human-In-The-Loop (`LG07`) staff approval before dispatching cancellation approval to the marketplace.

---

## 3. TikTok Shop Integration Specification

### 3.1 Authentication & Multi-Tenancy
- **Credentials** (stored encrypted in `channel_credentials` or `capability_registry.config`):
  - `app_key`: Partner App Key
  - `app_secret`: Partner App Secret (used for HMAC-SHA256 signature calculation)
  - `access_token`: Seller OAuth access token (valid 7 days)
  - `refresh_token`: Seller OAuth refresh token (valid 180 days)
  - `shop_cipher` / `shop_id`: Tenant TikTok Shop identifier
- **Base URL**: `https://open-api.tiktokglobalshop.com`

### 3.2 Request Signing Algorithm (Open API v202309)
Every API call to TikTok Shop must be signed with HMAC-SHA256:
1. Extract all query parameters excluding `sign` and `access_token`.
2. Sort parameters alphabetically by key.
3. Concatenate: `app_secret + path + key1value1key2value2 + body + app_secret`.
4. Generate HMAC-SHA256 digest using `app_secret`.
5. Pass `sign` in query parameter and `x-tts-access-token` in HTTP header.

### 3.3 Webhook Verification
- Inbound webhooks from TikTok Shop arrive at `/hooks/ecommerce/tiktok/{client_id}`.
- Verify `Authorization` header against HMAC-SHA256(`body`, `app_secret`).
- Handled events:
  - `ORDER_STATUS_CHANGE`: Synchronize status to `orders` table.
  - `PACKAGE_UPDATE`: Update tracking code in `orders.metadata`.
  - `RETURN_STATUS_CHANGE`: Notify staff if return or refund requested.

---

## 4. Spoke Actions & Risk Classification

Following `csa/contracts/plan.py`:

| Action | Description | Risk Class | Side Effects | HITL Required? |
|---|---|---|---|---|
| `get_order` | Retrieve order status and tracking from marketplace | `read` | `none` | No |
| `search_orders` | Search recent orders by date, status, buyer | `read` | `none` | No |
| `get_product` | Query product/variant details and live marketplace price | `read` | `none` | No |
| `sync_inventory` | Push updated stock quantity to marketplace product SKU | `commit` | `reversible` | No (automated sync) |
| `fulfill_order` | Confirm shipment and submit carrier tracking code | `commit` | `irreversible` | No (if valid tracking) |
| `cancel_order` | Request or accept cancellation of an unfulfilled order | `commit` | `irreversible` | Yes (staff approval) |
| `approve_refund` | Authorize customer return/refund on marketplace | `commit` | `irreversible` | Yes (staff approval) |

---

## 5. Console & Admin Subscription Integration

In accordance with the CSA Console contract (`LG13`):
1. **Subscription Toggle**:
   - Platform Admin can enable/disable `ecommerce_agent` for each client (`capability_registry.is_enabled`).
   - Tenant Admin can enable/disable individual marketplaces (`tiktok_shop`, `shopee`, `lazada`) via `capability_registry.config.marketplaces`.
2. **Dedicated E-Commerce Console Tab**:
   - **Marketplace Connection**: OAuth authorization status, token expiration alerts, shop metadata.
   - **Order Stream**: Real-time listing of cross-platform orders with channel badges (TikTok / Shopee / Lazada / Telegram).
   - **Stock Matrix**: Unified table showing CSA local stock vs TikTok Shop stock vs Shopee stock with one-click "Sync All" button.
   - **Dispute & Refund Inbox**: Pending cancellation and return requests awaiting staff decision.
