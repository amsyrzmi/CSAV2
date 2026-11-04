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
  ORDER_STATUSES, PAYMENT_STATUSES, orderStatusChips, orderStatusLabel, orderStatusTone,
  paymentLabel, paymentTone, paymentOptions, formatRm, orderSummary, channelLabel, whenText,
  parseOrderCounts, orderChipOptions, effectiveCountsRange, productSummary, storeSummary,
  storeStatusTone, mergeRows, chipGroupLabel, TILE_STATUSES, orderTiles, outOfStock,
  orderDetailGroups, productDetailGroups, storeDetailGroups, activeFilters,
} = await import(await load("business-view.ts", {
  '"@/lib/date-range"': JSON.stringify(await load("date-range.ts")),
}));
const { localOrderPath, orderListPath, orderCountsPath, withCursor } = await import(
  await load("business.ts"),
);

const ENUM = [
  "draft", "pending_confirmation", "confirmed", "processing", "shipped", "delivered",
  "cancelled", "refunded",
];

test("orderStatusChips covers every enum value", () => {
  assert.deepEqual([...ORDER_STATUSES], ENUM);
  const chips = orderStatusChips();
  assert.deepEqual(chips.map((chip) => chip.value), ENUM);
  const label = Object.fromEntries(chips.map((chip) => [chip.value, chip.label]));
  assert.equal(label.pending_confirmation, "Waiting for confirmation");
  assert.equal(label.processing, "Being prepared");
  for (const chip of chips) {
    assert.ok(chip.label.length > 0);
    assert.doesNotMatch(chip.label, /_/, "a raw code never shows on a chip");
    assert.equal(orderStatusLabel(chip.value), chip.label);
    assert.ok(["neutral", "info", "attention", "success", "error"].includes(orderStatusTone(chip.value)));
  }
  assert.equal(new Set(chips.map((chip) => chip.label)).size, ENUM.length, "labels are distinct");
  // A code the page does not know keeps its code rather than being hidden.
  assert.equal(orderStatusLabel("on_hold"), "on_hold");
  assert.equal(orderStatusTone("on_hold"), "neutral");
});

test("formatRm for MYR and another currency", () => {
  assert.equal(formatRm("1234.5", "MYR"), "RM 1,234.50");
  assert.equal(formatRm(1234.5, "MYR"), "RM 1,234.50");
  assert.equal(formatRm("59.9000", "MYR"), "RM 59.90");
  assert.equal(formatRm("0", "MYR"), "RM 0.00");
  assert.equal(formatRm("1000000", "MYR"), "RM 1,000,000.00");
  assert.equal(formatRm("12", "USD"), "USD 12.00");
  assert.equal(formatRm("1234.5", "SGD"), "SGD 1,234.50");
  assert.equal(formatRm("not a number", "MYR"), "RM not a number");
  assert.equal(formatRm("", "MYR"), "RM -");
});

test("paymentLabel", () => {
  assert.deepEqual(PAYMENT_STATUSES, ["unpaid", "cod_pending", "paid", "refunded", "failed"]);
  assert.equal(paymentLabel("unpaid"), "Not paid yet");
  assert.equal(paymentLabel("cod_pending"), "Pay on delivery");
  assert.equal(paymentLabel("paid"), "Paid");
  assert.equal(paymentLabel("refunded"), "Payment refunded");
  assert.equal(paymentLabel("failed"), "Payment failed");
  assert.equal(paymentLabel("mystery"), "mystery");
  assert.equal(paymentTone("paid"), "success");
  assert.equal(paymentTone("failed"), "error");
  assert.equal(paymentTone("mystery"), "neutral");
  assert.deepEqual(paymentOptions().map((option) => option.value), PAYMENT_STATUSES);
  assert.ok(paymentOptions().every((option) => !/_/.test(option.label)));
});

test("orderSummary", () => {
  const order = {
    id: "9f1c2d3e-aaaa-bbbb-cccc-0123456789ab", external_order_id: "TT-5521",
    channel: "tiktok_shop", total: "1234.50", currency: "MYR",
    created_at: "2026-11-02T01:15:00Z",
  };
  assert.deepEqual(orderSummary(order), {
    title: "Order TT-5521",
    subtitle: "TikTok Shop · RM 1,234.50 · 2 Nov 2026, 09:15",
  });
  // No platform reference: the first block of the id, never the whole id.
  const local = orderSummary({ ...order, external_order_id: null, channel: "web", currency: "USD" });
  assert.equal(local.title, "Order 9f1c2d3e", "matches the id in the details");
  assert.equal(local.subtitle, "Web · USD 1,234.50 · 2 Nov 2026, 09:15");
  assert.equal(orderSummary({ ...order, created_at: "nope" }).subtitle, "TikTok Shop · RM 1,234.50");
  assert.equal(channelLabel("manual"), "Manual");
  assert.equal(channelLabel("carrier_pigeon"), "carrier_pigeon");
  assert.equal(whenText(null), "Not recorded");
  assert.equal(whenText("2026-11-02T16:05:00Z"), "3 Nov 2026, 00:05");
});

