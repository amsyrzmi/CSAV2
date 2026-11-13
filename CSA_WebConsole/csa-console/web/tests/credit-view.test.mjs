import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const read = (file) => readFile(new URL(file, import.meta.url), "utf8");
const apiUrl = moduleUrl(await read("../lib/api.ts"));
const creditApiUrl = moduleUrl((await read("../lib/credit-api.ts")).replace('"./api"', JSON.stringify(apiUrl)));
const view = await import(moduleUrl(await read("../lib/credit-view.ts")));
const client = await import(creditApiUrl);
const {
  formatRm, cycleLabel, banner, proratedNote, stateMessage, dailySeries, agentSeries, priceTicks,
  cycleOption, newestFirst, executionTime, executionPrice, peakRow, countText,
} = view;

// 12 Nov 2026 in Kuala Lumpur: the current year is 2026.
const NOW = new Date("2026-11-12T04:00:00Z");

function usage(overrides = {}) {
  return {
    cycle_start: "2026-11-05", cycle_end: "2026-12-05",
    billed_from: "2026-11-05", billed_to: "2026-12-05",
    executions: 3, ai_usage_price: "0.06", platform_fee: "99.99", total: "100.05",
    final: false, status: "ok",
    by_agent: [
      { agent_id: "support", title: "Customer support assistant", executions: 2, price: "0.0400" },
      { agent_id: "training", title: "Training summary", executions: 1, price: "0.0200" },
    ],
    daily: [
      { day: "2026-11-05", price: "0.0000" },
      { day: "2026-11-06", price: "0.0600" },
      { day: "2026-11-07", price: "0.0000" },
    ],
    ...overrides,
  };
}

test("formatRm keeps every digit, groups thousands and adds the RM prefix", () => {
  assert.equal(formatRm("0.0001", 4), "RM 0.0001");
  assert.equal(formatRm("12345.6789", 4), "RM 12,345.6789");
  assert.equal(formatRm("100.00", 2), "RM 100.00");
  assert.equal(formatRm("1000000.00", 2), "RM 1,000,000.00");
  assert.equal(formatRm("0.00", 2), "RM 0.00");
  assert.equal(formatRm("0.0000", 4), "RM 0.0000");
  assert.equal(formatRm("999.99", 2), "RM 999.99");
  // Past what a float holds exactly: still every digit.
  assert.equal(formatRm("123456789012345678.1234", 4), "RM 123,456,789,012,345,678.1234");
  assert.equal(formatRm("9007199254740993.01", 2), "RM 9,007,199,254,740,993.01");
});

test("formatRm pads short values and rounds only digits past the places shown, half up", () => {
  assert.equal(formatRm("5", 2), "RM 5.00");
  assert.equal(formatRm("0.1", 4), "RM 0.1000");
  assert.equal(formatRm("100.00", 4), "RM 100.0000");
  assert.equal(formatRm("0.10000", 4), "RM 0.1000");
  assert.equal(formatRm("1.23456", 4), "RM 1.2346");
  assert.equal(formatRm("1.23454", 4), "RM 1.2345");
  assert.equal(formatRm("9.995", 2), "RM 10.00");
  assert.equal(formatRm("999999.995", 2), "RM 1,000,000.00");
  assert.equal(formatRm("-1.50", 2), "-RM 1.50");
  assert.equal(formatRm("-0.00", 2), "RM 0.00");
});

test("formatRm refuses anything that is not a plain decimal", () => {
  for (const bad of ["", " ", "abc", "1e3", "NaN", "Infinity", "1,000.00", "1.2.3", ".5", "5."]) {
    assert.equal(formatRm(bad, 2), "—", JSON.stringify(bad));
  }
});

test("cycleLabel shows the year only outside the current year or across a new year", () => {
  assert.equal(cycleLabel("2026-09-05", "2026-10-05", NOW), "5 Sep to 5 Oct");
  assert.equal(cycleLabel("2026-12-05", "2027-01-05", NOW), "5 Dec 2026 to 5 Jan 2027");
  assert.equal(cycleLabel("2025-09-05", "2025-10-05", NOW), "5 Sep 2025 to 5 Oct 2025");
  // The current year is Kuala Lumpur's: 31 Dec 2026 17:00 UTC is already 1 Jan 2027 there.
  assert.equal(cycleLabel("2027-01-05", "2027-02-05", new Date("2026-12-31T17:00:00Z")), "5 Jan to 5 Feb");
});

