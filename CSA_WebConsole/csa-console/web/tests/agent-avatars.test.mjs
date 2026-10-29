import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
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
const { AGENT_IDS, AGENT_TITLES, avatarForAgent, avatarForPackage } = avatars;

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

test("packages map onto avatars and e_invoicing has none", () => {
  const expected = {
    customer_support: "support",
    orders_products: "orders",
    subscriptions: "subscriptions",
    bookings: "bookings",
    marketplaces: "ecommerce",
    hr: "hr",
    staff_email: "email",
  };
  for (const [key, id] of Object.entries(expected)) {
    assert.equal(avatarForPackage(key), id, key);
  }
  assert.equal(avatarForPackage("e_invoicing"), null);
  for (const key of ["", "nonsense", "support", "constructor", "__proto__", "toString"]) {
    assert.equal(avatarForPackage(key), null, key);
  }
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

test("every AgentAvatar caller uses a size the pixel grids can scale to a whole number", async () => {
  const { AVATAR_SIZES, gridForSize } = await import(moduleUrl(
    await readFile(new URL("../lib/pixel-avatars.ts", import.meta.url), "utf8"),
  ));
  const root = new URL("../", import.meta.url).pathname;
  const files = [];
  async function walk(directory) {
    for (const entry of await readdir(`${root}${directory}`, { withFileTypes: true })) {
      const relative = `${directory}/${entry.name}`;
      if (entry.isDirectory()) await walk(relative);
      else if (relative.endsWith(".tsx")) files.push(relative);
    }
  }
  await walk("components");
  await walk("app");
  let seen = 0;
  for (const file of files) {
    const source = await readFile(`${root}${file}`, "utf8");
    for (const [, props] of source.matchAll(/<AgentAvatar\b([^>]*)\/?>/g)) {
      seen += 1;
      const size = /size=\{(\d+)\}/.exec(props);
      if (!size) continue; // the default size
      assert.ok(AVATAR_SIZES.includes(Number(size[1])), `${file}: size ${size[1]}`);
      assert.equal(Number(size[1]) % gridForSize(Number(size[1])), 0);
    }
  }
  assert.ok(seen >= 2, "found the known callers");
  const component = await readFile(`${root}components/console/fs/AgentAvatar.tsx`, "utf8");
  assert.match(component, /size = 48/, "the default size is 48");
});
