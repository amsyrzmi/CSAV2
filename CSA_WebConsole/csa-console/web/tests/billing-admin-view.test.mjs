import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
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
const creditViewUrl = moduleUrl(await read("../lib/credit-view.ts"));
const client = await import(moduleUrl((await read("../lib/billing-admin-api.ts")).replace('"./api"', JSON.stringify(apiUrl))));
const view = await import(moduleUrl((await read("../lib/billing-admin-view.ts")).replace('"./credit-view"', JSON.stringify(creditViewUrl))));
const { ApiError } = await import(apiUrl);
const {
  warningSentences, planErrorMessage, priceErrorMessage, fxErrorMessage, PLAN_RULE_CODES,
  rawCostText, marginText, costPerExecutionText, workbookNote, tokenText, stageLabel, bundleLabel,
  planStatus, klToday, cycleStartOf, addMonths, cycleOptions, suggestedDates, klDateTime,
  isUsdAmount, planBody, fullDay, initialPlanChoice, planChoiceReady, currentPlanOf,
  overviewErrorMessage, isUsdToMyr, langfuseCheckLabel, langfuseCheckDetail, langfuseCheckErrorMessage,
} = view;

// 13 Nov 2026, 12:00 in Kuala Lumpur.
const NOW = new Date("2026-11-13T04:00:00Z");

function company(overrides = {}) {
  return {
    client_id: "11111111-1111-4111-8111-111111111111",
    name: "Kedai Mawar",
    status: "ok",
    stage: "pilot",
    bundle: "individual",
    executions: 10000,
    prompt_tokens: 1234567,
    completion_tokens: 89012,
    raw_cost_rm: "167.3700",
    ai_charge: "284.52",
    platform_fee: "99.99",
    billed_total: "384.51",
    ai_margin_rm: "117.15",
    cost_per_execution_rm: "0.0167",
    workbook_cost_per_message_rm: "0.0167",
    unpriced_calls: 0,
    unpriced_executions: 0,
    usage_without_plan: 0,
    usage_after_termination: 0,
    terminated_on: null,
    warnings: [],
    ...overrides,
  };
}

// ---- Warnings as plain sentences ----

test("each warning reads as a plain sentence, with counts where they help", () => {
  assert.deepEqual(warningSentences(company({ warnings: ["pilot_ended"] })),
    ["Pilot period has ended: convert to collaborator or terminate"]);
  assert.deepEqual(warningSentences(company({ warnings: ["usage_after_termination"], usage_after_termination: 3 })),
    ["Usage recorded after termination: 3 executions, not billed"]);
  assert.deepEqual(warningSentences(company({ warnings: ["usage_after_termination"], usage_after_termination: 1 })),
    ["Usage recorded after termination: 1 execution, not billed"]);
  assert.deepEqual(warningSentences(company({ warnings: ["not_set_up_with_usage"], usage_without_plan: 1200 })),
    ["Has usage but no billing plan yet: 1,200 executions, not billed"]);
  assert.deepEqual(warningSentences(company({ warnings: ["usage_before_plan"], usage_without_plan: 2 })),
    ["Usage before the join date: 2 executions, not billed"]);
  assert.deepEqual(warningSentences(company({ warnings: ["usage_before_plan"], usage_without_plan: 1 })),
    ["Usage before the join date: 1 execution, not billed"]);
  assert.deepEqual(warningSentences(company({ warnings: ["pending_prices"], unpriced_calls: 4, unpriced_executions: 2 })),
    ["Some usage has no price yet: 4 calls in 2 executions"]);
  assert.deepEqual(warningSentences(company({ warnings: ["pending_prices"], unpriced_calls: 1, unpriced_executions: 1 })),
    ["Some usage has no price yet: 1 call in 1 execution"]);
  assert.deepEqual(warningSentences(company()), []);
  // Several warnings keep the server's order.
  assert.equal(warningSentences(company({ warnings: ["pilot_ended", "pending_prices"], unpriced_calls: 2, unpriced_executions: 2 })).length, 2);
});

