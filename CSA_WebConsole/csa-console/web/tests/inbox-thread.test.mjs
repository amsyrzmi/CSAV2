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
const thread = await import(moduleUrl(
  await readFile(new URL("../lib/inbox-thread.ts", import.meta.url), "utf8"),
));
const {
  senderOf, authorLabel, threadItems, dayLabel, clockTime, dateTime, cleanUsername, cleanContact,
  rowSubtitle, isExpired, approvalLabel, unseenCount, neighbourId, nearBottom, newMessagesText,
} = thread;

/** Local wall-clock time as ISO, so the tests pass in every time zone. */
const at = (y, m, d, h = 9, min = 0) => new Date(y, m - 1, d, h, min).toISOString();
const msg = (id, direction, author, created_at) => ({ id, direction, author, created_at });

test("inbound is the customer, outbound human_support is staff, any other outbound is the assistant", () => {
  assert.equal(senderOf({ direction: "inbound", author: "customer" }), "customer");
  assert.equal(senderOf({ direction: "outbound", author: "human_support" }), "staff");
  assert.equal(senderOf({ direction: "outbound", author: "ai" }), "assistant");
  assert.deepEqual(["customer", "staff", "assistant"].map(authorLabel), ["Customer", "Staff", "Assistant"]);
});

test("dates read 27 Oct 2026 and 27 Oct 2026, 09:15 in local time; bad input is blank", () => {
  assert.equal(dayLabel(at(2026, 10, 27, 9, 15)), "27 Oct 2026");
  assert.equal(clockTime(at(2026, 10, 27, 9, 5)), "09:05");
  assert.equal(clockTime(at(2026, 10, 27, 21, 40)), "21:40");
  assert.equal(dateTime(at(2026, 10, 27, 9, 15)), "27 Oct 2026, 09:15");
  assert.equal(dateTime(at(2026, 1, 3, 0, 0)), "3 Jan 2026, 00:00");
  assert.equal(dayLabel("nonsense"), "");
  assert.equal(dateTime("nonsense"), "");
});

test("a day divider opens each local day and same-sender messages within five minutes group", () => {
  const items = threadItems([
    msg("1", "inbound", "customer", at(2026, 10, 26, 23, 58)),
    msg("2", "inbound", "customer", at(2026, 10, 27, 0, 1)),
    msg("3", "inbound", "customer", at(2026, 10, 27, 0, 4)),
    msg("4", "outbound", "ai", at(2026, 10, 27, 0, 5)),
    msg("5", "outbound", "human_support", at(2026, 10, 27, 0, 6)),
    msg("6", "outbound", "human_support", at(2026, 10, 27, 0, 12)),
  ]);
  assert.deepEqual(
    items.map((item) => item.kind === "day" ? `day:${item.label}` : `${item.message.id}:${item.first ? "F" : "-"}${item.last ? "L" : "-"}`),
    ["day:26 Oct 2026", "1:FL", "day:27 Oct 2026", "2:F-", "3:-L", "4:FL", "5:FL", "6:FL"],
  );
  const keys = items.map((item) => item.key);
  assert.equal(new Set(keys).size, keys.length, "keys are unique");
});

test("a message with an unreadable time starts its own group and gets no divider", () => {
  const items = threadItems([
    msg("1", "inbound", "customer", at(2026, 10, 27, 9, 0)),
    msg("2", "inbound", "customer", "nonsense"),
  ]);
  assert.deepEqual(items.map((item) => item.kind), ["day", "message", "message"]);
  assert.equal(items[1].last, true);
  assert.equal(items[2].first, true);
});

test("a leading @ never doubles and a blank username is null", () => {
  assert.equal(cleanUsername("@aisyah_r"), "aisyah_r");
  assert.equal(cleanUsername("@@aisyah_r"), "aisyah_r");
  assert.equal(cleanUsername(" aisyah_r "), "aisyah_r");
  assert.equal(cleanUsername("@"), null);
  assert.equal(cleanUsername(null), null);
  const contact = { display_name: null, telegram_username: "@priyak", phone_masked: null, phone: null, kind: "customer", known: false };
  assert.equal(cleanContact(contact).telegram_username, "priyak");
  assert.equal(contact.telegram_username, "@priyak", "the input is not changed");
});

