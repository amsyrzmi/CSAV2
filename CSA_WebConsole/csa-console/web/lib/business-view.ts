/**
 * Pure rules for the Business management page: plain words for order statuses, payments,
 * sources and stores, money and dates the way docs/WRITING_GUIDE.md asks, the status chip
 * options with their counts, and the default window of the counts read. The raw codes are
 * kept for the opened details; nothing here changes a code or invents a field the order read
 * does not return.
 */
import type { Listing, OrderDetail, OrderLine, ShipTo } from "@/lib/business";
import { describeRange, presets, todayLocal, type DayRange } from "@/lib/date-range";
import { syncLine } from "@/lib/tiktok-connect";

export type Tone = "neutral" | "info" | "attention" | "success" | "error";

export const ORDER_STATUSES = [
  "draft",
  "pending_confirmation",
  "confirmed",
  "processing",
  "shipped",
  "delivered",
  "cancelled",
  "refunded",
] as const;

export const PAYMENT_STATUSES = ["unpaid", "cod_pending", "paid", "refunded", "failed"] as const;

const STATUS_WORDS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  pending_confirmation: { label: "Waiting for confirmation", tone: "attention" },
  confirmed: { label: "Confirmed", tone: "info" },
  processing: { label: "Being prepared", tone: "info" },
  shipped: { label: "Shipped", tone: "info" },
  delivered: { label: "Delivered", tone: "success" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  refunded: { label: "Refunded", tone: "neutral" },
};

const PAYMENT_WORDS: Record<string, { label: string; tone: Tone }> = {
  unpaid: { label: "Not paid yet", tone: "attention" },
  cod_pending: { label: "Pay on delivery", tone: "info" },
  paid: { label: "Paid", tone: "success" },
  refunded: { label: "Payment refunded", tone: "neutral" },
  failed: { label: "Payment failed", tone: "error" },
};

const CHANNEL_WORDS: Record<string, string> = {
  tiktok_shop: "TikTok Shop",
  shopee: "Shopee",
  lazada: "Lazada",
  telegram: "Telegram",
  web: "Web",
  manual: "Manual",
};

/** A code the page does not know keeps its code, so nothing is ever hidden. */
export function orderStatusLabel(status: string): string {
  return STATUS_WORDS[status]?.label ?? status;
}

export function orderStatusTone(status: string): Tone {
  return STATUS_WORDS[status]?.tone ?? "neutral";
}

/** One chip per order status, in the order of the status flow. */
export function orderStatusChips(): { value: string; label: string }[] {
  return ORDER_STATUSES.map((value) => ({ value, label: STATUS_WORDS[value].label }));
}

export function paymentLabel(status: string): string {
  return PAYMENT_WORDS[status]?.label ?? status;
}

export function paymentTone(status: string): Tone {
  return PAYMENT_WORDS[status]?.tone ?? "neutral";
}

export function paymentOptions(): { value: string; label: string }[] {
  return PAYMENT_STATUSES.map((value) => ({ value, label: PAYMENT_WORDS[value].label }));
}

export function channelLabel(channel: string): string {
  return CHANNEL_WORDS[channel] ?? channel;
}

