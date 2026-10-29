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
const view = await import(moduleUrl(
  await readFile(new URL("../lib/services-view.ts", import.meta.url), "utf8"),
));
const { serviceStats, rowAvatarState, statusLabel } = view;

const agent = (name, active) => ({ name, capability: { name, active } });
const service = (key, status, agents = [], sellable = true) => ({
  key, status, sellable, agents,
});

// The synthetic fixture the preview stub uses: eight services.
function fixture() {
  return [
    service("customer_support", "active", [
      agent("kb_agent", true), agent("ticket_agent", true),
      agent("escalation_agent", true), agent("order_approval", true),
    ]),
    service("orders_products", "active", [
      agent("order_agent", true), agent("invoice_agent", false),
    ]),
    service("subscriptions", "requested", [agent("subscription_agent", false)]),
    service("bookings", "available"),
    service("marketplaces", "available"),
    service("e_invoicing", "available", [], false),
    service("hr", "active"),
    service("staff_email", "suspended"),
  ];
}

test("serviceStats counts the synthetic fixture", () => {
  assert.deepEqual(serviceStats(fixture()), {
    active: 3, agentsOn: 5, waiting: 1, suspended: 1,
  });
});

test("serviceStats of no services is all zero", () => {
  assert.deepEqual(serviceStats([]), { active: 0, agentsOn: 0, waiting: 0, suspended: 0 });
});

test("an agent listed under two services counts once", () => {
  const shared = agent("order_approval", true);
  const stats = serviceStats([
    service("customer_support", "active", [shared]),
    service("orders_products", "active", [shared, agent("order_agent", true)]),
  ]);
  assert.equal(stats.agentsOn, 2);
});

test("agents without a capability or with an inactive one are not counted", () => {
  const stats = serviceStats([
    service("hr", "active", [
      { name: "no_capability", capability: null },
      { name: "missing" },
      agent("off", false),
      agent("on", true),
    ]),
  ]);
  assert.equal(stats.agentsOn, 1);
});

test("agents count even under a service that is not active", () => {
  const stats = serviceStats([service("staff_email", "suspended", [agent("a", true)])]);
  assert.equal(stats.agentsOn, 1);
});

test("rowAvatarState animates only an active service", () => {
  assert.equal(rowAvatarState("active"), "idle");
  for (const status of ["available", "requested", "suspended"]) {
    assert.equal(rowAvatarState(status), "still", status);
  }
});

test("statusLabel reproduces today's words and tones", () => {
  assert.deepEqual(statusLabel(service("a", "available")), { text: "Available", tone: "neutral" });
  assert.deepEqual(statusLabel(service("a", "requested")), { text: "Requested", tone: "wait" });
  assert.deepEqual(statusLabel(service("a", "active")), { text: "Active", tone: "ok" });
  assert.deepEqual(statusLabel(service("a", "suspended")), { text: "Suspended", tone: "off" });
});

test("an unsellable available service is Coming soon", () => {
  assert.deepEqual(
    statusLabel(service("e_invoicing", "available", [], false)),
    { text: "Coming soon", tone: "neutral" },
  );
});

test("an unsellable service in any other status follows its status", () => {
  assert.deepEqual(statusLabel(service("a", "active", [], false)), { text: "Active", tone: "ok" });
  assert.deepEqual(statusLabel(service("a", "requested", [], false)), { text: "Requested", tone: "wait" });
  assert.deepEqual(statusLabel(service("a", "suspended", [], false)), { text: "Suspended", tone: "off" });
});
