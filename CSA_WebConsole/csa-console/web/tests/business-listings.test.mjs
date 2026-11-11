import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Dates are written in the viewer's zone; pin it so the expected text holds anywhere.
process.env.TZ = "Asia/Kuala_Lumpur";

async function load(name, replacements = {}) {
  let source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(replacements)) source = source.replaceAll(from, to);
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const {
  isSyncedOrder, orderBadge, providerStatusLabel, orderSyncText, orderUpdateLine, orderDetailGroups,
  orderDetailView, BUYER_CLEARED_NOTE, trackOpenOrder, businessPageContext, ordersAboutText,
  ORDERS_NOT_YET_TEXT, CATALOGUE_ABOUT_TEXT, listingsEmptyText, listingSku, isListingOut,
  listingStockText, listingSummary, sameSkuLine, listingStatus, listingDetailGroups,
  detailRequested,
} = await import(await load("business-view.ts", {
  '"@/lib/date-range"': JSON.stringify(await load("date-range.ts")),
  '"@/lib/tiktok-connect"': JSON.stringify(await load("tiktok-connect.ts")),
}));
const { orderDetailPath, listingsPath } = await import(await load("business.ts"));

const NOW = new Date("2026-11-02T02:00:00Z");
const synced = {
  id: "o1", channel: "tiktok_shop", external_order_id: "TT-1", provider_status: "AWAITING_SHIPMENT",
  synced_at: "2026-11-02T01:57:00Z", updated_at: "2026-11-01T00:00:00Z", payment_status: "paid",
  total: "30.00", currency: "MYR", tracking_no: null, carrier: null, shipped_at: null,
  placed_at: null, created_at: "2026-11-01T00:00:00Z", buyer_cleared: false,
};
const local = { ...synced, id: "o2", channel: "web", external_order_id: null, provider_status: null, synced_at: null };

test("a TikTok order is badged; a synced one also gets TikTok's status and a sync line", () => {
  assert.equal(orderBadge(synced), "TikTok");
  assert.equal(orderBadge(local), null);
  assert.equal(isSyncedOrder(synced), true);
  assert.equal(isSyncedOrder(local), false);
  assert.equal(orderSyncText(synced, NOW), "Synced 3 min ago");
  assert.equal(orderSyncText(local, NOW), null);
  assert.equal(orderSyncText({ ...synced, synced_at: null }, NOW), "Not synced yet");
  assert.equal(providerStatusLabel("AWAITING_SHIPMENT"), "Awaiting shipment");
  assert.equal(providerStatusLabel("completed"), "Completed");
  assert.equal(providerStatusLabel("  "), "");
});

test("a TikTok-channel chat order with no TikTok order number stays a local record", () => {
  const chat = { ...local, channel: "tiktok_shop" };
  assert.equal(orderBadge(chat), "TikTok");
  assert.equal(isSyncedOrder(chat), false);
  assert.equal(orderSyncText(chat, NOW), null);
  assert.match(orderUpdateLine(chat), /^Local update: .* not a marketplace sync timestamp$/);
});

test("the update line differs for TikTok rows and the detail groups follow", () => {
  assert.equal(orderUpdateLine(synced), "Synced from TikTok: 2 Nov 2026, 09:57");
  assert.doesNotMatch(orderUpdateLine(synced), /not a marketplace sync timestamp/);
  assert.match(orderUpdateLine(local), /^Local update: 1 Nov 2026, 08:00 — not a marketplace sync timestamp$/);
  const record = (order) => orderDetailGroups(order).find((group) => group.title === "Record").rows;
  const tiktok = Object.fromEntries(record(synced).map((row) => [row.label, row.value]));
  assert.equal(tiktok["TikTok status"], "Awaiting shipment");
  assert.equal(tiktok["Synced from TikTok"], "2 Nov 2026, 09:57");
  assert.equal("Last changed here" in tiktok, false);
  const plain = Object.fromEntries(record(local).map((row) => [row.label, row.value]));
  assert.equal("TikTok status" in plain, false);
  assert.equal(plain["Last changed here"], "1 Nov 2026, 08:00");
});

const detail = {
  ...synced, subtotal: "26.00", shipping_fee: "5.00", discount: "1.00",
  lines: [
    { sku: "MUG-1", name: "Blue mug", qty: 2, unit_price: "13.00" },
    { sku: "", name: "Gift wrap", qty: 1, unit_price: "0.00" },
  ],
  ship_to: { recipient_name: "A*** B", phone: "(+60)12****78", address_text: null, region: "MY" },
};