test("banner says the bill is so far for an open cycle and nothing for a closed one", () => {
  assert.equal(banner(usage(), NOW), "Bill so far, final on 5 Dec");
  assert.equal(banner(usage({ status: "pending_prices" }), NOW), "Bill so far, final on 5 Dec");
  assert.equal(banner(usage({ final: true }), NOW), null);
  // No bill at all: never "Bill so far".
  assert.equal(banner(usage({ status: "not_set_up", billed_from: null, billed_to: null }), NOW), null);
  assert.equal(banner(usage({ cycle_start: "2026-12-05", cycle_end: "2027-01-05" }), NOW),
    "Bill so far, final on 5 Jan 2027");
});

test("proratedNote names a first or last period and stays quiet for a full cycle", () => {
  assert.equal(proratedNote(usage(), NOW), null);
  assert.equal(proratedNote(usage({ billed_from: "2026-11-15" }), NOW), "Billed from 15 Nov");
  assert.equal(proratedNote(usage({ billed_to: "2026-11-20" }), NOW), "Billed to 20 Nov");
  assert.equal(proratedNote(usage({ billed_from: "2026-11-15", billed_to: "2026-11-20" }), NOW),
    "Billed from 15 Nov to 20 Nov");
  assert.equal(proratedNote(usage({ billed_from: null, billed_to: null, status: "not_set_up" }), NOW), null);
  assert.equal(proratedNote(usage({ cycle_start: "2025-08-05", cycle_end: "2025-09-05",
    billed_from: "2025-08-15", billed_to: "2025-09-05" }), NOW), "Billed from 15 Aug 2025");
});

test("stateMessage is plain words for the two non-ok states", () => {
  assert.equal(stateMessage("not_set_up"), "Billing not set up yet");
  assert.equal(stateMessage("pending_prices"), "Some usage is still being priced");
  assert.equal(stateMessage("ok"), null);
});

test("dailySeries keeps every day in order, zero days included, with the exact price as text", () => {
  const rows = dailySeries(usage());
  assert.deepEqual(rows.map((r) => r.key), ["2026-11-05", "2026-11-06", "2026-11-07"]);
  assert.deepEqual(rows.map((r) => r.label), ["5 Nov", "6 Nov", "7 Nov"]);
  assert.deepEqual(rows.map((r) => r.value), [0, 0.06, 0]);
  assert.deepEqual(rows.map((r) => r.price), ["0.0000", "0.0600", "0.0000"]);
  assert.deepEqual(rows.map((r) => r.text), ["RM 0.0000", "RM 0.0600", "RM 0.0000"]);
  assert.deepEqual(dailySeries(usage({ daily: [] })), []);
});

test("agentSeries keeps the order the server gave, with titles and exact prices", () => {
  const rows = agentSeries(usage({ by_agent: [
    { agent_id: "hr", title: "HR assistant", executions: 1, price: "0.0100" },
    { agent_id: "support", title: "Customer support assistant", executions: 9, price: "12345.6789" },
  ] }));
  assert.deepEqual(rows.map((r) => r.key), ["hr", "support"]);
  assert.deepEqual(rows.map((r) => r.label), ["HR assistant", "Customer support assistant"]);
  assert.deepEqual(rows.map((r) => r.value), [0.01, 12345.6789]);
  assert.deepEqual(rows.map((r) => r.text), ["RM 0.0100", "RM 12,345.6789"]);
  assert.deepEqual(rows.map((r) => r.executions), [1, 9]);
});