test("counts: only a well-formed read shows figures, never a zero for a failure", () => {
  const by_status = Object.fromEntries(ENUM.map((status, index) => [status, index]));
  const counts = parseOrderCounts({ total: 28, by_status });
  assert.equal(counts?.total, 28);
  const options = orderChipOptions(counts);
  assert.deepEqual(options.map((option) => option.value), ["all", ...ENUM]);
  assert.equal(options[0].label, "All orders");
  assert.equal(options[0].count, 28);
  assert.equal(options[2].count, 1);
  assert.equal(options.find((option) => option.value === "draft")?.count, 0);
  for (const bad of [null, undefined, "x", {}, { total: "5", by_status }, { total: 5 },
    { total: 5, by_status: null }, { total: -1, by_status }]) {
    assert.equal(parseOrderCounts(bad), null);
  }
  // A status the read leaves out has no figure rather than 0.
  const partial = parseOrderCounts({ total: 3, by_status: { draft: 3 } });
  assert.equal(orderChipOptions(partial).find((o) => o.value === "shipped")?.count, null);
  // No counts (loading or failed): labels only.
  assert.ok(orderChipOptions(null).every((option) => option.count === null));
  assert.equal(orderChipOptions(null)[0].label, "All orders");
});

test("effectiveCountsRange defaults to the last 30 days and honours the explicit choices", () => {
  const now = new Date(2026, 10, 2, 12, 0, 0);
  assert.deepEqual(
    effectiveCountsRange({ from: null, to: null }, false, now),
    { kind: "default", range: { from: "2026-10-04", to: "2026-11-02" } },
  );
  assert.deepEqual(
    effectiveCountsRange({ from: null, to: null }, true, now),
    { kind: "all", range: { from: null, to: null } },
  );
  const chosen = { from: "2026-10-01", to: "2026-10-10" };
  assert.deepEqual(effectiveCountsRange(chosen, false, now), { kind: "chosen", range: chosen });
  assert.deepEqual(effectiveCountsRange(chosen, true, now), { kind: "chosen", range: chosen });
  assert.equal(effectiveCountsRange({ from: "2026-10-01", to: null }, false, now).kind, "chosen");
});

test("order query strings use URLSearchParams, UTC instants and omit empty parameters", () => {
  assert.equal(localOrderPath("", "", ""), "/console/v1/business/orders?");
  assert.equal(orderListPath("", {}), "/console/v1/business/orders?");
  const from = new Date(2026, 9, 4).toISOString();
  const to = new Date(2026, 10, 3).toISOString();
  const list = new URL(orderListPath("client_id=T1", {
    channel: "tiktok_shop", shopId: "S/1+2", from, to, status: "shipped", paymentStatus: "paid",
  }), "http://x.test");
  assert.equal(list.pathname, "/console/v1/business/orders");
  assert.equal(list.searchParams.get("client_id"), "T1");
  assert.equal(list.searchParams.get("channel"), "tiktok_shop");
  assert.equal(list.searchParams.get("shop_id"), "S/1+2");
  assert.equal(list.searchParams.get("from"), from);
  assert.equal(list.searchParams.get("to"), to);
  assert.match(list.searchParams.get("from"), /Z$/);
  assert.equal(list.searchParams.get("status"), "shipped");
  assert.equal(list.searchParams.get("payment_status"), "paid");
  assert.doesNotMatch(list.search, /\+08:00|%2B08/);
  const empty = new URL(orderListPath("", { status: "", paymentStatus: "", from: null, to: null }),
    "http://x.test");
  assert.equal([...empty.searchParams].length, 0);
  assert.throws(() => orderListPath("", { shopId: "S1" }), /source\/platform/);

  const counts = new URL(orderCountsPath("client_id=T1", {
    channel: "web", from, to, status: "shipped", paymentStatus: "unpaid",
  }), "http://x.test");
  assert.equal(counts.pathname, "/console/v1/business/orders/counts");
  assert.equal(counts.searchParams.has("status"), false, "the counts read never gets a status");
  assert.equal(counts.searchParams.get("payment_status"), "unpaid");
  assert.equal(counts.searchParams.get("channel"), "web");
  assert.equal(counts.searchParams.get("from"), from);
  assert.equal(orderCountsPath("", {}), "/console/v1/business/orders/counts?");

  assert.equal(withCursor("/console/v1/business/orders?", "abc"), "/console/v1/business/orders?before=abc");
  assert.equal(
    withCursor("/console/v1/business/orders?client_id=T1&status=paid", "a b&c"),
    "/console/v1/business/orders?client_id=T1&status=paid&before=a+b%26c",
  );
  assert.equal(withCursor("/console/v1/business/orders?x=1", null), "/console/v1/business/orders?x=1");
});

