import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(name, replacements = {}) {
  let source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(replacements)) source = source.replaceAll(from, to);
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const {
  lastAssistants, outcomeOf, plainSummary, whenText, pushCursor, popCursor, agentOptions,
  runsQuery, titleOf,
} = await import(await load("activity-view.ts", {
  '"@/lib/agent-avatars"': JSON.stringify(await load("agent-avatars.ts")),
}));

let next = 0;
function run(agent, extra = {}) {
  next += 1;
  return {
    id: next, trace_id: `t${next}`, conversation_id: null, agent, ok: true, status: "ok",
    error_code: null, error_category: null, latency_ms: 17,
    created_at: new Date(Date.UTC(2026, 10, 2, 10, next)).toISOString(), ...extra,
  };
}
/** Newest first, as the API sends them. */
const newestFirst = (...runs) => [...runs].reverse();

test("lastAssistants dedupes ticket and escalation under the tickets avatar", () => {
  const rows = newestFirst(
    run("order_agent"), run("ticket_agent"), run("order_approval", { ok: false, status: "error" }),
    run("escalation_agent", { ok: false, status: "error", error_code: "timeout" }),
  );
  const cards = lastAssistants(rows);
  assert.deepEqual(cards.map((card) => card.avatarId), ["tickets", "orders"]);
  // The newest run of each avatar decides the card: escalation (error) over ticket (ok),
  // and order_approval (error) over the older order_agent.
  assert.equal(cards[0].outcome, "needs_attention");
  assert.equal(cards[0].runId, 4);
  assert.equal(cards[0].title, "Support cases & hand-over assistant");
  assert.equal(cards[0].finishedAt, rows[0].created_at);
  assert.equal(cards[1].outcome, "needs_attention");
  assert.equal(cards[1].runId, 3);
});

test("lastAssistants returns fewer than five and never pads", () => {
  assert.deepEqual(lastAssistants([]), []);
  const rows = newestFirst(run("kb_agent"), run("invoice_agent"), run("kb_agent"));
  const cards = lastAssistants(rows);
  assert.deepEqual(cards.map((card) => card.avatarId), ["knowledge", "invoices"]);
  assert.equal(cards[0].outcome, "worked");
  // More than five distinct assistants: exactly five by default, `limit` otherwise.
  const many = newestFirst(
    run("kb_agent"), run("ticket_agent"), run("order_agent"), run("invoice_agent"),
    run("ecommerce_agent"), run("subscription_agent"), run("booking_agent"),
  );
  assert.equal(lastAssistants(many).length, 5);
  assert.equal(lastAssistants(many, 3).length, 3);
  assert.equal(lastAssistants(many, 20).length, 7);
  assert.deepEqual(lastAssistants(many, 0), []);
});

test("lastAssistants skips unknown capabilities", () => {
  const rows = newestFirst(run("kb_agent"), run("mystery_agent"), run("invoice_agent"));
  assert.deepEqual(lastAssistants(rows).map((card) => card.avatarId), ["invoices", "knowledge"]);
  assert.deepEqual(lastAssistants([run("mystery_agent")]), []);
});

test("outcomeOf maps ok and error and calls anything else partial", () => {
  assert.equal(outcomeOf(run("kb_agent")), "worked");
  assert.equal(outcomeOf(run("kb_agent", { ok: false, status: "error" })), "needs_attention");
  assert.equal(outcomeOf(run("kb_agent", { ok: true, status: "partial" })), "partial");
  assert.equal(outcomeOf(run("kb_agent", { ok: false, status: "partial" })), "partial");
  // A partial latest run is carried to the strip as partial, never as worked.
  assert.equal(lastAssistants([run("kb_agent", { status: "partial" })])[0].outcome, "partial");
});

