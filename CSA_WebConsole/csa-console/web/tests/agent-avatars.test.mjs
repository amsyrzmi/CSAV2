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
const avatars = await import(moduleUrl(
  await readFile(new URL("../lib/agent-avatars.ts", import.meta.url), "utf8"),
));
const { AGENT_IDS, AGENT_TITLES, avatarForAgent } = avatars;

test("capability names map onto the eleven avatar ids in one place", () => {
  const expected = {
    kb_agent: "knowledge",
    ticket_agent: "tickets",
    escalation_agent: "tickets",
    order_agent: "orders",
    order_approval: "orders",
    invoice_agent: "invoices",
    ecommerce_agent: "ecommerce",
    subscription_agent: "subscriptions",
    booking_agent: "bookings",
  };
  for (const [name, id] of Object.entries(expected)) {
    assert.equal(avatarForAgent(name), id, name);
  }
});

test("the service ids map to themselves", () => {
  for (const id of ["support", "assistant", "hr", "email"]) {
    assert.equal(avatarForAgent(id), id);
  }
});

test("lhdn_agent and unknown names have no avatar", () => {
  assert.equal(avatarForAgent("lhdn_agent"), null);
  assert.equal(avatarForAgent("nonsense"), null);
  assert.equal(avatarForAgent(""), null);
  assert.equal(avatarForAgent("constructor"), null);
  assert.equal(avatarForAgent("__proto__"), null);
});

test("there are eleven distinct ids and each has a title", () => {
  assert.equal(AGENT_IDS.length, 11);
  assert.equal(new Set(AGENT_IDS).size, 11);
  for (const id of AGENT_IDS) {
    assert.equal(typeof AGENT_TITLES[id], "string", id);
    assert.notEqual(AGENT_TITLES[id], "");
  }
  assert.deepEqual(Object.keys(AGENT_TITLES).sort(), [...AGENT_IDS].sort());
});