test("every warning code the server raises has its own plain sentence", () => {
  const generic = warningSentences(company({ warnings: ["something_new"] }))[0];
  const codes = ["pilot_ended", "usage_after_termination", "not_set_up_with_usage", "usage_before_plan", "pending_prices"];
  for (const code of codes) {
    const [sentence] = warningSentences(company({ warnings: [code], usage_without_plan: 3, usage_after_termination: 3, unpriced_calls: 3, unpriced_executions: 3 }));
    assert.notEqual(sentence, generic, code);
    assert.doesNotMatch(sentence, /_|null|undefined|NaN/, `${code}: ${sentence}`);
  }
});

test("an unknown warning still reads as a sentence, never a raw code", () => {
  const [sentence] = warningSentences(company({ warnings: ["something_new"] }));
  assert.ok(sentence && !sentence.includes("_"), sentence);
});

// ---- The Langfuse check ----

function check(overrides = {}) {
  return {
    client_id: company().client_id,
    cycle_start: "2026-11-05",
    cycle_end: "2026-12-05",
    checked_until: "2026-11-13T03:55:00Z",
    result: "match",
    ledger_input_tokens: 3234567,
    ledger_output_tokens: 7655,
    langfuse_input_tokens: 3234500,
    langfuse_output_tokens: 7655,
    warnings: [],
    ...overrides,
  };
}

test("a Langfuse drift adds a plain warning after the company's own", () => {
  const drifted = check({ result: "drift", warnings: ["langfuse_drift"] });
  const sentences = warningSentences(company({ warnings: ["pilot_ended"] }), drifted);
  assert.equal(sentences.length, 2);
  assert.match(sentences[0], /Pilot period/);
  assert.match(sentences[1], /Langfuse/);
  assert.match(sentences[1], /bill uses our records/);
  assert.doesNotMatch(sentences[1], /_/);
  assert.deepEqual(warningSentences(company(), check()), []);
  assert.deepEqual(warningSentences(company(), null), []);
});

test("the Langfuse check reads as match, does not match, or not reachable", () => {
  assert.equal(langfuseCheckLabel(check()), "Matches Langfuse");
  assert.equal(langfuseCheckLabel(check({ result: "drift" })), "Does not match Langfuse");
  assert.equal(langfuseCheckLabel(check({ result: "unavailable" })), "Langfuse not reachable");
});

test("the Langfuse check detail shows both sides' tokens on drift, and never changes the bill", () => {
  const drifted = langfuseCheckDetail(check({ result: "drift", langfuse_input_tokens: 1234567, langfuse_output_tokens: 7000 }), true);
  assert.match(drifted, /Our records: 3,234,567 tokens in, 7,655 out/);
  assert.match(drifted, /Langfuse: 1,234,567 in, 7,000 out/);
  assert.match(drifted, /bill always uses our records/);
  assert.doesNotMatch(drifted, /few minutes/);
  const open = langfuseCheckDetail(check(), false);
  assert.match(open, /within 1%/);
  assert.match(open, /last few minutes/);
  const down = langfuseCheckDetail(check({ result: "unavailable", langfuse_input_tokens: null, langfuse_output_tokens: null }), true);
  assert.match(down, /Nothing was compared/);
  assert.match(down, /bill is not affected/);
  for (const text of [drifted, open, down]) assert.doesNotMatch(text, /_|null|undefined|NaN/);
});

// ---- Error codes as plain English ----

const JARGON = /_|\b[45]\d\d\b|detail|shadow|exception|null|undefined|pydantic|PlanRule/i;
const err = (status, code) => new ApiError(status, code, code);
// The service's sources are outside the staged release, which runs this file on its own.
const plansPy = new URL("../../../../csa/billing/plans.py", import.meta.url);
const adminPy = new URL("../../../../csa/console/billing_admin.py", import.meta.url);
const noService = (url) => !existsSync(url) && "staged release";

test("every plan rule code in csa/billing/plans.py is listed and explained", { skip: noService(plansPy) }, async () => {
  const source = await readFile(plansPy, "utf8");
  const literal = /PlanRuleCode = Literal\[([^\]]*)\]/.exec(source)?.[1];
  assert.ok(literal, "PlanRuleCode not found in plans.py");
  const codes = [...literal.matchAll(/"(\w+)"/g)].map((m) => m[1]).sort();
  assert.deepEqual([...PLAN_RULE_CODES].sort(), codes);
  const seen = new Set();
  for (const code of codes) {
    const message = planErrorMessage(err(422, code));
    assert.ok(message.length > 20, code);
    assert.doesNotMatch(message, JARGON, `${code}: ${message}`);
    assert.ok(!seen.has(message), `${code} shares its sentence`);
    seen.add(message);
  }
});