test("the order detail view shows lines, the totals breakdown and only the ship-to parts present", () => {
  const view = orderDetailView(detail);
  assert.deepEqual(view.lines.map((line) => line.text), [
    "2 × Blue mug (MUG-1) · RM 13.00 each", "1 × Gift wrap · RM 0.00 each",
  ]);
  assert.equal(new Set(view.lines.map((line) => line.key)).size, 2);
  assert.deepEqual(view.totals, [
    { label: "Subtotal", value: "RM 26.00" }, { label: "Shipping", value: "RM 5.00" },
    { label: "Discount", value: "RM 1.00" }, { label: "Total", value: "RM 30.00" },
  ]);
  assert.deepEqual(view.shipTo, [
    { label: "Recipient", value: "A*** B" }, { label: "Phone", value: "(+60)12****78" },
    { label: "Region", value: "MY" },
  ]);
  assert.equal(view.clearedNote, null);
  // A part TikTok did not give is left out; the total always shows.
  const sparse = orderDetailView({ ...detail, subtotal: null, shipping_fee: null, discount: null, lines: null, ship_to: null });
  assert.deepEqual(sparse.totals, [{ label: "Total", value: "RM 30.00" }]);
  assert.deepEqual(sparse.lines, []);
  assert.deepEqual(sparse.shipTo, []);
});

test("a cleared order shows the note and never a ship-to, even one the server left in", () => {
  const view = orderDetailView({ ...detail, buyer_cleared: true });
  assert.equal(view.shipTo, null);
  assert.equal(view.clearedNote, "Customer details removed after 90 days");
  assert.equal(BUYER_CLEARED_NOTE, view.clearedNote);
  assert.equal(view.lines.length, 2, "lines and totals stay");
});

test("only one record is published: the last card opened, falling back when it closes", () => {
  let open = [];
  assert.deepEqual(businessPageContext("orders", open), { tab: "business", section: "orders" });
  open = trackOpenOrder(open, "a", true);
  assert.deepEqual(businessPageContext("orders", open), {
    tab: "business", section: "orders", record: { kind: "order", id: "a" },
  });
  open = trackOpenOrder(open, "b", true);
  assert.equal(businessPageContext("orders", open).record.id, "b");
  open = trackOpenOrder(open, "b", false);
  assert.equal(businessPageContext("orders", open).record.id, "a");
  open = trackOpenOrder(open, "a", false);
  assert.equal("record" in businessPageContext("orders", open), false);
  // Opening twice never lists a card twice; closing one that is not open changes nothing.
  assert.deepEqual(trackOpenOrder(trackOpenOrder([], "a", true), "a", true), ["a"]);
  assert.deepEqual(trackOpenOrder(["a"], "z", false), ["a"]);
  // Products & stock publishes the open TikTok listing as a product record (id only).
  assert.deepEqual(businessPageContext("products", ["o1"], ["l1", "l2"]), {
    tab: "business", section: "products", record: { kind: "product", id: "l2" },
  });
  assert.deepEqual(businessPageContext("products", ["o1"], []), { tab: "business", section: "products" });
  assert.deepEqual(businessPageContext("orders", ["o1"], ["l1"]).record, { kind: "order", id: "o1" });
  assert.equal("record" in businessPageContext("stores", [], ["l1"]), false);
});

