import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Browser-side dates (the days a person picks) are written in the viewer's zone; pin it.
process.env.TZ = "Asia/Kuala_Lumpur";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const subsSource = await readFile(new URL("../lib/subscriptions-api.ts", import.meta.url), "utf8");
const subsUrl = moduleUrl(subsSource.replace('"./api"', JSON.stringify(apiUrl)));
const viewSource = await readFile(new URL("../lib/subscriptions-view.ts", import.meta.url), "utf8");
const {
  summaryCounts, isEndingSoon, formatInTenantZone, customerChipOptions, filterCustomers, reviewSentence,
} = await import(moduleUrl(viewSource.replace('"@/lib/subscriptions-api"', JSON.stringify(subsUrl))));

const NOW = new Date("2026-11-02T02:00:00Z");
const DAY = 86_400_000;
const at = (days) => new Date(NOW.getTime() + days * DAY).toISOString();

let counter = 0;
function sub(overrides = {}) {
  counter += 1;
  return {
    id: `s${counter}`, status: "active", renews_at: at(90), cancel_at_period_end: false,
    customer: { id: `c${counter}`, full_name: `Customer ${counter}`, phone: null, email: null },
    plan: "basic", plan_name: "Basic", price: "59.90", currency: "MYR",
    ...overrides,
  };
}
const request = () => ({ id: `r${++counter}` });

test("summaryCounts exact, plus-suffix and null", () => {
  const customers = [
    sub(),
    sub({ renews_at: at(10) }),
    sub({ status: "paused", renews_at: at(10) }),
    sub({ status: "cancelled", renews_at: null }),
  ];
  const requests = [request(), request()];
  // Exact numbers as text when no further page exists.
  assert.deepEqual(summaryCounts(customers, requests, false, false, NOW), {
    active: "2", endingSoon: "1", requestsWaiting: "2",
  });
  // A further page of customers makes both customer figures "at least"; requests stay exact.
  assert.deepEqual(summaryCounts(customers, requests, true, false, NOW), {
    active: "2+", endingSoon: "1+", requestsWaiting: "2",
  });
  assert.deepEqual(summaryCounts(customers, requests, false, true, NOW), {
    active: "2", endingSoon: "1", requestsWaiting: "2+",
  });
  // A real zero is a zero; an empty page with more behind it is "0+", never a bare zero.
  assert.deepEqual(summaryCounts([], [], false, false, NOW), {
    active: "0", endingSoon: "0", requestsWaiting: "0",
  });
  assert.equal(summaryCounts([], [], true, false, NOW)?.active, "0+");
  // A read that failed or has not arrived hides the whole row: never a zero for a failure.
  assert.equal(summaryCounts(null, requests, false, false, NOW), null);
  assert.equal(summaryCounts(customers, null, false, false, NOW), null);
  assert.equal(summaryCounts(undefined, undefined, false, false, NOW), null);
});

test("endingSoon window", () => {
  const soon = (overrides) => isEndingSoon(sub(overrides), NOW);
  assert.equal(soon({ renews_at: at(30) }), true, "exactly 30 days is inside");
  assert.equal(soon({ renews_at: at(30.01) }), false, "just past 30 days is outside");
  assert.equal(soon({ renews_at: at(0.5) }), true);
  assert.equal(soon({ renews_at: at(-2) }), true, "a renewal that is due now counts");
  // Flagged to cancel at the period end counts however far the end is.
  assert.equal(soon({ renews_at: at(200), cancel_at_period_end: true }), true);
  // Only active customers: paused and cancelled never count.
  assert.equal(soon({ status: "paused", renews_at: at(5) }), false);
  assert.equal(soon({ status: "cancelled", renews_at: at(5), cancel_at_period_end: true }), false);
  // No period end at all and no flag: not ending.
  assert.equal(soon({ renews_at: null }), false);
  assert.equal(soon({ renews_at: "not a date" }), false);
});

