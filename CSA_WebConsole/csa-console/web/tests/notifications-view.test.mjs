import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/notifications-view.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const {
  renderNotification,
  notificationLink,
  durationText,
  badgeText,
  agoText,
  isUnread,
  applyRead,
  unreadAfter,
} = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const UUID = "0b9c1d52-3f4e-4a6b-8c7d-1e2f3a4b5c6d";
const render = (message_key, params) => renderNotification({ message_key, params });

test("the production source holds no runtime alias imports", () => {
  assert.equal(/\bfrom\s+["']@\//.test(source), false);
  assert.equal(/^import\s+(?!type\b)/m.test(source), false, "only `import type` is allowed");
});

test("the six kinds read exactly as decided, with every param present", () => {
  assert.deepEqual(
    render("inbox.overdue", { customer: "Aisyah", waited_minutes: 45, priority: "normal", repeat: 1 }),
    { title: "Waiting for your team", detail: "Aisyah has waited 45 minutes." },
  );
  assert.deepEqual(render("inbox.snooze_due", { customer: "Aisyah" }), {
    title: "Snooze ended",
    detail: "Aisyah is back in Needs us.",
  });
  assert.deepEqual(render("inbox.stale_claim", { customer: "Aisyah", waited_minutes: 65 }), {
    title: "Claimed, no reply yet",
    detail: "Aisyah was claimed but has had no reply. Waiting 1 hour 5 minutes.",
  });
  assert.deepEqual(render("inbox.follow_up", { customer: "Aisyah", waited_minutes: 120 }), {
    title: "Follow up or resolve",
    detail: "Aisyah has not answered for 2 hours. Follow up or resolve it.",
  });
  assert.deepEqual(render("inbox.handback", { customer: "Aisyah", waited_minutes: 3000 }), {
    title: "Hand it back?",
    detail:
      "Aisyah has not answered for 2 days. You can hand the chat back to the assistant.",
  });
  assert.deepEqual(render("inbox.assigned", { customer: "Aisyah", assigned_by: "Jo" }), {
    title: "Assigned to you",
    detail: "Aisyah was assigned to you.",
  });
});

test("a failed notification target reads in plain words and names the channel", () => {
  assert.deepEqual(render("notify.target_failed", { target_id: UUID, channel: "email", attempts: 5 }), {
    title: "A notification target stopped working",
    detail: "Email alerts could not be delivered after 5 attempts. Check the address on the Notifications page.",
  });
  assert.deepEqual(render("notify.target_failed", { target_id: UUID, channel: "telegram", attempts: 1 }), {
    title: "A notification target stopped working",
    detail: "Telegram alerts could not be delivered after 1 attempt. Check the staff chat on the Notifications page.",
  });
  assert.equal(
    render("notify.target_failed", { channel: "email" }).detail,
    "Email alerts could not be delivered. Check the address on the Notifications page.",
    "a missing attempt count is dropped, never a gap",
  );
  for (const params of [undefined, {}, { channel: {}, attempts: "5" }, { channel: "sms", attempts: Number.NaN }]) {
    const { title, detail } = render("notify.target_failed", params);
    assert.equal(title, "A notification target stopped working");
    assert.match(detail, /^Alerts could not be delivered\. Check the .* on the Notifications page\.$/);
    assert.doesNotMatch(detail, /undefined|NaN|\[object|null/);
  }
});

test("a failed notification email reads in plain words", () => {
  assert.deepEqual(render("notify.email_failed", { attempts: 5 }), {
    title: "Your notification email could not be delivered",
    detail: "We tried 5 times. Check your email settings on the Notifications page.",
  });
  assert.deepEqual(render("notify.email_failed", { attempts: 1 }), {
    title: "Your notification email could not be delivered",
    detail: "We tried once. Check your email settings on the Notifications page.",
  });
  for (const params of [undefined, {}, { attempts: "5" }, { attempts: 0 }, { attempts: Number.NaN }]) {
    assert.deepEqual(render("notify.email_failed", params), {
      title: "Your notification email could not be delivered",
      detail: "Check your email settings on the Notifications page.",
    });
  }
});

test("a missing duration drops the duration, never leaves a gap", () => {
  assert.equal(
    render("inbox.overdue", { customer: "Aisyah" }).detail,
    "Aisyah is waiting for your team.",
  );
  assert.equal(
    render("inbox.stale_claim", { customer: "Aisyah" }).detail,
    "Aisyah was claimed but has had no reply.",
  );
  assert.equal(
    render("inbox.follow_up", { customer: "Aisyah" }).detail,
    "Aisyah has not answered. Follow up or resolve it.",
  );
  assert.equal(
    render("inbox.handback", { customer: "Aisyah" }).detail,
    "Aisyah has not answered. You can hand the chat back to the assistant.",
  );
  // Zero and negative minutes are as good as missing.
  assert.equal(
    render("inbox.overdue", { customer: "Aisyah", waited_minutes: 0 }).detail,
    "Aisyah is waiting for your team.",
  );
  assert.equal(
    render("inbox.overdue", { customer: "Aisyah", waited_minutes: -5 }).detail,
    "Aisyah is waiting for your team.",
  );
});

test("overdue repeat: 1 and absent say nothing, 2 and more say the reminder number", () => {
  const base = "Aisyah has waited 45 minutes.";
  assert.equal(render("inbox.overdue", { customer: "Aisyah", waited_minutes: 45 }).detail, base);
  assert.equal(
    render("inbox.overdue", { customer: "Aisyah", waited_minutes: 45, repeat: 1 }).detail,
    base,
  );
  assert.equal(
    render("inbox.overdue", { customer: "Aisyah", waited_minutes: 45, repeat: 2 }).detail,
    `${base} Reminder 2.`,
  );
  assert.equal(
    render("inbox.overdue", { customer: "Aisyah", waited_minutes: 45, repeat: 7 }).detail,
    `${base} Reminder 7.`,
  );
  for (const repeat of [0, -3, 1.5, "2", null, {}, Number.NaN]) {
    assert.equal(
      render("inbox.overdue", { customer: "Aisyah", waited_minutes: 45, repeat }).detail,
      base,
      `repeat ${String(repeat)} adds nothing`,
    );
  }
  // repeat only belongs to overdue.
  assert.equal(
    render("inbox.snooze_due", { customer: "Aisyah", repeat: 3 }).detail,
    "Aisyah is back in Needs us.",
  );
});

test("priority high and urgent add a sentence on overdue only", () => {
  const base = "Aisyah has waited 45 minutes.";
  const overdue = (priority, extra = {}) =>
    render("inbox.overdue", { customer: "Aisyah", waited_minutes: 45, priority, ...extra }).detail;
  assert.equal(overdue("high"), `${base} Priority: high.`);
  assert.equal(overdue("urgent"), `${base} Priority: urgent.`);
  assert.equal(overdue("low"), base);
  assert.equal(overdue("normal"), base);
  assert.equal(overdue("whatever"), base);
  assert.equal(overdue(undefined), base);
  assert.equal(overdue(5), base);
  assert.equal(overdue("urgent", { repeat: 3 }), `${base} Priority: urgent. Reminder 3.`);
  for (const key of ["inbox.snooze_due", "inbox.stale_claim", "inbox.follow_up", "inbox.handback", "inbox.assigned"]) {
    assert.doesNotMatch(
      render(key, { customer: "Aisyah", waited_minutes: 45, priority: "urgent" }).detail,
      /Priority/,
      `${key} carries no priority sentence`,
    );
  }
});

test("a missing, null, empty or blank customer reads as a customer", () => {
  for (const customer of [undefined, null, "", "   ", "\n\t", 42, {}, [], true]) {
    const detail = render("inbox.snooze_due", { customer }).detail;
    assert.equal(detail, "A customer is back in Needs us.", `customer ${JSON.stringify(customer)}`);
  }
  assert.equal(render("inbox.snooze_due", {}).detail, "A customer is back in Needs us.");
  assert.equal(
    render("inbox.overdue", { waited_minutes: 10 }).detail,
    "A customer has waited 10 minutes.",
  );
  assert.equal(
    render("inbox.assigned", { customer: "  Aisyah  " }).detail,
    "Aisyah was assigned to you.",
    "the name is trimmed",
  );
});

test("an unknown key gives the generic pair and never shows the key", () => {
  for (const key of ["inbox.brand_new_thing", "", undefined, null, 7, {}, "inbox.overdue.extra"]) {
    const out = render(key, { customer: "Aisyah", waited_minutes: 5 });
    assert.deepEqual(out, {
      title: "New notification",
      detail: "Open the console to see the details.",
    });
  }
  assert.doesNotMatch(
    render("inbox.brand_new_thing", {}).title + render("inbox.brand_new_thing", {}).detail,
    /inbox|brand_new/,
  );
});

test("wrong-typed or missing params never print undefined, NaN or [object Object]", () => {
  const bad = [
    undefined,
    null,
    "text",
    42,
    [],
    {},
    { customer: {}, waited_minutes: "45", priority: {}, repeat: "2", assigned_by: {} },
    { customer: ["x"], waited_minutes: Number.NaN, repeat: Number.NaN },
    { customer: null, waited_minutes: null, priority: null, repeat: null },
    { waited_minutes: Number.POSITIVE_INFINITY, repeat: Number.POSITIVE_INFINITY },
    { waited_minutes: {}, customer: { toString: () => "[object Object]" } },
  ];
  const keys = [
    "inbox.overdue",
    "inbox.snooze_due",
    "inbox.stale_claim",
    "inbox.follow_up",
    "inbox.handback",
    "inbox.assigned",
    "inbox.nope",
  ];
  for (const params of bad) {
    for (const key of keys) {
      const { title, detail } = render(key, params);
      assert.ok(title.length > 0 && detail.length > 0);
      for (const text of [title, detail]) {
        assert.doesNotMatch(text, /undefined|NaN|\[object|null|Infinity/);
        assert.doesNotMatch(text, /\{|\}/);
      }
    }
  }
  // Items missing the params object entirely.
  assert.doesNotThrow(() => renderNotification({ message_key: "inbox.overdue" }));
  assert.doesNotThrow(() => renderNotification({}));
});

test("durationText table", () => {
  const table = [
    [0, null],
    [0.5, null],
    [1, "1 minute"],
    [1.9, "1 minute"],
    [2, "2 minutes"],
    [45, "45 minutes"],
    [59, "59 minutes"],
    [60, "1 hour"],
    [65, "1 hour 5 minutes"],
    [119, "1 hour 59 minutes"],
    [120, "2 hours"],
    [121, "2 hours 1 minute"],
    [47 * 60 + 59, "47 hours 59 minutes"],
    [48 * 60, "2 days"],
    [48 * 60 + 59, "2 days"],
    [72 * 60, "3 days"],
    [100 * 60, "4 days"],
  ];
  for (const [input, expected] of table) {
    assert.equal(durationText(input), expected, `durationText(${input})`);
  }
  for (const input of [-1, Number.NaN, Number.POSITIVE_INFINITY, "45", null, undefined, {}, [], true]) {
    assert.equal(durationText(input), null, `durationText(${String(input)})`);
  }
});

test("badgeText table", () => {
  for (const input of [undefined, null, "3", -1, 1.5, 0, Number.NaN, Number.POSITIVE_INFINITY, {}, []]) {
    assert.equal(badgeText(input), null, `badgeText(${String(input)})`);
  }
  assert.equal(badgeText(1), "1");
  assert.equal(badgeText(42), "42");
  assert.equal(badgeText(99), "99");
  assert.equal(badgeText(100), "99+");
  assert.equal(badgeText(12345), "99+");
});

test("notificationLink opens the Notifications page for a notifications route", () => {
  assert.deepEqual(notificationLink({ link: { route: "notifications" } }), { route: "notifications" });
  assert.deepEqual(
    notificationLink({ link: { route: "notifications", conversation: "x", junk: { a: 1 } } }),
    { route: "notifications" },
    "extra keys are dropped",
  );
  for (const route of ["Notifications", ["notifications"], "notifications/x", "/notifications", "", null, 7]) {
    assert.equal(notificationLink({ link: { route } }), null, JSON.stringify(route));
  }
});

test("notificationLink accepts only an inbox route with a UUID conversation", () => {
  assert.deepEqual(notificationLink({ link: { route: "inbox", conversation: UUID } }), {
    route: "inbox",
    conversation: UUID,
  });
  assert.deepEqual(
    notificationLink({ link: { route: "inbox", conversation: UUID, junk: { a: 1 } } }),
    { route: "inbox", conversation: UUID },
    "extra keys are dropped",
  );
  const bad = [
    null,
    undefined,
    "inbox",
    42,
    [],
    {},
    { route: "hr", conversation: UUID },
    { route: "Inbox", conversation: UUID },
    { route: ["inbox"], conversation: UUID },
    { route: "inbox" },
    { route: "inbox", conversation: null },
    { route: "inbox", conversation: "" },
    { route: "inbox", conversation: "abc" },
    { route: "inbox", conversation: `${UUID}x` },
    { route: "inbox", conversation: `/${UUID}` },
    { route: "inbox", conversation: `${UUID}&x=1` },
    { route: "inbox", conversation: 7 },
    { route: "inbox", conversation: { id: UUID } },
    { route: "inbox", conversation: [UUID] },
  ];
  for (const link of bad) {
    assert.equal(notificationLink({ link }), null, JSON.stringify(link));
  }
  assert.equal(notificationLink({}), null);
});

test("agoText on a fixed now", () => {
  const now = new Date("2026-11-19T12:00:00.000Z");
  const ago = (ms) => agoText(now, new Date(now.getTime() - ms).toISOString());
  const S = 1000;
  const M = 60 * S;
  const H = 60 * M;
  const D = 24 * H;
  const table = [
    [0, "just now"],
    [30 * S, "just now"],
    [59 * S + 999, "just now"],
    [60 * S, "1 minute ago"],
    [5 * M, "5 minutes ago"],
    [59 * M + 59 * S, "59 minutes ago"],
    [60 * M, "1 hour ago"],
    [119 * M, "1 hour ago"],
    [3 * H, "3 hours ago"],
    [23 * H + 59 * M, "23 hours ago"],
    [24 * H, "yesterday"],
    [47 * H + 59 * M, "yesterday"],
    [48 * H, "2 days ago"],
    [4 * D, "4 days ago"],
    [4 * D + 23 * H, "4 days ago"],
    [40 * D, "40 days ago"],
    [-5 * M, "just now"], // a clock a little ahead of the server
  ];
  for (const [ms, expected] of table) assert.equal(ago(ms), expected, `${ms} ms ago`);
  for (const iso of ["", "not a date", "2026-13-45", null, undefined, 7, {}]) {
    assert.equal(agoText(now, iso), "", `agoText(${String(iso)})`);
  }
  assert.equal(agoText(new Date(Number.NaN), "2026-11-19T11:00:00Z"), "");
});

const row = (id, read_at = null) => ({
  id,
  kind: "inbox",
  priority: "normal",
  message_key: "inbox.overdue",
  params: {},
  link: null,
  read_at,
  created_at: "2026-11-19T10:00:00Z",
});
const AT = "2026-11-19T12:00:00Z";

test("isUnread follows read_at", () => {
  assert.equal(isUnread({ read_at: null }), true);
  assert.equal(isUnread({ read_at: AT }), false);
  assert.equal(isUnread({}), true, "a missing read_at is unread");
});

test("applyRead marks matching unread rows and leaves the rest alone", () => {
  const items = [row("a"), row("b", "2026-11-18T09:00:00Z"), row("c")];
  const some = applyRead(items, ["a", "b", "zzz"], AT);
  assert.deepEqual(
    some.map((i) => i.read_at),
    [AT, "2026-11-18T09:00:00Z", null],
    "an already-read row keeps its first read time; unknown ids do nothing",
  );
  assert.deepEqual(
    applyRead(items, "all", AT).map((i) => i.read_at),
    [AT, "2026-11-18T09:00:00Z", AT],
  );
  assert.deepEqual(
    applyRead(items, [], AT).map((i) => i.read_at),
    [null, "2026-11-18T09:00:00Z", null],
  );
  // Pure: the input is untouched and the output is a new array.
  assert.equal(items[0].read_at, null);
  assert.notEqual(some, items);
  assert.equal(some[2], items[2], "an untouched row keeps its identity");
});

test("unreadAfter subtracts only the unread rows that matched, never below zero", () => {
  const items = [row("a"), row("b", "2026-11-18T09:00:00Z"), row("c")];
  assert.equal(unreadAfter(5, items, ["a"]), 4);
  assert.equal(unreadAfter(5, items, ["a", "c"]), 3);
  assert.equal(unreadAfter(5, items, ["a", "a"]), 4, "a repeated id counts once");
  assert.equal(unreadAfter(5, items, ["b"]), 5, "an already-read row changes nothing");
  assert.equal(unreadAfter(5, items, ["zzz"]), 5, "an unknown id changes nothing");
  assert.equal(unreadAfter(5, items, []), 5);
  assert.equal(unreadAfter(5, items, "all"), 0, "all clears rows beyond the loaded page too");
  assert.equal(unreadAfter(1, items, ["a", "c"]), 0, "never below zero");
  assert.equal(unreadAfter(0, items, ["a"]), 0);
  assert.equal(unreadAfter(Number.NaN, items, ["a"]), 0);
});