test("product and store summaries are plain and keep codes out of the line", () => {
  const product = {
    sku: "MUG-1", name: "Blue mug", price: "18", currency: "MYR", available_qty: 7, is_active: true,
  };
  assert.deepEqual(productSummary(product), { title: "Blue mug", subtitle: "RM 18.00 · 7 available" });
  assert.equal(productSummary({ ...product, available_qty: 1 }).subtitle, "RM 18.00 · 1 available");
  assert.equal(productSummary({ ...product, available_qty: 0 }).subtitle, "RM 18.00 · None available");
  const store = { platform: "tiktok_shop", shop_id: "S-77", connection: { status: "unverified" } };
  assert.deepEqual(storeSummary(store), {
    title: "TikTok Shop · S-77",
    subtitle: "Saved record only, not checked with the marketplace",
    status: "Not verified",
  });
  assert.equal(storeSummary({ ...store, shop_id: null }).title, "TikTok Shop · Store ID not recorded");
  for (const status of ["unsupported", "missing_shop_id", "unverified", "revoked", "disconnected",
    "expired", "app_unavailable", "binding_missing", "permissions_missing", "authorization_recorded"]) {
    const { status: label } = storeSummary({ ...store, connection: { status } });
    assert.ok(label && !/_/.test(label), status);
  }
});

test("older rows join the rows on screen and a row never shows twice", () => {
  const first = [{ id: "a", n: 1 }, { id: "b", n: 2 }];
  const merged = mergeRows(first, [{ id: "b", n: 9 }, { id: "c", n: 3 }]);
  assert.deepEqual(merged.map((row) => row.id), ["a", "b", "c"]);
  assert.equal(merged[1].n, 2, "the row already shown is kept");
  assert.deepEqual(first.map((row) => row.id), ["a", "b"], "the old list is not changed");
  assert.deepEqual(mergeRows([], []), []);
  assert.equal(storeStatusTone("authorization_recorded"), "info");
  assert.equal(storeStatusTone("whatever"), "neutral");
});

test("the chip group names the window its numbers cover", () => {
  assert.equal(chipGroupLabel("default"), "Orders, last 30 days");
  assert.equal(chipGroupLabel("all"), "Orders, all dates");
  assert.equal(chipGroupLabel("chosen"), "Orders, dates you chose");
});

test("orderTiles maps one status per tile and marks the pressed one", () => {
  assert.deepEqual(TILE_STATUSES.map((t) => [t.key, t.status, t.label]), [
    ["to_confirm", "pending_confirmation", "To confirm"],
    ["to_pack", "confirmed", "To pack"],
    ["on_the_way", "shipped", "On the way"],
  ]);
  const counts = { total: 9, by_status: { pending_confirmation: 4, confirmed: 0, shipped: 3, draft: 2 } };
  assert.deepEqual(orderTiles(counts, "confirmed"), [
    { key: "to_confirm", label: "To confirm", value: 4, pressed: false, status: "pending_confirmation" },
    { key: "to_pack", label: "To pack", value: 0, pressed: true, status: "confirmed" },
    { key: "on_the_way", label: "On the way", value: 3, pressed: false, status: "shipped" },
  ]);
  assert.ok(orderTiles(counts, "all").every((tile) => tile.pressed === false));
});

test("orderTiles shows null values when counts are missing", () => {
  for (const tile of orderTiles(null, "all")) assert.equal(tile.value, null);
  // A well-formed read without one status is also not a zero.
  assert.equal(orderTiles({ total: 1, by_status: { shipped: 1 } }, "all")[0].value, null);
});

