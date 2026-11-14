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
const apiModule = await import(apiUrl);
const creditApiUrl = moduleUrl((await read("../lib/credit-api.ts")).replace('"./api"', JSON.stringify(apiUrl)));
const view = await import(moduleUrl(await read("../lib/credit-view.ts")));
const client = await import(creditApiUrl);
const {
  formatRm, cycleLabel, banner, proratedNote, stateMessage, dailySeries, agentSeries, priceTicks,
  cycleOption, newestFirst, peakRow, countText, terminationPlace, terminationNotice,
} = view;

// 12 Nov 2026 in Kuala Lumpur: the current year is 2026.
const NOW = new Date("2026-11-12T04:00:00Z");

function usage(overrides = {}) {
  return {
    cycle_start: "2026-11-05", cycle_end: "2026-12-05",
    billed_from: "2026-11-05", billed_to: "2026-12-05",
    executions: 3, ai_usage_price: "0.06", platform_fee: "99.99", total: "100.05",
    final: false, status: "ok", terminated_on: null,
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

test("terminationPlace compares date-only strings: start day inside, end day after", () => {
  const cycle = { cycle_start: "2026-11-05", cycle_end: "2026-12-05" };
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: null })), null);
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "2026-11-05" })), "inside");
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "2026-11-20" })), "inside");
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "2026-12-04" })), "inside");
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "2026-12-05" })), null);
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "2027-01-10" })), null);
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "2026-11-04" })), "before");
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "2025-12-31" })), "before");
  assert.equal(terminationPlace(usage({ ...cycle, terminated_on: "not a day" })), null);
});

test("terminationNotice names the end day with its year, or says the plan has ended", () => {
  const cycle = { cycle_start: "2026-11-05", cycle_end: "2026-12-05" };
  assert.equal(terminationNotice(usage({ ...cycle, terminated_on: "2026-11-12" })),
    "Your plan ended on 12 Nov 2026. The figures below are the final AI Usage Charges and bill for your last billing period.");
  assert.equal(terminationNotice(usage({ ...cycle, terminated_on: "2026-11-05" })),
    "Your plan ended on 5 Nov 2026. The figures below are the final AI Usage Charges and bill for your last billing period.");
  assert.equal(terminationNotice(usage({ ...cycle, terminated_on: "2026-10-20" })), "Your plan has ended.");
  assert.equal(terminationNotice(usage({ ...cycle, terminated_on: null })), null);
  assert.equal(terminationNotice(usage({ ...cycle, terminated_on: "2026-12-05" })), null);
  // A KL day is written as sent, whatever the browser's zone: the last day of a year stays put.
  assert.equal(terminationNotice(usage({ cycle_start: "2026-12-05", cycle_end: "2027-01-05", terminated_on: "2026-12-31" })),
    "Your plan ended on 31 Dec 2026. The figures below are the final AI Usage Charges and bill for your last billing period.");
  for (const text of [terminationNotice(usage({ ...cycle, terminated_on: "2026-11-12" })), "Your plan has ended."]) {
    assert.doesNotMatch(text, FORBIDDEN, text);
    assert.doesNotMatch(text, UNDISCLOSED, text);
  }
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

// ---- The API client ----

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

// Owner decision 2026-11-13: the client sees the bill, the charges per AI agent and per day and
// the execution count, never a list of single executions.
test("there is no per-execution list: no fetch, no types, no list helpers, no list on the page", async () => {
  assert.equal(client.fetchExecutions, undefined);
  assert.equal(view.executionTime, undefined);
  assert.equal(view.executionPrice, undefined);
  const api = await read("../lib/credit-api.ts");
  assert.doesNotMatch(api, /\/executions|ExecutionOut|ExecutionsPageOut|cursor/);
  const page = await read("../components/console/CreditWorkspace.tsx");
  assert.doesNotMatch(page, /fetchExecutions|ExecutionList|credit-executions|Load more|cursor/);
  // The list's error sentences went with it.
  for (const code of ["invalid_cursor", "cursor_from_another_cycle"]) {
    assert.doesNotMatch(apiModule.explainedMessage(code) ?? "", /execution/i, code);
  }
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
    "platform_fee", "total", "final", "status", "by_agent", "daily", "terminated_on",
  ]);
  assert.deepEqual(fields("CyclePickOut"), ["start", "end", "total", "final"]);
});

// The bill line is "AI Usage Charges" everywhere the client reads it (owner decision 2026-11-13).
const CHARGES_STRINGS = [
  ["../components/console/CreditWorkspace.tsx", "Your company's AI Usage Charges and bill for each billing cycle."],
  ["../components/console/CreditWorkspace.tsx", 'label: "AI Usage Charges"'],
  ["../components/console/CreditWorkspace.tsx", "It is added to the AI Usage Charges once it is priced."],
  ["../components/console/CreditWorkspace.tsx", "Your AI Usage Charges and bill will show here once your plan is set up."],
  ["../components/console/CreditWorkspace.tsx", "AI Usage Charges by AI agent"],
  ["../components/console/CreditWorkspace.tsx",
    "No AI Usage Charges in this cycle yet. Each AI agent your company uses will appear here with its charges."],
  ["../components/console/CreditWorkspace.tsx", '<th scope="col" className={amountHead}>AI Usage Charges</th>'],
  ["../components/console/credit/CreditCharts.tsx", '"AI Usage Charges per day"'],
  ["../components/console/credit/CreditCharts.tsx", '"AI Usage Charges by AI agent"'],
  ["../components/console/credit/CreditCharts.tsx", '<th scope="col">AI Usage Charges</th>'],
  ["../components/console/credit/CreditCharts.tsx", "No AI Usage Charges on any day of this cycle yet."],
  ["../components/console/credit/CreditCharts.tsx", "No AI Usage Charges for any AI agent in this cycle yet."],
  ["../components/console/credit/CreditCharts.tsx", "The exact charges are in the table that follows."],
  ["../lib/api.ts", "Only your company's Admin can see its AI Usage Charges and bill."],
];
// Nothing the client reads may name a model, a provider or what the charge is made of.
const UNDISCLOSED = /\bmodels?\b|provider|groq|gemini|langfuse|token|markup|margin|aws|usd/i;

test("the bill line is AI Usage Charges in every client string, never the old AI usage price", async () => {
  for (const [file, text] of CHARGES_STRINGS) {
    assert.ok((await read(file)).includes(text), `${file} must say: ${text}`);
    assert.doesNotMatch(text, UNDISCLOSED, text);
  }
  for (const file of CLIENT_FILES) {
    const source = await read(file);
    assert.doesNotMatch(source, /AI usage price/i, file);
    source.split("\n").forEach((line, index) => {
      assert.doesNotMatch(line, UNDISCLOSED, `${file}:${index + 1}: ${line.trim()}`);
    });
  }
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
