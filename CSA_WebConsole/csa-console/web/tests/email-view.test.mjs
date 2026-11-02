import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Days are the viewer's local days; pin the zone so the expected results hold anywhere.
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
  filterThreads, cacheNote, anyFilterOn, noFilters, presentValues, draftThreadIds, listDate,
} = await import(await load("email-view.ts", {
  '"@/lib/date-range"': JSON.stringify(await load("date-range.ts")),
}));

function thread(id, extra = {}) {
  return {
    thread_id: id, subject: `Subject ${id}`, sender: "A <a@x.test>", snippet: "", received_at: "2026-10-28T02:00:00Z",
    labels: [], unread: false, category: null, priority: null, summary: null, action_items: [],
    analysed_at: null, has_body: true, ...extra,
  };
}
const none = noFilters();

test("filterThreads unread", () => {
  const threads = [thread("a", { unread: true }), thread("b"), thread("c", { unread: true })];
  assert.deepEqual(filterThreads(threads, { ...none, unread: true }, new Set()).map(t => t.thread_id), ["a", "c"]);
  assert.equal(filterThreads(threads, none, new Set()).length, 3, "no filter keeps every thread, in order");
});

test("filterThreads has a draft", () => {
  const threads = [thread("a"), thread("b"), thread("c")];
  const drafts = new Set(["b"]);
  assert.deepEqual(filterThreads(threads, { ...none, hasDraft: true }, drafts).map(t => t.thread_id), ["b"]);
  assert.deepEqual(filterThreads(threads, { ...none, hasDraft: true }, new Set()), []);
});

test("filterThreads date range uses local days", () => {
  // 28 Oct 2026 23:30 in Kuala Lumpur is still the 28th locally but 15:30 UTC; 29 Oct 00:30 local is
  // 28 Oct 16:30 UTC. A UTC reading of the day would put both on the 28th.
  const late = thread("late", { received_at: "2026-10-28T15:30:00Z" });
  const early = thread("early", { received_at: "2026-10-28T16:30:00Z" });
  const missing = thread("missing", { received_at: null });
  const threads = [late, early, missing];
  const on28 = { from: "2026-10-28", to: "2026-10-28" };
  const on29 = { from: "2026-10-29", to: "2026-10-29" };
  assert.deepEqual(filterThreads(threads, { ...none, range: on28 }, new Set()).map(t => t.thread_id), ["late"]);
  assert.deepEqual(filterThreads(threads, { ...none, range: on29 }, new Set()).map(t => t.thread_id), ["early"]);
  const both = { from: "2026-10-28", to: "2026-10-29" };
  assert.deepEqual(filterThreads(threads, { ...none, range: both }, new Set()).map(t => t.thread_id), ["late", "early"]);
  assert.equal(filterThreads(threads, none, new Set()).length, 3, "a thread with no time stays when no days are chosen");
});

test("filterThreads combines filters", () => {
  const threads = [
    thread("a", { unread: true, category: "billing", priority: "high" }),
    thread("b", { unread: true, category: "billing", priority: "normal" }),
    thread("c", { unread: false, category: "billing", priority: "high" }),
    thread("d", { unread: true, category: "complaint", priority: "high" }),
  ];
  const drafts = new Set(["a", "b", "d"]);
  const all = { unread: true, hasDraft: true, category: "billing", priority: "high", range: { from: null, to: null } };
  assert.deepEqual(filterThreads(threads, all, drafts).map(t => t.thread_id), ["a"]);
  assert.deepEqual(filterThreads(threads, { ...none, category: "billing" }, drafts).map(t => t.thread_id), ["a", "b", "c"]);
  assert.deepEqual(filterThreads(threads, { ...none, priority: "high", hasDraft: true }, drafts).map(t => t.thread_id), ["a", "d"]);
  const input = [...threads];
  filterThreads(threads, all, drafts);
  assert.deepEqual(threads, input, "the input list is not changed");
});

test("cacheNote text", () => {
  assert.equal(cacheNote(0), "Showing the last 90 days, up to 200 emails.");
  assert.equal(cacheNote(200), "Showing the last 90 days, up to 200 emails.");
});

test("anyFilterOn, presentValues and draftThreadIds", () => {
  assert.equal(anyFilterOn(none), false);
  assert.equal(anyFilterOn({ ...none, unread: true }), true);
  assert.equal(anyFilterOn({ ...none, hasDraft: true }), true);
  assert.equal(anyFilterOn({ ...none, category: "billing" }), true);
  assert.equal(anyFilterOn({ ...none, priority: "low" }), true);
  assert.equal(anyFilterOn({ ...none, range: { from: "2026-10-01", to: "2026-10-02" } }), true);
  const threads = [thread("a", { category: "billing", priority: "low" }), thread("b", { category: "complaint", priority: "high" }),
    thread("c", { category: "billing", priority: null }), thread("d")];
  assert.deepEqual(presentValues(threads, "category"), ["complaint", "billing"], "categories keep the order of the page's labels");
  assert.deepEqual(presentValues(threads, "priority"), ["high", "low"], "priorities keep their order of importance");
  const ids = draftThreadIds([{ thread_id: "a" }, { thread_id: null }, { thread_id: "a" }, { thread_id: "c" }]);
  assert.deepEqual([...ids].sort(), ["a", "c"]);
});

test("listDate shows the time today, the day this year and the year before that", () => {
  const now = new Date("2026-10-29T06:00:00Z"); // 29 Oct 2026, 14:00 in Kuala Lumpur
  assert.equal(listDate("2026-10-29T01:30:00Z", now), "09:30");
  assert.equal(listDate("2026-10-28T15:30:00Z", now), "28 Oct", "23:30 on the 28th locally is yesterday");
  assert.equal(listDate("2025-12-31T02:00:00Z", now), "31 Dec 2025");
  assert.equal(listDate(null, now), "");
  assert.equal(listDate("not a date", now), "");
});