test("the row subtitle is dropped when it repeats the title or is only the channel name", () => {
  assert.equal(rowSubtitle("Nur Aisyah", "@aisyah_r · Telegram", "Telegram"), "@aisyah_r · Telegram");
  assert.equal(rowSubtitle("@aisyah_r", "@aisyah_r · Telegram", "Telegram"), null);
  assert.equal(rowSubtitle("+60•••0148", "+60•••0148 · WhatsApp", "WhatsApp"), null);
  assert.equal(rowSubtitle("Tan Wei Jian", "WhatsApp", "WhatsApp"), null);
  assert.equal(rowSubtitle("Tan", "Tan", "WhatsApp"), null);
});

test("a pending approval past its expiry is expired; a decided one never is", () => {
  const now = new Date(2026, 9, 27, 10, 0).getTime();
  assert.equal(isExpired({ status: "pending", expires_at: at(2026, 10, 27, 9, 59) }, now), true);
  assert.equal(isExpired({ status: "pending", expires_at: at(2026, 10, 27, 10, 1) }, now), false);
  assert.equal(isExpired({ status: "approved", expires_at: at(2026, 10, 27, 9, 0) }, now), false);
  assert.equal(isExpired({ status: "pending", expires_at: "nonsense" }, now), false);
});

test("approval labels are words with a tone and never the raw code", () => {
  assert.deepEqual(approvalLabel("pending", false), { text: "Needs review", tone: "attention" });
  assert.deepEqual(approvalLabel("pending", true), { text: "Expired", tone: "neutral" });
  assert.deepEqual(approvalLabel("approved", false), { text: "Approved", tone: "success" });
  assert.deepEqual(approvalLabel("edited", false), { text: "Sent after edit", tone: "success" });
  assert.deepEqual(approvalLabel("rejected", false), { text: "Rejected", tone: "neutral" });
  assert.deepEqual(approvalLabel("expired", false), { text: "Expired", tone: "neutral" });
  assert.deepEqual(approvalLabel("some_new_code", false), { text: "Handled", tone: "neutral" });
});

test("unseen counts messages newer than the last one the reader saw", () => {
  const times = [at(2026, 10, 27, 9, 0), at(2026, 10, 27, 9, 1), at(2026, 10, 27, 9, 2)];
  const seen = new Date(times[0]).getTime();
  assert.equal(unseenCount(times, seen), 2);
  assert.equal(unseenCount(times, new Date(times[2]).getTime()), 0);
  assert.equal(unseenCount(times, null), 0);
  assert.equal(newMessagesText(1), "1 new message");
  assert.equal(newMessagesText(2), "2 new messages");
});

test("J and K move one row and stop at the ends; with nothing selected they start at the top", () => {
  const ids = ["a", "b", "c"];
  assert.equal(neighbourId(ids, "a", "next"), "b");
  assert.equal(neighbourId(ids, "b", "previous"), "a");
  assert.equal(neighbourId(ids, "c", "next"), "c");
  assert.equal(neighbourId(ids, "a", "previous"), "a");
  assert.equal(neighbourId(ids, "", "next"), "a");
  assert.equal(neighbourId(ids, "gone", "previous"), "a");
  assert.equal(neighbourId([], "", "next"), null);
});

test("the reader is at the bottom within 80 px", () => {
  assert.equal(nearBottom({ scrollTop: 920, scrollHeight: 1400, clientHeight: 400 }), true);
  assert.equal(nearBottom({ scrollTop: 919, scrollHeight: 1400, clientHeight: 400 }), false);
  assert.equal(nearBottom({ scrollTop: 0, scrollHeight: 300, clientHeight: 400 }), true);
});
