/** Filters address local records only; a store ID is not a verified connection. */
export type ConnectionStatus =
  | "unsupported" | "missing_shop_id" | "unverified" | "revoked" | "disconnected"
  | "expired" | "app_unavailable" | "binding_missing" | "permissions_missing"
  | "authorization_recorded";

export const connectionLabels: Record<ConnectionStatus, string> = {
  unsupported: "Live integration not implemented for this platform",
  missing_shop_id: "Store ID missing",
  unverified: "No usable local authorization evidence",
  revoked: "Authorization revoked locally",
  disconnected: "Disconnected or credential version changed",
  expired: "Expired by recorded timestamp",
  app_unavailable: "App disabled, missing or revision changed",
  binding_missing: "Shop-binding evidence missing",
  permissions_missing: "Required stored permissions missing",
  authorization_recorded: "Authorization recorded locally, not verified live",
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