test("plan errors say what to do", () => {
  assert.equal(planErrorMessage(err(422, "change_not_on_a_fifth")), "Plan changes take effect on the 5th. Pick a 5th.");
  assert.match(planErrorMessage(err(422, "before_join_date")), /join date/);
  assert.match(planErrorMessage(err(422, "after_termination")), /final/);
  for (const [status, code] of [[404, "tenant not found"], [403, "platform_admin_only"], [403, "csrf_required"],
    [401, "Request failed (401)"], [422, "Request failed (422)"], [500, "Something went wrong on our side. Try again."],
    [418, "brand_new_code"]]) {
    const message = planErrorMessage(err(status, code));
    assert.ok(message.length > 20, code);
    assert.doesNotMatch(message, JARGON, `${code}: ${message}`);
  }
  assert.match(planErrorMessage(err(422, "Request failed (422)")), /reason/);
  assert.match(planErrorMessage(new TypeError("Failed to fetch")), /reach/);
});

test("price and FX errors explain the date rule, the provider and duplicates", () => {
  assert.equal(priceErrorMessage(err(422, "shadow_rule")),
    "A price can only be back-dated before the earliest existing price for that model, to fill unpriced calls; otherwise it must start today or tomorrow. Pick another start.");
  assert.match(priceErrorMessage(err(422, "unknown_provider")), /Groq or Gemini/);
  assert.match(priceErrorMessage(err(409, "price_exists")), /already/);
  assert.match(fxErrorMessage(err(422, "shadow_rule")), /today or tomorrow/);
  assert.match(fxErrorMessage(err(409, "fx_rate_exists")), /already/);
  for (const fn of [priceErrorMessage, fxErrorMessage]) {
    for (const [status, code] of [[422, "shadow_rule"], [422, "unknown_provider"], [409, "price_exists"],
      [409, "fx_rate_exists"], [422, "Request failed (422)"], [403, "platform_admin_only"], [403, "csrf_required"],
      [401, "Request failed (401)"], [500, "x"], [400, "odd_code"]]) {
      const message = fn(err(status, code));
      assert.ok(message.length > 20, `${fn.name} ${code}`);
      assert.doesNotMatch(message, JARGON, `${fn.name} ${code}: ${message}`);
    }
  }
  assert.match(priceErrorMessage(err(422, "Request failed (422)")), /8 decimal places/);
  assert.match(fxErrorMessage(err(422, "Request failed (422)")), /8 decimal places/);
});

// ---- Exact money and counts ----

test("raw cost, margin and cost per execution are shown exactly as the API sends them", () => {
  assert.equal(rawCostText(company()), "RM 167.3700");
  assert.equal(rawCostText(company({ raw_cost_rm: "12345.6789" })), "RM 12,345.6789");
  assert.equal(rawCostText(company({ raw_cost_rm: "0.0000" })), "RM 0.0000");
  assert.equal(marginText(company()), "RM 117.15");
  assert.equal(marginText(company({ ai_margin_rm: "0.00" })), "RM 0.00");
  assert.equal(marginText(company({ ai_margin_rm: "-1234.56" })), "-RM 1,234.56");
  assert.equal(costPerExecutionText(company({ cost_per_execution_rm: "0.0123" })), "RM 0.0123");
  assert.equal(costPerExecutionText(company({ cost_per_execution_rm: "0.0000" })), "RM 0.0000");
  assert.equal(costPerExecutionText(company({ executions: 0, cost_per_execution_rm: null })), "No priced executions");
});

test("the workbook note compares exactly, in words, never by colour", () => {
  assert.equal(workbookNote(company({ cost_per_execution_rm: "0.0123" })), "Less than the workbook's RM 0.0167");
  assert.equal(workbookNote(company({ cost_per_execution_rm: "0.0168" })), "More than the workbook's RM 0.0167");
  assert.equal(workbookNote(company({ cost_per_execution_rm: "0.0167" })), "Same as the workbook's RM 0.0167");
  assert.equal(workbookNote(company({ cost_per_execution_rm: "1.0000" })), "More than the workbook's RM 0.0167");
  assert.equal(workbookNote(company({ cost_per_execution_rm: null })), "Workbook assumes RM 0.0167");
});