test("outOfStock counts zero and negative availability and captions a partial read", () => {
  const rows = [{ available_qty: 0 }, { available_qty: -2 }, { available_qty: 1 }, { available_qty: 7 }];
  assert.deepEqual(outOfStock(rows, false), { value: 2, caption: null });
  assert.deepEqual(outOfStock(rows, true), { value: 2, caption: "Among your newest 50 products" });
  assert.deepEqual(outOfStock([], false), { value: 0, caption: null });
});

const ORDER = {
  id: "00000001-0000-4000-8000-000000000000", status: "shipped", payment_status: "paid",
  total: "1001.50", currency: "MYR", channel: "tiktok_shop", external_order_id: "EXT-1",
  tracking_no: "TRK1", carrier: "J&T", placed_at: "2026-11-02T01:58:00Z",
  shipped_at: "2026-11-02T02:30:00Z", created_at: "2026-11-02T01:58:00Z", updated_at: "2026-11-02T03:00:00Z",
};

test("orderDetailGroups uses Courier not added yet, Source with the channel name and Last changed here", () => {
  const groups = orderDetailGroups(ORDER);
  assert.deepEqual(groups.map((g) => g.title), ["Payment", "Delivery", "Record"]);
  assert.deepEqual(groups[0].rows, [
    { label: "Payment", value: "Paid" }, { label: "Total", value: "RM 1,001.50" },
  ]);
  assert.deepEqual(groups[1].rows.map((r) => r.label), ["Tracking number", "Courier", "Shipped"]);
  assert.deepEqual(groups[1].rows.map((r) => r.value), ["TRK1", "J&T", "2 Nov 2026, 10:30"]);
  assert.deepEqual(groups[2].rows.map((r) => r.label), [
    "Marketplace order no.", "Source", "Placed", "Created", "Last changed here",
  ]);
  assert.equal(groups[2].rows[1].value, "TikTok Shop");
  assert.equal(groups[2].rows[4].value, "2 Nov 2026, 11:00");
  const bare = orderDetailGroups({
    ...ORDER, tracking_no: null, carrier: null, shipped_at: null, external_order_id: null, placed_at: null,
  });
  assert.deepEqual(bare[1].rows.map((r) => r.value), ["not added yet", "not added yet", "Not shipped yet"]);
  assert.equal(bare[2].rows[0].value, "not added yet");
  assert.equal(bare[2].rows[2].value, "Not recorded");
});

test("productDetailGroups and storeDetailGroups", () => {
  const product = {
    name: "Blue mug", price: "18.00", currency: "MYR", stock_qty: 10, reserved_qty: 3,
    available_qty: 7, is_active: true,
  };
  const groups = productDetailGroups(product);
  assert.deepEqual(groups.map((g) => g.title), ["Stock", "Price", "Status"]);
  assert.deepEqual(groups[0].rows, [
    { label: "In stock", value: "10" }, { label: "Set aside for orders", value: "3" },
    { label: "Available", value: "7" },
  ]);
  assert.equal(groups[1].rows[0].value, "RM 18.00");
  assert.equal(groups[2].rows[0].value, "For sale");
  assert.equal(productDetailGroups({ ...product, is_active: false })[2].rows[0].value, "Not for sale");
  const store = {
    platform: "tiktok_shop", shop_id: "SHOP-A", connection: { status: "unverified" },
    updated_at: "2026-10-03T00:00:00Z",
  };
  const stores = storeDetailGroups(store);
  assert.deepEqual(stores.map((g) => g.title), ["Marketplace", "Store ID", "Status", "Last changed here"]);
  assert.deepEqual(stores.map((g) => g.rows[0].value), ["TikTok Shop", "SHOP-A", "Not verified", "3 Oct 2026, 08:00"]);
  assert.equal(storeDetailGroups({ ...store, shop_id: null })[1].rows[0].value, "not recorded");
});

test("activeFilters lists only active filters", () => {
  const none = { range: { from: null, to: null }, payment: "", status: "all" };
  assert.deepEqual(activeFilters(none), []);
  assert.deepEqual(
    activeFilters({ range: { from: "2026-10-26", to: "2026-11-01" }, payment: "unpaid", status: "shipped" }),
    [
      { key: "date", label: "26 Oct to 1 Nov 2026" },
      { key: "payment", label: "Not paid yet" },
      { key: "status", label: "Shipped" },
    ],
  );
  assert.deepEqual(activeFilters({ ...none, payment: "failed" }), [{ key: "payment", label: "Payment failed" }]);
});
