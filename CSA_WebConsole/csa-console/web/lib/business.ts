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

export function localOrderPath(tenantQuery: string, channel: string, shopId: string): string {
  if (shopId && !channel) throw new Error("Select a source/platform before a store ID.");
  const query = new URLSearchParams(tenantQuery);
  if (channel) query.set("channel", channel);
  if (shopId) query.set("shop_id", shopId);
  return `/console/v1/business/orders?${query}`;
}
