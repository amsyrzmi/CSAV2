/**
 * Pure rules for the Business management page: plain words for order statuses, payments,
 * sources and stores, money and dates the way docs/WRITING_GUIDE.md asks, the status chip
 * options with their counts, and the default window of the counts read. The raw codes are
 * kept for the opened details; nothing here changes a code or invents a field the order read
 * does not return.
 */
import { describeRange, presets, todayLocal, type DayRange } from "@/lib/date-range";

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

const STORE_STATUS_WORDS: Record<string, { label: string; tone: Tone }> = {
  unsupported: { label: "Not supported yet", tone: "neutral" },
  missing_shop_id: { label: "Store ID missing", tone: "attention" },
  unverified: { label: "Not verified", tone: "neutral" },
  revoked: { label: "Access removed", tone: "attention" },
  disconnected: { label: "Disconnected", tone: "attention" },
  expired: { label: "Expired", tone: "attention" },
  app_unavailable: { label: "App unavailable", tone: "attention" },
  binding_missing: { label: "Shop link missing", tone: "attention" },
  permissions_missing: { label: "Permissions missing", tone: "attention" },
  authorization_recorded: { label: "Access recorded", tone: "info" },
};

type StoreLike = {
  platform: string;
  shop_id: string | null;
  connection: { status: string };
};

export function storeSummary(store: StoreLike): { title: string; subtitle: string; status: string } {
  return {
    title: `${channelLabel(store.platform)} · ${store.shop_id ?? "Store ID not recorded"}`,
    subtitle: "Saved record only, not checked with the marketplace",
    status: STORE_STATUS_WORDS[store.connection.status]?.label ?? store.connection.status,
  };
}

export function storeStatusTone(status: string): Tone {
  return STORE_STATUS_WORDS[status]?.tone ?? "neutral";
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
};

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
        { label: "Placed", value: whenText(order.placed_at) },
        { label: "Created", value: whenText(order.created_at) },
        { label: "Last changed here", value: whenText(order.updated_at) },
      ],
    },
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