test("peakRow finds the highest exact price, the first of equals, and none when all are zero", () => {
  const rows = dailySeries(usage({ daily: [
    { day: "2026-11-05", price: "0.0300" },
    { day: "2026-11-06", price: "9007199254740993.0001" },
    { day: "2026-11-07", price: "9007199254740993.0001" },
  ] }));
  assert.equal(peakRow(rows)?.key, "2026-11-06");
  assert.equal(peakRow(dailySeries(usage({ daily: [{ day: "2026-11-05", price: "0.0000" }] }))), null);
  assert.equal(peakRow([]), null);
});

test("countText groups a whole count", () => {
  assert.equal(countText(0), "0");
  assert.equal(countText(1234567), "1,234,567");
});

test("priceTicks steps cleanly from zero past the largest value, labelled with exact amounts", () => {
  const small = priceTicks(["0.0000", "0.0200", "0.0010"]);
  assert.deepEqual(small.ticks, [0, 0.005, 0.01, 0.015, 0.02]);
  assert.deepEqual(small.labels, ["RM 0.0000", "RM 0.0050", "RM 0.0100", "RM 0.0150", "RM 0.0200"]);
  const large = priceTicks(["284.5200", "12.0000"]);
  assert.deepEqual(large.ticks, [0, 100, 200, 300]);
  assert.deepEqual(large.labels, ["RM 0.00", "RM 100.00", "RM 200.00", "RM 300.00"]);
  const cents = priceTicks(["0.0700"]);
  assert.deepEqual(cents.labels, ["RM 0.00", "RM 0.02", "RM 0.04", "RM 0.06", "RM 0.08"]);
  assert.deepEqual(priceTicks(["0.0000"]), { ticks: [0], labels: ["RM 0.00"] });
  assert.deepEqual(priceTicks([]), { ticks: [0], labels: ["RM 0.00"] });
  for (const { ticks, labels } of [small, large, cents]) {
    assert.equal(ticks.length, labels.length);
    assert.ok(ticks.length <= 5);
  }
});

test("cycleOption marks the open cycle and shows the bill at 2 places", () => {
  assert.equal(cycleOption({ start: "2026-11-05", end: "2026-12-05", total: "100.05", final: false }, NOW),
    "5 Nov to 5 Dec (open), bill so far RM 100.05");
  assert.equal(cycleOption({ start: "2026-10-05", end: "2026-11-05", total: "1234.50", final: true }, NOW),
    "5 Oct to 5 Nov, RM 1,234.50");
  assert.equal(cycleOption({ start: "2026-09-05", end: "2026-10-05", total: null, final: true }, NOW),
    "5 Sep to 5 Oct, billing not set up");
});

test("newestFirst orders cycles by start, newest first, without changing the input", () => {
  const picks = [
    { start: "2026-09-05", end: "2026-10-05", total: null, final: true },
    { start: "2026-11-05", end: "2026-12-05", total: "1.00", final: false },
    { start: "2026-10-05", end: "2026-11-05", total: "2.00", final: true },
  ];
  assert.deepEqual(newestFirst(picks).map((p) => p.start), ["2026-11-05", "2026-10-05", "2026-09-05"]);
  assert.equal(picks[0].start, "2026-09-05");
});

test("executionTime is day, short month, year and 24-hour time in the given zone", () => {
  assert.equal(executionTime("2026-11-12T06:05:00Z", "Asia/Kuala_Lumpur"), "12 Nov 2026, 14:05");
  assert.equal(executionTime("2026-11-12T16:30:00+00:00", "Asia/Kuala_Lumpur"), "13 Nov 2026, 00:30");
  assert.equal(executionTime("not a time", "Asia/Kuala_Lumpur"), "—");
});

test("executionPrice is the price at 4 places, or Pending while it is being priced", () => {
  assert.equal(executionPrice("0.0123"), "RM 0.0123");
  assert.equal(executionPrice(null), "Pending");
});

// ---- The API client: paths and the cursor rule ----

async function capture(run, reply) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push([path, init]);
    return Response.json(reply);
  };
  try {
    await run();
  } finally {
    globalThis.fetch = original;
  }
  return requests.map(([path, init]) => [path, init?.method ?? "GET"]);
}

