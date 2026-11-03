import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/sandbox-chat.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { sandboxStatus, sandboxDelayed } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

test("sandbox delivery labels distinguish queued, held, failed and available", () => {
  assert.match(sandboxStatus.queued, /^Waiting to start$/);
  assert.match(sandboxStatus.awaiting_delivery, /^Reply on its way$/);
  assert.match(sandboxStatus.held, /Held for a staff check.*can't release it/);
  assert.match(sandboxStatus.failed, /do not assume/);
  assert.match(sandboxStatus.available, /only you can see this/);
  assert.match(sandboxStatus.no_reply, /without a reply/);
});

test("deadline is exactly 90 seconds, not an invented failure/delivery state", () => {
  const created_at = "2026-10-16T00:00:00Z";
  const start = Date.parse(created_at);
  for (const status of ["queued", "processing", "awaiting_delivery"]) {
    assert.equal(sandboxDelayed({ status, created_at }, start + 90_000), false);
    assert.equal(sandboxDelayed({ status, created_at }, start + 90_001), true);
  }
  for (const status of ["held", "available", "failed", "no_reply"])
    assert.equal(sandboxDelayed({ status, created_at }, start + 180_000), false);
});

const chat = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const ROLES = ["platform_admin", "tenant_admin", "tenant_staff", "read_only", null];
const FORBIDDEN = /customer records?|order|ticket|invoice|booking|email|approv|escalat|payment/i;

test("the limits wall is gone: no limits copy or disclaimer mode is exported", () => {
  assert.equal(chat.disclaimerMode, undefined);
  assert.equal(chat.SANDBOX_COMPACT_NOTICE, undefined);
});

test("Check for new messages is for a failed refresh or the 90-second notice only", () => {
  const created_at = "2026-10-16T00:00:00Z";
  const start = Date.parse(created_at);
  const turn = (status) => ({ status, created_at });
  // Healthy: nothing failed, nothing late.
  assert.equal(chat.needsManualCheck("", [], start), false);
  assert.equal(chat.needsManualCheck("", [turn("available"), turn("held"), turn("failed")], start + 600_000), false);
  assert.equal(chat.needsManualCheck("", [turn("processing")], start + 90_000), false);
  // A failed automatic refresh, with or without messages.
  assert.equal(chat.needsManualCheck("Failed to fetch", [], start), true);
  assert.equal(chat.needsManualCheck("Failed to fetch", [turn("available")], start), true);
  // The delay notice (the same rule that shows it).
  for (const status of ["queued", "processing", "awaiting_delivery"])
    assert.equal(chat.needsManualCheck("", [turn("available"), turn(status)], start + 90_001), true, status);
});

test("every role gets the same three prompt chips, all inside the sandbox limits", () => {
  const expected = [
    "What does our knowledge base say about ",
    "Which of our products ",
    "How should we answer a customer who asks about ",
  ];
  assert.deepEqual([...chat.SANDBOX_CHIPS], expected);
  // Each starter ends in a space so the user types the rest; the button shows it with an ellipsis.
  for (const chip of expected) assert.ok(chip.endsWith(" "), chip);
  assert.deepEqual(expected.map(chat.chipLabel), [
    "What does our knowledge base say about…",
    "Which of our products…",
    "How should we answer a customer who asks about…",
  ]);
  for (const role of ROLES) {
    const chips = chat.chipsFor(role);
    assert.deepEqual(chips, expected, `chips for ${role}`);
    for (const chip of chips) assert.doesNotMatch(chip, FORBIDDEN, chip);
  }
  // A caller that edits its copy cannot change the shared list.
  chat.chipsFor("tenant_staff").push("Approve a refund");
  assert.deepEqual(chat.chipsFor("tenant_staff"), expected);
});

test("the log follows the end only for its first load, the reader's own question or a reader at the end", () => {
  const empty = { thread: "a", firstId: null, lastKey: null };
  const loaded = { thread: "a", firstId: 11, lastKey: "k30" };
  const own = new Set(["mine"]);
  // Nothing loaded yet: nothing to do.
  assert.equal(chat.scrollPlan(empty, empty, false, own), "stay");
  // First load of a conversation, or another conversation: the end.
  assert.equal(chat.scrollPlan(empty, loaded, false, own), "bottom");
  assert.equal(chat.scrollPlan({ ...loaded, thread: "b" }, loaded, false, own), "bottom");
  // Older turns arrive above: keep what the reader sees, even if they sat at the end.
  assert.equal(chat.scrollPlan(loaded, { ...loaded, firstId: 1 }, false, own), "keep-offset");
  assert.equal(chat.scrollPlan(loaded, { ...loaded, firstId: 1 }, true, own), "keep-offset");
  // A poll with a new turn or reply: only a reader already at the end follows it.
  assert.equal(chat.scrollPlan(loaded, { ...loaded, lastKey: "k31" }, false, own), "stay");
  assert.equal(chat.scrollPlan(loaded, { ...loaded, lastKey: "k31" }, true, own), "bottom");
  assert.equal(chat.scrollPlan(loaded, loaded, false, own), "stay");
  // The reader's own new question: the end, wherever they were.
  assert.equal(chat.scrollPlan(loaded, { ...loaded, lastKey: "mine" }, false, own), "bottom");
  // The same own question seen again by a later poll is not new.
  assert.equal(chat.scrollPlan({ ...loaded, lastKey: "mine" }, { ...loaded, lastKey: "mine" }, false, own), "stay");
});

test("only a turn the server is processing counts as being worked on", () => {
  // Every status name the file knows, so a new status cannot slip in unclassified.
  const names = Object.keys(chat.sandboxStatus);
  assert.deepEqual([...names].sort(),
    ["available", "awaiting_delivery", "failed", "held", "no_reply", "processing", "queued"]);
  const working = names.filter((status) => chat.isWorking(status));
  assert.deepEqual(working, ["processing"]);
  // Not delivered yet, waiting for delivery, and every finished state are not "being worked on".
  for (const status of ["queued", "awaiting_delivery", "held", "available", "failed", "no_reply"])
    assert.equal(chat.isWorking(status), false, status);
  // A status this build does not know never animates.
  assert.equal(chat.isWorking("pending"), false);
  assert.equal(chat.isWorking(""), false);
});

test("follow-up starters only prefill, stay inside the sandbox limits and are a fresh copy", () => {
  const expected = ["Tell me more about ", "Say that in a shorter way", "Give me an example"];
  assert.deepEqual(chat.followUpsFor(), expected);
  for (const starter of chat.followUpsFor()) assert.doesNotMatch(starter, FORBIDDEN, starter);
  // The button shows an ellipsis only where the reader is meant to finish the sentence.
  assert.deepEqual(expected.map(chat.followUpLabel),
    ["Tell me more about…", "Say that in a shorter way", "Give me an example"]);
  chat.followUpsFor().push("Approve a refund");
  assert.deepEqual(chat.followUpsFor(), expected);
});

test("follow-ups are offered only after an answered, settled conversation", () => {
  const turn = (status, replies = 0) => ({ status, replies: Array.from({ length: replies }, (_, id) => ({ id: String(id), body: "x" })) });
  assert.equal(chat.followUpsReady([]), false);
  assert.equal(chat.followUpsReady([turn("available", 1)]), true);
  assert.equal(chat.followUpsReady([turn("failed"), turn("available", 2)]), true);
  // The latest turn has no reply (held, failed, no reply) or one is still on its way.
  for (const status of ["held", "failed", "no_reply", "queued", "processing", "awaiting_delivery"])
    assert.equal(chat.followUpsReady([turn("available", 1), turn(status)]), false, status);
  // An answered turn followed by an unfinished one.
  assert.equal(chat.followUpsReady([turn("processing"), turn("available", 1)]), false);
  assert.equal(chat.followUpsReady([turn("available", 0)]), false);
});

test("the jump-to-latest pill shows only when the reader is more than a screen from the end", () => {
  assert.equal(chat.farFromEnd({ scrollHeight: 3000, scrollTop: 2100, clientHeight: 900 }), false);
  assert.equal(chat.farFromEnd({ scrollHeight: 3000, scrollTop: 1200, clientHeight: 900 }), false);
  assert.equal(chat.farFromEnd({ scrollHeight: 3000, scrollTop: 1199, clientHeight: 900 }), true);
  assert.equal(chat.farFromEnd({ scrollHeight: 800, scrollTop: 0, clientHeight: 900 }), false);
});
