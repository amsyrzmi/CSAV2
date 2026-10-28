import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const inbox = await import(moduleUrl(
  await readFile(new URL("../lib/inbox-api.ts", import.meta.url), "utf8"),
));
const { inboxListQuery, approvalsPath, canConnectWhatsApp, mergeById, listTime } = inbox;

const BASE = "/console/v1/inbox/conversations";
const none = { tenantQuery: "", channel: "", needsReview: false, sender: "", q: "" };

test("with nothing set the list path has no query and no trailing separator", () => {
  assert.equal(inboxListQuery(none), BASE);
});

test("the tenant query comes first and only set filters follow", () => {
  assert.equal(inboxListQuery({ ...none, tenantQuery: "client_id=abc" }), `${BASE}?client_id=abc`);
  assert.equal(inboxListQuery({ ...none, channel: "whatsapp" }), `${BASE}?channel=whatsapp`);
  assert.equal(inboxListQuery({ ...none, needsReview: true }), `${BASE}?needs_review=true`);
  assert.equal(inboxListQuery({ ...none, sender: "new" }), `${BASE}?sender=new`);
  assert.equal(
    inboxListQuery({ tenantQuery: "client_id=abc", channel: "telegram", needsReview: true, sender: "known", q: "ali" }),
    `${BASE}?client_id=abc&channel=telegram&needs_review=true&sender=known&q=ali`,
  );
  assert.equal(
    inboxListQuery({ ...none, channel: "telegram", sender: "known" }),
    `${BASE}?channel=telegram&sender=known`,
  );
});

test("the search text is trimmed and URL-encoded", () => {
  assert.equal(inboxListQuery({ ...none, q: "  ali  " }), `${BASE}?q=ali`);
  assert.equal(inboxListQuery({ ...none, q: "50% off" }), `${BASE}?q=50%25%20off`);
  assert.equal(inboxListQuery({ ...none, q: "@aisyah" }), `${BASE}?q=%40aisyah`);
  assert.equal(inboxListQuery({ ...none, q: "a&b=c" }), `${BASE}?q=a%26b%3Dc`);
  assert.equal(inboxListQuery({ ...none, q: "Zoë 你好" }), `${BASE}?q=${encodeURIComponent("Zoë 你好")}`);
});

test("a search shorter than two characters is dropped", () => {
  assert.equal(inboxListQuery({ ...none, q: "a" }), BASE);
  assert.equal(inboxListQuery({ ...none, q: "  a  " }), BASE);
  assert.equal(inboxListQuery({ ...none, q: " " }), BASE);
  assert.equal(inboxListQuery({ ...none, q: "ab" }), `${BASE}?q=ab`);
  assert.equal(inboxListQuery({ ...none, tenantQuery: "client_id=abc", q: "a" }), `${BASE}?client_id=abc`);
});

test("the approvals path carries the conversation and the handled flag", () => {
  const id = "3f2c1a40-0000-4000-8000-000000000001";
  assert.equal(
    approvalsPath({ tenantQuery: "", conversationId: id, includeHandled: false }),
    `/console/v1/inbox/approvals?conversation_id=${id}&include_handled=false`,
  );
  assert.equal(
    approvalsPath({ tenantQuery: "client_id=abc", conversationId: id, includeHandled: true }),
    `/console/v1/inbox/approvals?client_id=abc&conversation_id=${id}&include_handled=true`,
  );
});

test("only platform and tenant administrators can connect WhatsApp", () => {
  assert.equal(canConnectWhatsApp("platform_admin"), true);
  assert.equal(canConnectWhatsApp("tenant_admin"), true);
  assert.equal(canConnectWhatsApp("tenant_staff"), false);
  assert.equal(canConnectWhatsApp("tenant_viewer"), false);
  assert.equal(canConnectWhatsApp(null), false);
});