test("formatInTenantZone Asia/Kuala_Lumpur vs UTC", () => {
  const iso = "2026-11-27T18:00:00Z";
  assert.equal(formatInTenantZone(iso, "Asia/Kuala_Lumpur"), "28 Nov 2026, 02:00");
  assert.equal(formatInTenantZone(iso, "UTC"), "27 Nov 2026, 18:00");
  assert.equal(formatInTenantZone(iso, "America/Los_Angeles"), "27 Nov 2026, 10:00");
  // Unknown zone: the browser's zone (pinned to Kuala Lumpur above).
  assert.equal(formatInTenantZone(iso, null), "28 Nov 2026, 02:00");
  assert.equal(formatInTenantZone("nonsense", "UTC"), "—");
});

test("customer chips count only a complete list and filter the loaded rows", () => {
  const rows = [
    sub({ renews_at: at(5) }),
    sub(),
    sub({ status: "paused" }),
    sub({ status: "cancelled", renews_at: null }),
    sub({ cancel_at_period_end: true }),
  ];
  const whole = customerChipOptions(rows, false, NOW);
  assert.deepEqual(whole.map((o) => [o.value, o.label, o.count]), [
    ["all", "All", 5], ["active", "Active", 3], ["paused", "Paused", 1],
    ["cancelled", "Cancelled", 1], ["ending", "Ending soon", 2],
  ]);
  // A paged list: the figures would only describe the loaded page, so none are drawn.
  assert.ok(customerChipOptions(rows, true, NOW).every((o) => o.count === null));
  assert.equal(filterCustomers(rows, "all", NOW).length, 5);
  assert.deepEqual(filterCustomers(rows, "paused", NOW).map((r) => r.status), ["paused"]);
  assert.equal(filterCustomers(rows, "active", NOW).length, 3);
  assert.equal(filterCustomers(rows, "ending", NOW).length, 2);
});

test("reviewSentence says the change in plain words", () => {
  const ctx = {
    customer: "Aisyah Rahman",
    currentPlan: "Basic",
    renewsAt: "2026-11-27T01:00:00Z",
    timeZone: "Asia/Kuala_Lumpur",
    planName: (code) => ({ basic: "Basic", pro: "Pro" })[code] ?? code,
    planPrice: (code) => (code === "pro" ? "RM 99.00 a month" : null),
  };
  assert.equal(
    reviewSentence({ action: "pause", reason: null }, ctx),
    "This will pause Aisyah Rahman's subscription until someone resumes it.",
  );
  assert.equal(
    reviewSentence({ action: "pause", resume_at: "2099-11-15" }, ctx),
    "This will pause Aisyah Rahman's subscription until 15 Nov 2099.",
  );
  assert.equal(
    reviewSentence({ action: "resume" }, ctx),
    "This will resume Aisyah Rahman's subscription now.",
  );
  assert.equal(
    reviewSentence({ action: "change_plan", plan_code: "pro" }, ctx),
    "This will move Aisyah Rahman from Basic to Pro, starting now, at RM 99.00 a month.",
  );
  assert.equal(
    reviewSentence({ action: "change_plan", plan_code: "pro", price_override: "70" }, ctx),
    "This will move Aisyah Rahman from Basic to Pro, starting now, at a special price of RM 70.00.",
  );
  assert.equal(
    reviewSentence({ action: "change_plan", plan_code: "pro", effective_at: "2026-12-09T16:00:00.000Z" }, ctx),
    "This will move Aisyah Rahman from Basic to Pro, starting on 10 Dec 2026, at the plan's price on that day.",
  );
  assert.equal(
    reviewSentence({ action: "change_plan", plan_code: "gold" }, ctx),
    "This will move Aisyah Rahman from Basic to gold, starting now.",
  );
  assert.equal(
    reviewSentence({ action: "cancel_at_period_end" }, ctx),
    "This will cancel Aisyah Rahman's subscription when the current period ends on 27 Nov 2026. They keep it until then.",
  );
  assert.equal(
    reviewSentence({ action: "cancel_at_period_end" }, { ...ctx, renewsAt: null }),
    "This will cancel Aisyah Rahman's subscription when the current period ends. They keep it until then.",
  );
  assert.equal(
    reviewSentence({ action: "cancel_now" }, ctx),
    "This will cancel Aisyah Rahman's subscription straight away. A cancelled subscription cannot be started again.",
  );
  assert.equal(
    reviewSentence({ action: "extend_renewal", renews_at: "2026-12-14T01:00:00.000Z" }, ctx),
    "This will move the next renewal for Aisyah Rahman from 27 Nov 2026 to 14 Dec 2026.",
  );
});