/** "RM 1,234.50" for MYR; any other currency keeps its code ("USD 12.00"). */
export function formatRm(amount: number | string, currency: string): string {
  const prefix = currency === "MYR" ? "RM" : currency;
  const text = typeof amount === "string" ? amount.trim() : String(amount);
  const value = text === "" ? Number.NaN : Number(text);
  if (!Number.isFinite(value)) return `${prefix} ${text === "" ? "-" : text}`;
  const money = value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${prefix} ${money}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2 Nov 2026, 09:15" in the viewer's zone, or "" when the time cannot be read. */
function dateAndTime(at: string | null | undefined): string {
  if (!at) return "";
  const time = new Date(at);
  if (Number.isNaN(time.getTime())) return "";
  const two = (value: number) => String(value).padStart(2, "0");
  return `${time.getDate()} ${MONTHS[time.getMonth()]} ${time.getFullYear()}, ${two(time.getHours())}:${two(time.getMinutes())}`;
}

/** The date for the opened details; a missing or unreadable time says so. */
export function whenText(at: string | null | undefined): string {
  return dateAndTime(at) || "Not recorded";
}

type OrderLike = {
  id: string;
  external_order_id: string | null;
  channel: string;
  total: string | number;
  currency: string;
  created_at: string;
};

/**
 * The card's two plain lines. The reference is the platform's own order number when there is
 * one, else the first block of the local id; the whole id stays in the opened details.
 */
export function orderSummary(order: OrderLike): { title: string; subtitle: string } {
  const reference = order.external_order_id?.trim() || order.id.slice(0, 8);
  const parts = [
    channelLabel(order.channel),
    formatRm(order.total, order.currency),
    dateAndTime(order.created_at),
  ].filter(Boolean);
  return { title: `Order ${reference}`, subtitle: parts.join(" · ") };
}

type ProductLike = { name: string; price: string | number; currency: string; available_qty: number };

export function productSummary(product: ProductLike): { title: string; subtitle: string } {
  const available = product.available_qty > 0 ? String(product.available_qty) : "None";
  return {
    title: product.name,
    subtitle: `${formatRm(product.price, product.currency)} · ${available} available`,
  };
}

const STORE_STATUS_WORDS: Record<string, { label: string; tone: Tone; line: string }> = {
  unsupported: {
    label: "Not supported yet", tone: "neutral", line: "Syncing isn't available for this marketplace yet.",
  },
  missing_shop_id: {
    label: "Waiting for TikTok approval", tone: "attention", line: "Finish approving the connection on TikTok.",
  },
  unverified: { label: "Not verified", tone: "neutral", line: "Reconnect to check this shop with TikTok." },
  revoked: { label: "Needs reconnecting", tone: "attention", line: "Syncing has stopped. Reconnect to start it again." },
  disconnected: {
    label: "Disconnected", tone: "neutral",
    line: "Syncing is off. Customer details are removed 30 days after disconnecting.",
  },
  expired: { label: "Needs reconnecting", tone: "attention", line: "Syncing has stopped. Reconnect to start it again." },
  app_unavailable: {
    label: "Needs reconnecting", tone: "attention", line: "Syncing has stopped. Reconnect to start it again.",
  },
  binding_missing: {
    label: "Needs reconnecting", tone: "attention", line: "Syncing has stopped. Reconnect to start it again.",
  },
  permissions_missing: {
    label: "Needs reconnecting", tone: "attention",
    line: "TikTok didn't give us all the access we need. Reconnect and allow every permission.",
  },
  authorization_recorded: { label: "Connected", tone: "success", line: "Orders and products sync from this shop." },
};

type StoreLike = {
  platform: string;
  shop_id: string | null;
  connection: { status: string };
};

export function storeSummary(store: StoreLike): { title: string; subtitle: string; status: string } {
  const words = STORE_STATUS_WORDS[store.connection.status];
  return {
    title: `${channelLabel(store.platform)} · ${store.shop_id ?? "Store ID not recorded"}`,
    subtitle: words?.line ?? "",
    status: words?.label ?? store.connection.status,
  };
}

export function storeStatusTone(status: string): Tone {
  return STORE_STATUS_WORDS[status]?.tone ?? "neutral";
}

const TIKTOK = "tiktok_shop";
const CONNECTED = "authorization_recorded";

/**
 * Which buttons a TikTok store card offers. Only an Admin (the server says so with `can_manage`)
 * gets any: Disconnect while it is connected, Reconnect for every other state, a pending store
 * included. Other marketplaces have no connect flow.
 */
export function storeActions(
  store: { platform: string; connection: { status: string }; can_manage?: boolean },
): { reconnect: boolean; disconnect: boolean } {
  const manage = store.platform === TIKTOK && store.can_manage === true;
  const connected = store.connection.status === CONNECTED;
  return { reconnect: manage && !connected, disconnect: manage && connected };
}

/**
 * Whether the page offers a first Connect. A shop that was disconnected, expired or is waiting
 * for approval is reconnected on its own card, so a second Connect is never offered beside it.
 */
export function offersConnect(stores: readonly { platform: string; connection: { status: string } }[]): boolean {
  return !stores.some((store) => store.platform === TIKTOK && store.connection.status !== CONNECTED);
}

export type OrderCounts = { total: number; by_status: Record<string, number> };

/** The counts read as a well-formed answer, or null. A bad answer never turns into zeros. */
export function parseOrderCounts(raw: unknown): OrderCounts | null {
  if (!raw || typeof raw !== "object") return null;
  const { total, by_status: byStatus } = raw as Record<string, unknown>;
  if (typeof total !== "number" || !Number.isFinite(total) || total < 0) return null;
  if (!byStatus || typeof byStatus !== "object" || Array.isArray(byStatus)) return null;
  const by_status: Record<string, number> = {};
  for (const [status, count] of Object.entries(byStatus)) {
    if (typeof count === "number" && Number.isFinite(count) && count >= 0) by_status[status] = count;
  }
  return { total, by_status };
}

/** "All orders" plus one chip per status. Without counts (loading or failed) no figure shows. */
export function orderChipOptions(
  counts: OrderCounts | null,
): { value: string; label: string; count: number | null }[] {
  return [
    { value: "all", label: "All orders", count: counts ? counts.total : null },
    ...orderStatusChips().map((chip) => ({ ...chip, count: counts?.by_status[chip.value] ?? null })),
  ];
}

export type CountsScope = { kind: "chosen" | "default" | "all"; range: DayRange };

/**
 * The days the counts cover. The days the reader chose win; else all time when asked for
 * explicitly (the unwindowed read can be slow on a busy shop); else the last 30 days.
 */
export function effectiveCountsRange(
  range: DayRange,
  allTime: boolean,
  now: Date = new Date(),
): CountsScope {
  if (range.from !== null || range.to !== null) return { kind: "chosen", range };
  if (allTime) return { kind: "all", range: { from: null, to: null } };
  const last30 = presets(todayLocal(now)).find((preset) => preset.key === "last30");
  return { kind: "default", range: last30 ? last30.range : { from: null, to: null } };
}

/** The chip group's spoken name says which orders the numbers are about. */
export function chipGroupLabel(kind: CountsScope["kind"]): string {
  return kind === "default" ? "Orders, last 30 days" : kind === "all" ? "Orders, all dates" : "Orders, dates you chose";
}

/** Rows loaded by "Older" join the rows already shown; a row never appears twice. */
export function mergeRows<T extends { id: string }>(current: readonly T[], incoming: readonly T[]): T[] {
  const seen = new Set(current.map((row) => row.id));
  return [...current, ...incoming.filter((row) => !seen.has(row.id))];
}

/** The three order tiles: each is exactly one server status, so a tile's number is its list. */
export const TILE_STATUSES: readonly {
  key: "to_confirm" | "to_pack" | "on_the_way";
  status: string;
  label: string;
}[] = [
  { key: "to_confirm", status: "pending_confirmation", label: "To confirm" },
  { key: "to_pack", status: "confirmed", label: "To pack" },
  { key: "on_the_way", status: "shipped", label: "On the way" },
];

/** A tile's number comes from the counts read; with no counts it is null, never a zero. */
export function orderTiles(
  counts: OrderCounts | null,
  status: string,
): { key: string; label: string; value: number | null; pressed: boolean; status: string }[] {
  return TILE_STATUSES.map((tile) => ({
    key: tile.key,
    label: tile.label,
    value: counts?.by_status[tile.status] ?? null,
    pressed: status === tile.status,
    status: tile.status,
  }));
}

/** Products with nothing left to sell among those loaded; `more` says the read was only a page. */
export function outOfStock(
  products: { available_qty: number }[],
  more: boolean,
): { value: number; caption: string | null } {
  return {
    value: products.filter((product) => product.available_qty <= 0).length,
    caption: more ? "Among your newest 50 products" : null,
  };
}

export type DetailGroup = { title: string; rows: { label: string; value: string }[] };

type OrderDetailLike = {
  payment_status: string; total: string | number; currency: string;
  tracking_no: string | null; carrier: string | null; shipped_at: string | null;
  external_order_id: string | null; channel: string;
  placed_at: string | null; created_at: string; updated_at: string;
  provider_status?: string | null; synced_at?: string | null;
};

const TIKTOK_CHANNEL = "tiktok_shop";

/**
 * An order copied from TikTok Shop by sync. A TikTok-channel order with no TikTok order number
 * (a chat order) is still a local record, so it gets no sync wording.
 */
export function isSyncedOrder(order: { channel: string; external_order_id: string | null }): boolean {
  return order.channel === TIKTOK_CHANNEL && !!order.external_order_id;
}

/** The "TikTok" source badge: every order from the TikTok Shop channel, synced or not. */
export function orderBadge(order: { channel: string }): string | null {
  return order.channel === TIKTOK_CHANNEL ? "TikTok" : null;
}

/** TikTok's own code in plain words: "AWAITING_SHIPMENT" reads "Awaiting shipment". */
export function providerStatusLabel(code: string): string {
  const text = code.trim().replaceAll("_", " ").toLowerCase();
  return text ? text[0].toUpperCase() + text.slice(1) : "";
}

/** "Synced 3 min ago" for a synced order; null for a local one (it keeps its local-update line). */
export function orderSyncText(
  order: { channel: string; external_order_id: string | null; synced_at?: string | null }, now: Date,
): string | null {
  return isSyncedOrder(order) ? syncLine(null, order.synced_at ?? null, now) : null;
}

/** The line among the exact lines under Technical details: sync time for TikTok rows, local update for the rest. */
export function orderUpdateLine(
  order: { channel: string; external_order_id: string | null; synced_at?: string | null; updated_at: string },
): string {
  return isSyncedOrder(order)
    ? `Synced from TikTok: ${whenText(order.synced_at)}`
    : `Local update: ${whenText(order.updated_at)} — not a marketplace sync timestamp`;
}

const NOT_ADDED = "not added yet";

/** The plain groups shown first in an opened order; the exact lines stay under Technical details. */
export function orderDetailGroups(order: OrderDetailLike): DetailGroup[] {
  return [
    {
      title: "Payment",
      rows: [
        { label: "Payment", value: paymentLabel(order.payment_status) },
        { label: "Total", value: formatRm(order.total, order.currency) },
      ],
    },
    {
      title: "Delivery",
      rows: [
        { label: "Tracking number", value: order.tracking_no || NOT_ADDED },
        { label: "Courier", value: order.carrier || NOT_ADDED },
        { label: "Shipped", value: order.shipped_at ? whenText(order.shipped_at) : "Not shipped yet" },
      ],
    },
    {
      title: "Record",
      rows: [
        { label: "Marketplace order no.", value: order.external_order_id || NOT_ADDED },
        { label: "Source", value: channelLabel(order.channel) },
        ...(isSyncedOrder(order) && order.provider_status
          ? [{ label: "TikTok status", value: providerStatusLabel(order.provider_status) }]
          : []),
        { label: "Placed", value: whenText(order.placed_at) },
        { label: "Created", value: whenText(order.created_at) },
        isSyncedOrder(order)
          ? { label: "Synced from TikTok", value: whenText(order.synced_at) }
          : { label: "Last changed here", value: whenText(order.updated_at) },
      ],
    },
  ];
}

export const BUYER_CLEARED_NOTE = "Customer details removed after 90 days";

export type OrderDetailView = {
  lines: { key: string; text: string }[];
  totals: { label: string; value: string }[];
  /** The recipient's details as TikTok gave them; null once they were removed. */
  shipTo: { label: string; value: string }[] | null;
  clearedNote: string | null;
};

function lineText(line: OrderLine, currency: string): string {
  const sku = line.sku ? ` (${line.sku})` : "";
  return `${line.qty} × ${line.name}${sku} · ${formatRm(line.unit_price, currency)} each`;
}

function shipToRows(shipTo: ShipTo | null): { label: string; value: string }[] {
  const given = (value: string | null | undefined) => (value?.trim() ? value.trim() : null);
  const rows: { label: string; value: string | null }[] = [
    { label: "Recipient", value: given(shipTo?.recipient_name) },
    { label: "Phone", value: given(shipTo?.phone) },
    { label: "Address", value: given(shipTo?.address_text) },
    { label: "Region", value: given(shipTo?.region) },
  ];
  return rows.filter((row): row is { label: string; value: string } => row.value !== null);
}

/**
 * The extra detail of a synced order: lines, the totals breakdown (a part TikTok did not give is
 * left out; the total always shows), and the ship-to rows that are present. A cleared order shows
 * the note and never a ship-to, even if the server sent one.
 */
export function orderDetailView(detail: OrderDetail): OrderDetailView {
  const cleared = detail.buyer_cleared === true;
  const money = (value: string | null) => (value === null ? null : formatRm(value, detail.currency));
  const parts: { label: string; value: string | null }[] = [
    { label: "Subtotal", value: money(detail.subtotal) },
    { label: "Shipping", value: money(detail.shipping_fee) },
    { label: "Discount", value: money(detail.discount) },
  ];
  return {
    lines: (detail.lines ?? []).map((line, index) => ({
      key: `${index}-${line.sku}-${line.name}`, text: lineText(line, detail.currency),
    })),
    totals: [
      ...parts.filter((row): row is { label: string; value: string } => row.value !== null),
      { label: "Total", value: formatRm(detail.total, detail.currency) },
    ],
    shipTo: cleared ? null : shipToRows(detail.ship_to),
    clearedNote: cleared ? BUYER_CLEARED_NOTE : null,
  };
}

/**
 * Whether a card's own detail (an order's ship-to) may be read and published. Only a card a person
 * opened counts; the page's "Show technical details" switch opens every card and does not.
 */
export function detailRequested(cardOpen: boolean, openedByPerson: boolean): boolean {
  return cardOpen && openedByPerson;
}

/**
 * The open order cards, oldest first. A card that opens joins the end; the page publishes the
 * last one as its context, so exactly one record is published and it clears when its card closes.
 */
export function trackOpenOrder(open: readonly string[], id: string, isOpen: boolean): string[] {
  const rest = open.filter((value) => value !== id);
  return isOpen ? [...rest, id] : rest;
}

/**
 * What the page tells the assistant it is showing: the Business page and at most one open record,
 * the newest open order, or on Products & stock the newest open TikTok listing (as a product).
 */
export function businessPageContext(
  section: string, open: readonly string[], openListings: readonly string[] = [],
): { tab: "business"; section: string; record?: { kind: "order" | "product"; id: string } } {
  const products = section === "products";
  const id = products ? openListings.at(-1) : section === "stores" ? undefined : open.at(-1);
  return id
    ? { tab: "business", section, record: { kind: products ? "product" : "order", id } }
    : { tab: "business", section };
}

const ABOUT_ORDERS =
  "Orders from a connected TikTok Shop are copied into fourstacks and kept up to date. They show a "
  + "TikTok badge and TikTok's own status next to ours. Orders from chat, the web and manual entry are "
  + "saved here too. fourstacks only reads from TikTok Shop. It never changes an order there. "
  + "Shopee is not connected. Use Refresh to see the newest orders.";

/** The About this page text; a tenant with no connected shop is told where to connect one. */
export function ordersAboutText(sync: string | null): string {
  return sync === "not_connected"
    ? `${ABOUT_ORDERS} No TikTok Shop is connected yet. Connect your TikTok Shop under Store connections.`
    : ABOUT_ORDERS;
}

export const ORDERS_NOT_YET_TEXT =
  "fourstacks does not send anything to TikTok Shop. It cannot confirm, ship, cancel or refund an "
  + "order there, and it cannot change a product or its stock there. Shopee is not connected. Live "
  + "courier tracking, payments and returns are not part of this page yet.";

export const CATALOGUE_ABOUT_TEXT =
  "Your catalogue is the products and stock you keep in fourstacks, for your whole business. "
  + "TikTok Shop products are copied from your shop and show TikTok's own stock and price. The two "
  + "are separate and are not merged, so a change in one does not change the other. "
  + "When a TikTok product has the same SKU as one of yours, its card says so.";

/** The empty TikTok list says how to fill it. `sync` is the products read's marketplace_sync. */
export function listingsEmptyText(sync: string | null): string {
  return sync === "not_connected"
    ? "No TikTok Shop products copied yet. Connect your shop under Store connections."
    : "No TikTok Shop products found.";
}

export function listingSku(listing: { seller_sku: string | null }): string | null {
  return listing.seller_sku?.trim() || null;
}

export function isListingOut(listing: { stock_qty: number | null }): boolean {
  return listing.stock_qty !== null && listing.stock_qty <= 0;
}

export function listingStockText(listing: { stock_qty: number | null }): string {
  if (listing.stock_qty === null) return "Stock not given";
  return listing.stock_qty > 0 ? `${listing.stock_qty} in stock` : "Out of stock";
}

/** Variant, SKU, price and stock on one plain line; a SKU TikTok did not share says so. */
export function listingSummary(listing: Listing): { title: string; subtitle: string } {
  const sku = listingSku(listing);
  const parts = [
    listing.variant_name?.trim() || null,
    sku ? `SKU ${sku}` : "No SKU shared",
    listing.price === null ? "No price" : formatRm(listing.price, listing.currency),
    listingStockText(listing),
  ].filter((part): part is string => part !== null);
  return { title: listing.product_name, subtitle: parts.join(" · ") };
}

/** "Same SKU as your product Blue mug", or null when nothing matched (or the SKU is not shared). */
export function sameSkuLine(listing: { same_sku_product: string | null }): string | null {
  return listing.same_sku_product ? `Same SKU as your product ${listing.same_sku_product}` : null;
}

export function listingStatus(listing: { product_status: string | null }): { label: string; tone: Tone } {
  const status = listing.product_status?.trim();
  if (!status) return { label: "Status not given", tone: "neutral" };
  // TikTok's own code for a live product is ACTIVATE; it reads as "Active".
  const label = status.toUpperCase() === "ACTIVATE" ? "Active" : providerStatusLabel(status);
  return { label, tone: /^activ/i.test(status) ? "success" : "neutral" };
}

export function listingDetailGroups(listing: Listing): DetailGroup[] {
  return [
    { title: "Stock", rows: [{ label: "TikTok stock", value: listingStockText(listing) }] },
    {
      title: "Price",
      rows: [{
        label: "Price", value: listing.price === null ? "Not given" : formatRm(listing.price, listing.currency),
      }],
    },
    { title: "Product", rows: [
      { label: "Product", value: listing.product_name },
      { label: "Variant", value: listing.variant_name?.trim() || "None" },
      { label: "SKU", value: listingSku(listing) ?? "Not shared" },
      { label: "Status", value: listingStatus(listing).label },
    ] },
    { title: "Synced from TikTok", rows: [{ label: "Synced from TikTok", value: whenText(listing.synced_at) }] },
  ];
}

type ProductDetailLike = ProductLike & { stock_qty: number; reserved_qty: number; is_active: boolean };

export function productDetailGroups(product: ProductDetailLike): DetailGroup[] {
  return [
    {
      title: "Stock",
      rows: [
        { label: "In stock", value: String(product.stock_qty) },
        { label: "Set aside for orders", value: String(product.reserved_qty) },
        { label: "Available", value: String(product.available_qty) },
      ],
    },
    { title: "Price", rows: [{ label: "Price", value: formatRm(product.price, product.currency) }] },
    { title: "Status", rows: [{ label: "Status", value: product.is_active ? "For sale" : "Not for sale" }] },
  ];
}

type StoreDetailLike = StoreLike & { updated_at: string };

export function storeDetailGroups(store: StoreDetailLike): DetailGroup[] {
  return [
    { title: "Marketplace", rows: [{ label: "Marketplace", value: channelLabel(store.platform) }] },
    { title: "Store ID", rows: [{ label: "Store ID", value: store.shop_id ?? "not recorded" }] },
    { title: "Status", rows: [{ label: "Status", value: storeSummary(store).status }] },
    { title: "Last changed here", rows: [{ label: "Last changed here", value: whenText(store.updated_at) }] },
  ];
}

/** One removable chip per filter that is narrowing the list. */
export function activeFilters(
  state: { range: DayRange; payment: string; status: string },
): { key: "date" | "payment" | "status"; label: string }[] {
  const chips: { key: "date" | "payment" | "status"; label: string }[] = [];
  if (state.range.from !== null || state.range.to !== null) {
    chips.push({ key: "date", label: describeRange(state.range) });
  }
  if (state.payment !== "") chips.push({ key: "payment", label: paymentLabel(state.payment) });
  if (state.status !== "all" && state.status !== "") {
    chips.push({ key: "status", label: orderStatusLabel(state.status) });
  }
  return chips;
}