test("fetchUsage and fetchCycles read the billing routes with GET and no tenant id", async () => {
  assert.deepEqual(await capture(() => client.fetchUsage(), usage()), [["/console/v1/billing/usage", "GET"]]);
  assert.deepEqual(await capture(() => client.fetchUsage("2026-10-05"), usage()),
    [["/console/v1/billing/usage?cycle=2026-10-05", "GET"]]);
  assert.deepEqual(await capture(() => client.fetchCycles(), []), [["/console/v1/billing/usage/cycles", "GET"]]);
});

test("fetchExecutions always sends the cycle and leaves the cursor out of the first page", async () => {
  const page = { items: [], next_cursor: null };
  assert.deepEqual(await capture(() => client.fetchExecutions("2026-11-05"), page),
    [["/console/v1/billing/usage/executions?cycle=2026-11-05", "GET"]]);
  for (const empty of [null, ""]) {
    assert.deepEqual(await capture(() => client.fetchExecutions("2026-11-05", empty), page),
      [["/console/v1/billing/usage/executions?cycle=2026-11-05", "GET"]], JSON.stringify(empty));
  }
  assert.deepEqual(await capture(() => client.fetchExecutions("2026-11-05", "MjAy-_x"), page),
    [["/console/v1/billing/usage/executions?cycle=2026-11-05&cursor=MjAy-_x", "GET"]]);
});

// ---- Contract: the client sees prices only ----

const FORBIDDEN = /token|raw|cost|markup|margin|usd|aws/i;
const CLIENT_FILES = [
  "../lib/credit-api.ts",
  "../lib/credit-view.ts",
  "../app/portal/credit/page.tsx",
  "../components/console/CreditWorkspace.tsx",
  "../components/console/credit/CreditCharts.tsx",
];

test("the Credit tab's files never name a price breakdown the client must not see", async () => {
  for (const file of CLIENT_FILES) {
    const source = await read(file);
    source.split("\n").forEach((line, index) => {
      assert.doesNotMatch(line, FORBIDDEN, `${file}:${index + 1}: ${line.trim()}`);
    });
  }
});

test("the API types mirror the server's client models field for field", async () => {
  const source = await read("../lib/credit-api.ts");
  const fields = (name) => {
    const body = new RegExp(`export interface ${name} \\{([^}]*)\\}`).exec(source)?.[1];
    assert.ok(body, `no interface ${name}`);
    return [...body.matchAll(/^\s*(\w+)\??:/gm)].map((m) => m[1]);
  };
  assert.deepEqual(fields("AgentUsageOut"), ["agent_id", "title", "executions", "price"]);
  assert.deepEqual(fields("DailyUsageOut"), ["day", "price"]);
  assert.deepEqual(fields("UsageOut"), [
    "cycle_start", "cycle_end", "billed_from", "billed_to", "executions", "ai_usage_price",
    "platform_fee", "total", "final", "status", "by_agent", "daily",
  ]);
  assert.deepEqual(fields("ExecutionOut"), ["at", "agent_id", "agent_title", "job_label", "price"]);
  assert.deepEqual(fields("ExecutionsPageOut"), ["items", "next_cursor"]);
  assert.deepEqual(fields("CyclePickOut"), ["start", "end", "total", "final"]);
});

test("the Credit page is served: proxy.ts adopts /portal/credit", async () => {
  const proxy = await read("../proxy.ts");
  assert.ok(proxy.includes('"/portal/credit",'), "proxy.ts must adopt /portal/credit or it answers 404");
});

// The staged release copies only reviewed files, and not this script, so this runs in the checkout.
const releaseScript = new URL("../scripts/check-isolated.mjs", import.meta.url);
test("the release copies and runs the Credit files", { skip: !existsSync(releaseScript) && "staged release" }, async () => {
  const release = await readFile(releaseScript, "utf8");
  for (const file of ["lib/credit-api.ts", "lib/credit-view.ts", "tests/credit-view.test.mjs"]) {
    assert.ok(release.includes(`"${file}",`), `check-isolated.mjs must copy ${file}`);
  }
  assert.ok(release.includes('"../tests/credit-view.test.mjs"]'), "the release must run the Credit tests");
});