test("tokens are whole counts with thousands grouped", () => {
  assert.equal(tokenText(0), "0");
  assert.equal(tokenText(1234567), "1,234,567");
  assert.equal(tokenText(999), "999");
});

// ---- Labels ----

test("stage, bundle and plan status labels; a terminated company shows its date", () => {
  assert.equal(stageLabel("pilot"), "Pilot");
  assert.equal(stageLabel("collaborator"), "Collaborator");
  assert.equal(stageLabel("terminated"), "Terminated");
  assert.equal(stageLabel(null), "No plan");
  assert.equal(bundleLabel("individual"), "Individual");
  assert.equal(bundleLabel("curated"), "Curated");
  assert.equal(bundleLabel("complete"), "Complete");
  assert.equal(bundleLabel(null), "—");
  assert.equal(planStatus(company(), NOW), "Pilot, Individual");
  assert.equal(planStatus(company({ stage: null, bundle: null, status: "not_set_up" }), NOW), "Not set up");
  assert.equal(planStatus(company({ terminated_on: "2026-11-10", stage: "collaborator", bundle: "complete" }), NOW),
    "Terminated on 10 Nov 2026");
  assert.equal(planStatus(company({ terminated_on: "2026-11-13" }), NOW), "Terminated on 13 Nov 2026");
  assert.equal(planStatus(company({ terminated_on: "2026-12-01" }), NOW), "Pilot, Individual; terminates on 1 Dec 2026");
  assert.equal(fullDay("2027-01-05"), "5 Jan 2027");
  assert.equal(fullDay("nope"), "—");
});

// ---- Kuala Lumpur days and the 5ths ----

test("today is the Kuala Lumpur date: 17:00 UTC on the 4th is already the 5th", () => {
  assert.equal(klToday(new Date("2026-12-04T17:00:00Z")), "2026-12-05");
  assert.equal(klToday(new Date("2026-12-04T15:59:59Z")), "2026-12-04");
  assert.equal(klToday(new Date("2026-12-31T16:00:00Z")), "2027-01-01");
});

test("cycle starts and month steps use whole numbers only, across the year boundary", () => {
  assert.equal(cycleStartOf("2026-11-13"), "2026-11-05");
  assert.equal(cycleStartOf("2026-11-05"), "2026-11-05");
  assert.equal(cycleStartOf("2026-11-04"), "2026-10-05");
  assert.equal(cycleStartOf("2027-01-04"), "2026-12-05");
  assert.equal(cycleStartOf("2027-01-01"), "2026-12-05");
  assert.equal(addMonths("2026-12-05", 1), "2027-01-05");
  assert.equal(addMonths("2026-11-05", 3), "2027-02-05");
  assert.equal(addMonths("2027-01-05", -1), "2026-12-05");
  assert.equal(addMonths("2026-01-05", -13), "2024-12-05");
});

test("the plan form suggests the current cycle start and the next 5ths, in Kuala Lumpur time", () => {
  assert.deepEqual(suggestedDates(new Date("2026-12-04T17:00:00Z"), "pilot").map((s) => s.date),
    ["2026-12-05", "2027-01-05", "2027-02-05", "2027-03-05"]);
  const before = suggestedDates(new Date("2026-12-04T15:59:00Z"), "collaborator");
  assert.deepEqual(before.map((s) => s.date), ["2026-11-05", "2026-12-05", "2027-01-05", "2027-02-05"]);
  assert.equal(before[0].label, "5 Nov 2026 (current cycle start)");
  assert.equal(before[1].label, "5 Dec 2026");
  // A termination may be any day, so today comes first when it is not the cycle start.
  assert.deepEqual(suggestedDates(NOW, "terminated").map((s) => s.date),
    ["2026-11-13", "2026-11-05", "2026-12-05", "2027-01-05", "2027-02-05"]);
  assert.equal(suggestedDates(NOW, "terminated")[0].label, "13 Nov 2026 (today)");
  assert.deepEqual(suggestedDates(new Date("2026-11-04T16:00:00Z"), "terminated").map((s) => s.date),
    ["2026-11-05", "2026-12-05", "2027-01-05", "2027-02-05"]);
});