test("the Orders folds say what is true, and point to Connections only when not connected", () => {
  const connected = ordersAboutText("connected");
  assert.match(connected, /copied into fourstacks and kept up to date/);
  assert.match(connected, /never changes an order there/);
  assert.match(connected, /Shopee is not connected/);
  assert.doesNotMatch(connected, /Connect your TikTok Shop on Connections/);
  assert.match(ordersAboutText("not_connected"), /Connect your TikTok Shop on Connections\.$/);
  assert.equal(ordersAboutText(null), connected);
  for (const text of [connected, ordersAboutText("not_connected"), ORDERS_NOT_YET_TEXT, CATALOGUE_ABOUT_TEXT]) {
    assert.doesNotMatch(text, /not a live|don't update|Coming later|tenant admin|not connected or\s+checked/i);
  }
  assert.match(ORDERS_NOT_YET_TEXT, /does not send anything to TikTok Shop/);
  assert.match(CATALOGUE_ABOUT_TEXT, /separate and are not merged/);
});

const listing = {
  id: "l1", product_name: "Blue mug", variant_name: "Large", seller_sku: "MUG-1", price: "18.00",
  currency: "MYR", stock_qty: 4, product_status: "ACTIVATE", image_url: null,
  synced_at: "2026-11-02T01:00:00Z", same_sku_product: "Blue mug (local)",
};

test("a listing line carries variant, SKU, price and TikTok's stock", () => {
  assert.deepEqual(listingSummary(listing), {
    title: "Blue mug", subtitle: "Large · SKU MUG-1 · RM 18.00 · 4 in stock",
  });
  assert.equal(sameSkuLine(listing), "Same SKU as your product Blue mug (local)");
  assert.equal(listingStatus({ product_status: "ACTIVE" }).tone, "success");
  assert.deepEqual(listingStatus(listing), { label: "Active", tone: "success" });
  assert.deepEqual(listingStatus({ product_status: "DRAFT" }), { label: "Draft", tone: "neutral" });
  assert.equal(listingStatus({ product_status: null }).label, "Status not given");
  const rows = listingDetailGroups(listing).flatMap((group) => group.rows.map((row) => `${row.label}: ${row.value}`));
  assert.ok(rows.includes("TikTok stock: 4 in stock"));
  assert.ok(rows.includes("Synced from TikTok: 2 Nov 2026, 09:00"));
});

test("a masked or missing SKU says so and never claims a match; stock may be unknown", () => {
  const masked = { ...listing, variant_name: null, seller_sku: "  ", price: null, stock_qty: null, same_sku_product: null };
  assert.equal(listingSku(masked), null);
  assert.equal(listingSummary(masked).subtitle, "No SKU shared · No price · Stock not given");
  assert.equal(sameSkuLine(masked), null);
  assert.equal(isListingOut(masked), false, "unknown stock is not out of stock");
  assert.equal(listingStockText({ stock_qty: 0 }), "Out of stock");
  assert.equal(isListingOut({ stock_qty: 0 }), true);
  assert.equal(isListingOut({ stock_qty: -1 }), true);
  assert.equal(isListingOut({ stock_qty: 1 }), false);
});

test("the empty TikTok list says how to fill it only when no shop is connected", () => {
  assert.equal(listingsEmptyText("not_connected"),
    "No TikTok Shop products copied yet. Connect your shop on Connections.");
  assert.equal(listingsEmptyText("connected"), "No TikTok Shop products found.");
  assert.equal(listingsEmptyText(null), "No TikTok Shop products found.");
});

test("the detail and listing paths keep the platform admin's tenant", () => {
  assert.equal(orderDetailPath("", "a b"), "/console/v1/business/orders/a%20b");
  assert.equal(orderDetailPath("client_id=c1", "o1"), "/console/v1/business/orders/o1?client_id=c1");
  assert.equal(listingsPath(""), "/console/v1/business/listings");
  assert.equal(listingsPath("client_id=c1"), "/console/v1/business/listings?client_id=c1");
});

test("the Business page publishes the open order through the page-context publisher", async () => {
  const workspace = await readFile(new URL("../components/console/BusinessWorkspace.tsx", import.meta.url), "utf8");
  const cards = await readFile(new URL("../components/console/business/cards.tsx", import.meta.url), "utf8");
  // One publisher call for the whole page, fed by the open-card list; cards only report open and close.
  assert.equal(workspace.match(/usePublishContext\(/g).length, 1);
  assert.match(workspace, /usePublishContext\(businessPageContext\(section, openOrders, openListings\)\)/);
  assert.match(workspace, /trackOpenOrder\(current, id, open\)/);
  assert.match(workspace, /onOpen=\{onOrderOpen\}/);
  // A TikTok listing card reports open and close the same way (its id only).
  const listingCard = await readFile(new URL("../components/console/business/ListingCard.tsx", import.meta.url), "utf8");
  assert.match(listingCard, /onOpen=\{onOpen \? \(open\) => onOpen\(listing\.id, open\) : undefined\}/);
  assert.match(workspace, /onListingOpen=\{listingOpened\}/);
  assert.match(cards, /told\.current\?\.\(true\);\s+return \(\) => told\.current\?\.\(false\);/);
});

test("the technical switch opens cards but never asks for a detail or publishes a record", async () => {
  // Switch on, no card opened by a person: nothing is requested.
  assert.equal(detailRequested(true, false), false);
  assert.equal(detailRequested(false, false), false);
  // A person opened it and it is still open: requested. Closed again: not.
  assert.equal(detailRequested(true, true), true);
  assert.equal(detailRequested(false, true), false);
  // The card passes only that decision to its detail and to the page, never the switch's own state.
  const cards = await readFile(new URL("../components/console/business/cards.tsx", import.meta.url), "utf8");
  assert.match(cards, /const asked = detailRequested\(open, userOpen\);/);
  assert.match(cards, /detail\?\.\(asked\)/);
  assert.match(cards, /\[asked\]\);/);
  assert.match(cards, /setUserOpen\(next\)/);
  assert.doesNotMatch(cards, /detail\?\.\(open\)/);
  // No record is published while no card is open, so the context carries none.
  assert.deepEqual(businessPageContext("orders", [], []), { tab: "business", section: "orders" });
});

test("listing pictures are never rendered and provider text stays plain text", async () => {
  const card = await readFile(new URL("../components/console/business/ListingCard.tsx", import.meta.url), "utf8");
  const detail = await readFile(new URL("../components/console/business/OrderDetail.tsx", import.meta.url), "utf8");
  for (const source of [card, detail]) {
    assert.doesNotMatch(source, /<img|<Image|dangerouslySetInnerHTML|image_url\b.*src/);
  }
});
