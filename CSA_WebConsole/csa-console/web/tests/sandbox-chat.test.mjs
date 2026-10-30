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
  assert.match(sandboxStatus.queued, /not delivered/);
  assert.match(sandboxStatus.awaiting_delivery, /not available/);
  assert.match(sandboxStatus.held, /not delivered/);
  assert.match(sandboxStatus.failed, /do not assume/);
  assert.match(sandboxStatus.available, /privately/);
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

test("the disclaimer is full until the conversation has a message, then compact", () => {
  assert.equal(chat.disclaimerMode(0), "full");
  assert.equal(chat.disclaimerMode(1), "compact");
  assert.equal(chat.disclaimerMode(5), "compact");
});

test("every role gets the same three prompt chips, all inside the sandbox limits", () => {
  const expected = [
    "Ask something a customer might ask",
    "Check what the knowledge base says about a topic",
    "Rephrase an answer for a customer",
  ];
  assert.deepEqual([...chat.SANDBOX_CHIPS], expected);
  for (const role of ROLES) {
    const chips = chat.chipsFor(role);
    assert.deepEqual(chips, expected, `chips for ${role}`);
    for (const chip of chips) assert.doesNotMatch(chip, FORBIDDEN, chip);
  }
  // A caller that edits its copy cannot change the shared list.
  chat.chipsFor("tenant_staff").push("Approve a refund");
  assert.deepEqual(chat.chipsFor("tenant_staff"), expected);
});

test("the compact disclaimer is one line naming the sandbox limits", () => {
  assert.equal(chat.SANDBOX_COMPACT_NOTICE, "Private sandbox: knowledge and product questions only.");
  assert.doesNotMatch(chat.SANDBOX_COMPACT_NOTICE, /\n/);
});