test("the cycle picker lists the current cycle and earlier ones, newest first", () => {
  const options = cycleOptions(new Date("2027-01-04T17:00:00Z"), 3);
  assert.deepEqual(options.map((o) => [o.start, o.end, o.open]), [
    ["2027-01-05", "2027-02-05", true],
    ["2026-12-05", "2027-01-05", false],
    ["2026-11-05", "2026-12-05", false],
  ]);
  assert.match(options[0].label, /\(open\)$/);
  assert.equal(cycleOptions(NOW).length, 12);
});

test("a price or rate start is a Kuala Lumpur date and time with its offset", () => {
  assert.equal(klDateTime("2026-11-13", "09:30"), "2026-11-13T09:30:00+08:00");
  assert.equal(klDateTime("2026-11-13", ""), null);
  assert.equal(klDateTime("", "09:30"), null);
  assert.equal(klDateTime("13/11/2026", "09:30"), null);
  assert.equal(klDateTime("2026-11-13", "25:00"), null);
});

test("USD amounts are positive decimals with at most 8 places", () => {
  for (const ok of ["0.59", "1", "0.00000001", "99999.99999999"]) assert.ok(isUsdAmount(ok), ok);
  for (const bad of ["0", "0.0", "-1", "1e3", "0.000000001", "100000", "", " 1", "1,000"]) assert.ok(!isUsdAmount(bad), bad);
});

test("a termination leaves the bundle out; any other plan sends it", () => {
  assert.deepEqual(planBody({ stage: "terminated", bundle: "curated", effective_from: "2026-11-13", reason: " Left " }),
    { stage: "terminated", effective_from: "2026-11-13", reason: "Left" });
  assert.deepEqual(planBody({ stage: "collaborator", bundle: "curated", effective_from: "2026-12-05", reason: "Converted" }),
    { stage: "collaborator", bundle: "curated", effective_from: "2026-12-05", reason: "Converted" });
});

// ---- The plan form's starting point and its submit rule ----

test("a known plan starts the form on that plan; a company not set up starts on Pilot, Individual", () => {
  assert.deepEqual(initialPlanChoice(company({ stage: "collaborator", bundle: "complete" })),
    { stage: "collaborator", bundle: "complete" });
  assert.deepEqual(initialPlanChoice(company({ stage: "pilot", bundle: "curated" })), { stage: "pilot", bundle: "curated" });
  assert.deepEqual(initialPlanChoice(company({ stage: null, bundle: null })), { stage: "pilot", bundle: "individual" });
});

test("an unknown plan starts the form on nothing chosen, never on Pilot, Individual", () => {
  assert.deepEqual(initialPlanChoice(null), { stage: "", bundle: "" });
  assert.equal(planChoiceReady(initialPlanChoice(null)), false);
});

test("the plan can be submitted only once a stage, and a bundle unless terminating, are chosen", () => {
  assert.equal(planChoiceReady({ stage: "", bundle: "" }), false);
  assert.equal(planChoiceReady({ stage: "pilot", bundle: "" }), false);
  assert.equal(planChoiceReady({ stage: "", bundle: "curated" }), false);
  assert.equal(planChoiceReady({ stage: "collaborator", bundle: "curated" }), true);
  // A termination keeps the bundle in force, so the form does not ask for one.
  assert.equal(planChoiceReady({ stage: "terminated", bundle: "" }), true);
  assert.equal(planBody({ stage: "pilot", bundle: "", effective_from: "2026-12-05", reason: "x" }), null);
  assert.equal(planBody({ stage: "", bundle: "curated", effective_from: "2026-12-05", reason: "x" }), null);
  assert.deepEqual(planBody({ stage: "terminated", bundle: "", effective_from: "2026-11-13", reason: "Left" }),
    { stage: "terminated", effective_from: "2026-11-13", reason: "Left" });
});

test("the Tenants page finds a tenant's plan in the overview, or knows it does not know it", () => {
  const overview = { cycle_start: "2026-11-05", cycle_end: "2026-12-05", final: false,
    companies: [company({ stage: "collaborator", bundle: "curated", terminated_on: "2026-12-01" })] };
  assert.deepEqual(currentPlanOf(overview, company().client_id),
    { stage: "collaborator", bundle: "curated", terminated_on: "2026-12-01" });
  assert.equal(currentPlanOf(overview, "22222222-2222-4222-8222-222222222222"), null);
  assert.equal(currentPlanOf(null, company().client_id), null);
});

