export type ConnectionStatus =
  | "unsupported" | "missing_shop_id" | "unverified" | "revoked" | "disconnected"
  | "expired" | "app_unavailable" | "binding_missing" | "permissions_missing"
  | "authorization_recorded";

export const connectionLabels: Record<ConnectionStatus, string> = {
  unsupported: "Syncing is not available for this marketplace yet",
  missing_shop_id: "Waiting for the seller to approve on TikTok",
  unverified: "No usable authorization on record",
  revoked: "Access was removed",
  disconnected: "Disconnected, or the saved credential changed",
  expired: "Access expired",
  app_unavailable: "The TikTok app is disabled, missing or changed",
  binding_missing: "The link to the shop is missing",
  permissions_missing: "Some required permissions are missing",
  authorization_recorded: "Connected and checked with TikTok",
};

/** One TikTok (or other marketplace) store record as `GET /business/stores` sends it. */
export type StoreRecord = {
  id: string; platform: string; shop_id: string | null; expires_at: string | null;
  updated_at: string; expiry: "unknown" | "expired" | "not_expired";
  authorization: "verified" | "unverified";
  permissions: "granted" | "missing" | "unknown";
  provider_reads: "available" | "unavailable";
  connection: { status: ConnectionStatus; live_integration: "available" | "unavailable" };
  sync: {
    orders_synced_at: string | null; products_synced_at: string | null; problem: "sync_failing" | null;
  } | null;
  can_manage: boolean;
};

export type DemoListing = Readonly<{
  id: string;
  sku: string;
  name: string;
  price: string;
  status: "Demo draft";
}>;

export const demoListings: readonly DemoListing[] = Object.freeze([
  Object.freeze({
    id: "demo-listing-1",
    sku: "DEMO-MUG-001",
    name: "Sample ceramic mug",
    price: "18.00",
    status: "Demo draft",
  }),
  Object.freeze({
    id: "demo-listing-2",
    sku: "DEMO-TOTE-002",
    name: "Sample canvas tote",
    price: "24.00",
    status: "Demo draft",
  }),
]);

export function appendDemoListing(listings: readonly DemoListing[]): DemoListing[] {
  const next = listings.length + 1;
  return [
    ...listings,
    {
      id: `demo-listing-${next}`,
      sku: `DEMO-NEW-${String(next).padStart(3, "0")}`,
      name: `Sample listing ${next}`,
      price: "12.00",
      status: "Demo draft",
    },
  ];
}

/** What the order list and the counts can be narrowed by; empty values are left out. */
export type OrderQuery = {
  channel?: string;
  shopId?: string;
  /** ISO instants from `toISOString()` (UTC); a raw `+08:00` offset would decode to a space. */
  from?: string | null;
  to?: string | null;
  status?: string;
  paymentStatus?: string;
};

function orderParams(tenantQuery: string, filters: OrderQuery, withStatus: boolean): URLSearchParams {
  if (filters.shopId && !filters.channel) throw new Error("Select a source/platform before a store ID.");
  const query = new URLSearchParams(tenantQuery);
  if (filters.channel) query.set("channel", filters.channel);
  if (filters.shopId) query.set("shop_id", filters.shopId);
  if (filters.from) query.set("from", filters.from);
  if (filters.to) query.set("to", filters.to);
  if (withStatus && filters.status) query.set("status", filters.status);
  if (filters.paymentStatus) query.set("payment_status", filters.paymentStatus);
  return query;
}

export function localOrderPath(tenantQuery: string, channel: string, shopId: string): string {
  return orderListPath(tenantQuery, { channel, shopId });
}

export function orderListPath(tenantQuery: string, filters: OrderQuery): string {
  return `/console/v1/business/orders?${orderParams(tenantQuery, filters, true)}`;
}

/** The counts read ignores any status on purpose, so it never sends one. */
export function orderCountsPath(tenantQuery: string, filters: OrderQuery): string {
  return `/console/v1/business/orders/counts?${orderParams(tenantQuery, filters, false)}`;
}

/** The same read for the next page: `before` joins the query. No cursor leaves the path as is. */
export function withCursor(path: string, cursor: string | null): string {
  if (!cursor) return path;
  const at = path.indexOf("?");
  const query = new URLSearchParams(at < 0 ? "" : path.slice(at + 1));
  query.set("before", cursor);
  return `${at < 0 ? path : path.slice(0, at)}?${query}`;
}

/** The order read's row. The last three are filled only for a synced TikTok order. */
export type Order = {
  id: string; status: string; payment_status: string; total: string; currency: string;
  channel: string; external_order_id: string | null; external_shop_id: string | null;
  tracking_no: string | null; carrier: string | null; placed_at: string | null;
  shipped_at: string | null; created_at: string; updated_at: string;
  provider_status?: string | null; synced_at?: string | null; buyer_cleared?: boolean;
};

export type OrderLine = { sku: string; name: string; qty: number; unit_price: string };
export type ShipTo = {
  recipient_name?: string | null; phone?: string | null; address_text?: string | null; region?: string | null;
};

/** `GET /orders/{id}`: lines, totals and ship-to are present only for a synced TikTok order. */
export type OrderDetail = Order & {
  lines: OrderLine[] | null; subtotal: string | null; shipping_fee: string | null;
  discount: string | null; ship_to: ShipTo | null;
};

/** One TikTok Shop listing copied by sync (`GET /listings`). `same_sku_product` is our product's name. */
export type Listing = {
  id: string; product_name: string; variant_name: string | null; seller_sku: string | null;
  price: string | null; currency: string; stock_qty: number | null; product_status: string | null;
  image_url: string | null; synced_at: string; same_sku_product: string | null;
};

export function orderDetailPath(tenantQuery: string, id: string): string {
  return `/console/v1/business/orders/${encodeURIComponent(id)}${tenantQuery ? `?${tenantQuery}` : ""}`;
}

export function listingsPath(tenantQuery: string): string {
  return `/console/v1/business/listings${tenantQuery ? `?${tenantQuery}` : ""}`;
}
