import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// lastChangeText formats in the runtime's local zone (the viewer's, in a browser). Pin the zone
// so the expected dates hold wherever the suite runs; Node re-reads TZ when it is assigned.
process.env.TZ = "UTC";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const view = await import(moduleUrl(
  await readFile(new URL("../lib/services-view.ts", import.meta.url), "utf8"),
));
const { serviceStats, rowAvatarState, statusLabel, agentCountLabel, lastChangeText, shortDate } = view;

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

test("agentCountLabel reads as a short phrase", () => {
  assert.equal(agentCountLabel(0), "No agents");
  assert.equal(agentCountLabel(1), "1 agent");
  assert.equal(agentCountLabel(4), "4 agents");
});

const AT = "2026-10-20T12:00:00Z";
const change = (action, actor = "Preview Admin", at = AT) => ({ action, at, actor });

test("lastChangeText words every action with a stable date and the server's actor", () => {
  const cases = [
    ["requested", "Requested 20 Oct 2026 by Preview Admin"],
    ["activated", "Activated 20 Oct 2026 by Preview Admin"],
    ["suspended", "Suspended 20 Oct 2026 by Preview Admin"],
    ["subscribed", "Subscription turned on 20 Oct 2026 by Preview Admin"],
    ["unsubscribed", "Subscription turned off 20 Oct 2026 by Preview Admin"],
    ["tenant_switch_on", "Tenant switch turned on 20 Oct 2026 by Preview Admin"],
    ["tenant_switch_off", "Tenant switch turned off 20 Oct 2026 by Preview Admin"],
  ];
  for (const [action, text] of cases) assert.equal(lastChangeText(change(action)), text, action);
});

test("lastChangeText never starts a line with the bare word Subscribed", () => {
  assert.equal(
    lastChangeText(change("subscribed", "a member of your team")),
    "Subscription turned on 20 Oct 2026 by a member of your team",
  );
});

test("lastChangeText shows the role labels the server sends", () => {
  assert.equal(
    lastChangeText(change("suspended", "a platform administrator", "2026-10-27T12:00:00Z")),
    "Suspended 27 Oct 2026 by a platform administrator",
  );
});

test("lastChangeText is null without a change, with a bad date or an unknown action", () => {
  assert.equal(lastChangeText(null), null);
  assert.equal(lastChangeText(undefined), null);
  assert.equal(lastChangeText(change("activated", "x", "not a date")), null);
  assert.equal(lastChangeText(change("activated", "x", "")), null);
  assert.equal(lastChangeText(change("exploded")), null);
});

test("lastChangeText is null when the date or the actor is not a string", () => {
  // new Date(null) is 1 Jan 1970 and a missing actor would read "by undefined".
  assert.equal(lastChangeText(change("activated", "Preview Admin", null)), null);
  assert.equal(lastChangeText(change("activated", "Preview Admin", 1761000000000)), null);
  assert.equal(lastChangeText({ action: "activated", at: AT }), null);
  assert.equal(lastChangeText(change("activated", null)), null);
  assert.equal(lastChangeText(change("activated", 42)), null);
  assert.equal(lastChangeText(change("activated", "")), null);
});

test("lastChangeText dates follow the pinned zone, not the UTC calendar day by accident", () => {
  assert.equal(
    lastChangeText(change("activated", "Preview Admin", "2026-10-20T23:59:00Z")),
    "Activated 20 Oct 2026 by Preview Admin",
  );
});

test("lastChangeText returns an actor with markup characters unchanged (React escapes it)", () => {
  const actor = `<img src=x onerror="alert(1)"> & "Co"`;
  assert.equal(lastChangeText(change("activated", actor)), `Activated 20 Oct 2026 by ${actor}`);
});

test("shortDate gives the same short date as the last-changed line, or null", () => {
  assert.equal(shortDate("2026-10-20T08:30:00Z"), "20 Oct 2026");
  for (const bad of [null, undefined, "", "not a date", 0, {}]) assert.equal(shortDate(bad), null, String(bad));
});