// ---- Overview errors and the FX cap ----

test("overview errors read as plain English, never a code", () => {
  assert.match(overviewErrorMessage(err(422, "cycle_must_start_on_a_fifth")), /cycle/i);
  assert.match(overviewErrorMessage(err(404, "cycle_not_found")), /not started/);
  assert.match(overviewErrorMessage(err(403, "platform_admin_only")), /platform/);
  assert.match(overviewErrorMessage(new TypeError("Failed to fetch")), /reach/);
  for (const [status, code] of [[422, "cycle_must_start_on_a_fifth"], [404, "cycle_not_found"], [403, "platform_admin_only"],
    [401, "Request failed (401)"], [404, "Request failed (404)"], [422, "Request failed (422)"], [400, "odd_code"],
    [500, "Something went wrong on our side. Try again."], [503, "x"]]) {
    const message = overviewErrorMessage(err(status, code));
    assert.ok(message.length > 20, code);
    assert.doesNotMatch(message, JARGON, `${code}: ${message}`);
  }
  assert.doesNotMatch(overviewErrorMessage(new Error("cycle_not_found")), JARGON);
});

test("Langfuse check errors read as plain English, never a code", () => {
  assert.match(langfuseCheckErrorMessage(new TypeError("Failed to fetch")), /reach/);
  assert.match(langfuseCheckErrorMessage(err(403, "platform_admin_only")), /platform/);
  assert.match(langfuseCheckErrorMessage(err(404, "tenant not found")), /no longer exists/);
  assert.match(langfuseCheckErrorMessage(err(404, "cycle_not_found")), /not started/);
  for (const [status, code] of [[401, "Request failed (401)"], [403, "platform_admin_only"], [404, "tenant not found"],
    [422, "cycle_must_start_on_a_fifth"], [500, "x"], [400, "odd_code"]]) {
    const message = langfuseCheckErrorMessage(err(status, code));
    assert.ok(message.length > 20, code);
    assert.doesNotMatch(message, JARGON, `${code}: ${message}`);
  }
});

test("a USD to MYR rate is a USD-shaped amount below 1,000, compared exactly", () => {
  for (const ok of ["4.4", "4.47123456", "0.00000001", "999.99999999", "0999.5"]) assert.ok(isUsdToMyr(ok), ok);
  for (const bad of ["1000", "1000.0", "1000.00000001", "01000", "99999", "0", "0.000", "-4.4", "4.471234567", "", "4,4", "1e2"]) {
    assert.ok(!isUsdToMyr(bad), bad);
  }
  assert.match(fxErrorMessage(err(422, "Request failed (422)")), /1,000/);
});

test("a Pilot after Collaborator refusal fits both causes and does not say to choose Collaborator", () => {
  const message = planErrorMessage(err(422, "bundle_change_same_stage_only"));
  assert.match(message, /Pilot plan can never come after a Collaborator plan/);
  assert.match(message, /already scheduled/);
  assert.doesNotMatch(message, /choose Collaborator/i);
});

// ---- The API client: paths, methods and CSRF ----

async function capture(run, reply = () => Response.json({}, { status: 201 })) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return reply(path, init);
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}

test("the overview is a GET of the admin route, with the cycle when one is chosen", async () => {
  const body = { cycle_start: "2026-11-05", cycle_end: "2026-12-05", final: false, companies: [] };
  const [, plain] = await capture(() => client.fetchOverview(), () => Response.json(body));
  assert.deepEqual(plain.map(([p, i]) => [p, i?.method ?? "GET"]), [["/console/v1/billing/admin/overview", "GET"]]);
  const [, picked] = await capture(() => client.fetchOverview("2026-10-05"), () => Response.json(body));
  assert.deepEqual(picked.map(([p]) => p), ["/console/v1/billing/admin/overview?cycle=2026-10-05"]);
});

test("the Langfuse check is a GET for one company and the cycle shown", async () => {
  const [, requests] = await capture(() => client.fetchLangfuseCheck("a b/1", "2026-10-05"), () => Response.json(check()));
  assert.deepEqual(requests.map(([p, i]) => [p, i?.method ?? "GET"]), [
    ["/console/v1/billing/admin/clients/a%20b%2F1/langfuse-check?cycle=2026-10-05", "GET"],
  ]);
});