test("merging keeps the first occurrence and the order", () => {
  assert.deepEqual(
    mergeById([{ id: "a", n: 1 }, { id: "b", n: 2 }, { id: "a", n: 3 }, { id: "c", n: 4 }, { id: "b", n: 5 }]),
    [{ id: "a", n: 1 }, { id: "b", n: 2 }, { id: "c", n: 4 }],
  );
  assert.deepEqual(mergeById([{ id: 2 }, { id: 1 }, { id: 2 }]), [{ id: 2 }, { id: 1 }]);
  assert.deepEqual(mergeById([]), []);
});

// Local-time construction keeps these independent of the machine time zone.
const NOW = new Date(2026, 9, 28, 15, 30, 0).getTime();
const at = (ms) => new Date(NOW - ms).toISOString();
const S = 1000, MIN = 60 * S, HOUR = 60 * MIN;

test("under a minute is Just now", () => {
  assert.equal(listTime(at(0), NOW), "Just now");
  assert.equal(listTime(at(59 * S), NOW), "Just now");
});

test("from a minute it counts minutes", () => {
  assert.equal(listTime(at(60 * S), NOW), "1 min");
  assert.equal(listTime(at(2 * MIN + 10 * S), NOW), "2 min");
  assert.equal(listTime(at(25 * MIN), NOW), "25 min");
  assert.equal(listTime(at(59 * MIN + 59 * S), NOW), "59 min");
});

test("from an hour it counts hours up to a day", () => {
  assert.equal(listTime(at(60 * MIN), NOW), "1 h");
  assert.equal(listTime(at(3 * HOUR + 5 * MIN), NOW), "3 h");
  assert.equal(listTime(at(23 * HOUR), NOW), "23 h");
});

test("past 24 hours it uses the calendar day in local time", () => {
  // NOW is 28 Oct 15:30 local.
  const yesterdayMidnight = new Date(2026, 9, 27, 0, 0, 0).toISOString();
  const yesterdayNoon = new Date(2026, 9, 27, 12, 0, 0).toISOString();
  const dayBefore = new Date(2026, 9, 26, 23, 59, 59).toISOString();
  assert.equal(listTime(yesterdayNoon, NOW), "Yesterday");
  assert.equal(listTime(yesterdayMidnight, NOW), "Yesterday");
  assert.equal(listTime(dayBefore, NOW), "26 Oct");
});

test("just after midnight, 24 hours back is Yesterday and 25 hours back is the day before", () => {
  const early = new Date(2026, 9, 28, 0, 20, 0).getTime();
  // 24 hours earlier is 27 Oct 00:20 local; 25 hours earlier is 26 Oct 23:20.
  assert.equal(listTime(new Date(early - 24 * HOUR).toISOString(), early), "Yesterday");
  assert.equal(listTime(new Date(early - 25 * HOUR).toISOString(), early), "26 Oct");
  // 23 hours earlier is still an hour count even though it crosses midnight.
  assert.equal(listTime(new Date(early - 23 * HOUR).toISOString(), early), "23 h");
});

test("older dates show day and short month, with the year when it differs", () => {
  assert.equal(listTime(new Date(2026, 9, 20, 9, 0, 0).toISOString(), NOW), "20 Oct");
  assert.equal(listTime(new Date(2026, 0, 3, 9, 0, 0).toISOString(), NOW), "3 Jan");
  assert.equal(listTime(new Date(2025, 9, 27, 9, 0, 0).toISOString(), NOW), "27 Oct 2025");
  assert.equal(listTime(new Date(2025, 11, 31, 9, 0, 0).toISOString(), NOW), "31 Dec 2025");
});

test("a future timestamp is clamped to Just now", () => {
  assert.equal(listTime(new Date(NOW + 5 * MIN).toISOString(), NOW), "Just now");
  assert.equal(listTime(new Date(NOW + 5 * 24 * HOUR).toISOString(), NOW), "Just now");
});

test("an invalid date gives an empty string", () => {
  assert.equal(listTime("", NOW), "");
  assert.equal(listTime("not a date", NOW), "");
});