test("plainSummary relative times", () => {
  const at = (minutes, seconds = 0) => new Date(Date.UTC(2026, 10, 2, 12, 0, 0) - (minutes * 60 + seconds) * 1000);
  const now = new Date(Date.UTC(2026, 10, 2, 12, 0, 0));
  const made = (when) => run("customer_support_unknown", { created_at: when.toISOString() });
  const kb = (when) => run("kb_agent", { created_at: when.toISOString() });
  assert.equal(plainSummary(kb(at(0, 20)), now), "Knowledge base agent finished just now");
  assert.equal(plainSummary(kb(at(1)), now), "Knowledge base agent finished 1 minute ago");
  assert.equal(plainSummary(kb(at(3)), now), "Knowledge base agent finished 3 minutes ago");
  assert.equal(plainSummary(kb(at(59, 59)), now), "Knowledge base agent finished 59 minutes ago");
  // A clock a little ahead of the server never shows a negative time.
  assert.equal(plainSummary(kb(new Date(now.getTime() + 5000)), now), "Knowledge base agent finished just now");
  // An hour or more shows the date and the minute, in the reader's own zone.
  const local = new Date(2026, 10, 2, 9, 5);
  const later = new Date(2026, 10, 2, 12, 0);
  assert.equal(plainSummary(kb(local), later), "Knowledge base agent finished on 2 Nov 2026, 09:05");
  assert.equal(whenText(local.toISOString(), later), "on 2 Nov 2026, 09:05");
  assert.equal(whenText(at(2).toISOString(), now), "2 minutes ago");
  // An unknown capability keeps its own name rather than a made-up title.
  assert.equal(plainSummary(made(at(3)), now), "customer_support_unknown finished 3 minutes ago");
  assert.equal(titleOf("escalation_agent"), "Support cases & hand-over assistant");
  assert.equal(titleOf("mystery_agent"), "mystery_agent");
});

test("pageStack push and pop", () => {
  const empty = [];
  const one = pushCursor(empty, 40);
  const two = pushCursor(one, 30);
  assert.deepEqual(empty, []);
  assert.deepEqual(one, [40]);
  assert.deepEqual(two, [40, 30]);
  assert.deepEqual(popCursor(two), [40]);
  assert.deepEqual(popCursor(one), []);
  assert.deepEqual(popCursor([]), []);
  assert.deepEqual(two, [40, 30], "inputs are never mutated");
});

test("agentOptions labels are unique", () => {
  const options = agentOptions();
  assert.equal(options.length, 9);
  assert.equal(new Set(options.map((option) => option.value)).size, 9);
  assert.equal(new Set(options.map((option) => option.label)).size, 9);
  const labels = Object.fromEntries(options.map((option) => [option.value, option.label]));
  assert.equal(labels.escalation_agent, "Support cases & hand-over assistant: escalation");
  assert.equal(labels.ticket_agent, "Support cases & hand-over assistant: tickets");
  assert.equal(labels.order_agent, "Order management assistant: orders");
  assert.equal(labels.order_approval, "Order management assistant: approvals");
  // A title that only one capability has stays plain.
  assert.equal(labels.kb_agent, "Knowledge base agent");
  assert.equal(labels.invoice_agent, "Invoice lookup assistant");
  assert.equal(labels.booking_agent, "Bookings assistant");
  assert.ok(!Object.keys(labels).includes("lhdn_agent"), "nothing runs for lhdn_agent");
});

test("runsQuery sends only what is chosen and never an empty status", () => {
  const base = runsQuery("client_id=abc", { limit: 5 });
  assert.equal(base, "client_id=abc&limit=5");
  assert.equal(runsQuery("", { limit: 100 }), "limit=100");
  const all = runsQuery("", {
    limit: 5, beforeId: 8, agent: "kb_agent", status: "error",
    from: "2026-11-01T16:00:00.000Z", to: "2026-11-03T16:00:00.000Z",
  });
  const params = new URLSearchParams(all);
  assert.equal(params.get("limit"), "5");
  assert.equal(params.get("before_id"), "8");
  assert.equal(params.get("agent"), "kb_agent");
  assert.equal(params.get("status"), "error");
  assert.equal(params.get("from"), "2026-11-01T16:00:00.000Z");
  assert.equal(params.get("to"), "2026-11-03T16:00:00.000Z");
  // "all", empty text and null all leave the parameter out (an empty status is a 422).
  const none = runsQuery("", { limit: 5, beforeId: null, agent: "", status: "all", from: null, to: null });
  assert.equal(none, "limit=5");
  assert.ok(!runsQuery("", { limit: 5, status: "all" }).includes("status"));
  // A tenant query keeps its own parameters first.
  assert.ok(runsQuery("client_id=a%2Fb", { limit: 5 }).startsWith("client_id=a%2Fb&"));
});