test("the three writes POST JSON with the CSRF token", async () => {
  const [, requests] = await capture(async () => {
    await client.postPlan("a b/1", { stage: "pilot", bundle: "individual", effective_from: "2026-11-13", reason: "Joined" });
    await client.postPrice({ provider: "groq", model: "m", input_usd_per_mtok: "0.59", output_usd_per_mtok: "0.79", effective_from: "2026-11-13T00:00:00+08:00" });
    await client.postFx({ usd_to_myr: "4.4", effective_from: "2026-11-13T00:00:00+08:00" });
  });
  assert.deepEqual(requests.map(([p]) => p), [
    "/console/v1/billing/admin/clients/a%20b%2F1/plan",
    "/console/v1/billing/admin/prices",
    "/console/v1/billing/admin/fx",
  ]);
  for (const [, init] of requests) {
    assert.equal(init.method, "POST");
    assert.equal(new Headers(init.headers).get("X-CSRF-Token"), "synthetic-csrf");
    assert.equal(new Headers(init.headers).get("Content-Type"), "application/json");
  }
  // Amounts travel as the exact strings typed, never as floats.
  assert.equal(JSON.parse(requests[1][1].body).input_usd_per_mtok, "0.59");
});

test("a refusal reaches the caller with its code", async () => {
  await assert.rejects(
    capture(() => client.postPlan("c", { stage: "pilot", bundle: "individual", effective_from: "2026-11-14", reason: "x" }),
      () => Response.json({ detail: "change_not_on_a_fifth" }, { status: 422 })),
    (cause) => cause instanceof ApiError && cause.status === 422 && cause.code === "change_not_on_a_fifth",
  );
});

test("the API types mirror the admin models field for field", { skip: noService(adminPy) }, async () => {
  const source = await read("../lib/billing-admin-api.ts");
  const fields = (name) => {
    const body = new RegExp(`export interface ${name} \\{([^}]*)\\}`).exec(source)?.[1];
    assert.ok(body, `no interface ${name}`);
    return [...body.matchAll(/^\s*(\w+)\??:/gm)].map((m) => m[1]);
  };
  const server = await readFile(adminPy, "utf8");
  const model = (name) => {
    const body = new RegExp(`class ${name}\\(BaseModel\\):([\\s\\S]*?)(?=\\n\\S)`).exec(server)?.[1];
    assert.ok(body, `no model ${name}`);
    return [...body.matchAll(/^ {4}(\w+): /gm)].map((m) => m[1]);
  };
  for (const name of ["AdminCompanyOut", "AdminOverviewOut", "BillingPlanIn", "BillingPlanOut",
    "BillingPriceIn", "BillingPriceOut", "BillingFxIn", "BillingFxOut", "LangfuseCheckOut"]) {
    assert.deepEqual(fields(name), model(name), name);
  }
});

// ---- Contract: the admin page stays out of the client's Credit tab ----

const CLIENT_CREDIT_FILES = [
  "../lib/credit-api.ts",
  "../lib/credit-view.ts",
  "../app/portal/credit/page.tsx",
  "../components/console/CreditWorkspace.tsx",
  ...(await readdir(new URL("../components/console/credit/", import.meta.url))).map((f) => `../components/console/credit/${f}`),
];

test("no client Credit file imports the platform-admin billing files", async () => {
  for (const file of CLIENT_CREDIT_FILES) {
    const source = await read(file);
    assert.doesNotMatch(source, /billing-admin|components\/console\/billing|BillingAdmin/, file);
  }
});

test("the Billing page is served: proxy.ts adopts /billing", async () => {
  const proxy = await read("../proxy.ts");
  assert.ok(proxy.includes('"/billing",'), "proxy.ts must adopt /billing or it answers 404");
});

const releaseScript = new URL("../scripts/check-isolated.mjs", import.meta.url);
test("the release copies and runs the Billing files", { skip: !existsSync(releaseScript) && "staged release" }, async () => {
  const release = await readFile(releaseScript, "utf8");
  for (const file of ["lib/billing-admin-api.ts", "lib/billing-admin-view.ts", "tests/billing-admin-view.test.mjs"]) {
    assert.ok(release.includes(`"${file}",`), `check-isolated.mjs must copy ${file}`);
  }
  assert.ok(release.includes('"../tests/billing-admin-view.test.mjs"]'), "the release must run the Billing tests");
});
