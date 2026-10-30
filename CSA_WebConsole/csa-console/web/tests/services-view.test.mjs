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
const {
  serviceStats, rowAvatarState, statusLabel, agentCountLabel, lastChangeText, shortDate, statusLine,
  SAVED_NOT_REFRESHED, buildText, setupText, connectionText,
} = view;

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
    ["tenant_switch_on", "Company switch turned on 20 Oct 2026 by Preview Admin"],
    ["tenant_switch_off", "Company switch turned off 20 Oct 2026 by Preview Admin"],
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

// The line under an active or requested row's summary. When the last-change line already says
// when and by whom, the row must not say it a second time; only the extra facts remain.
const row = (status, extra = {}) => ({
  key: "subscriptions", status, sellable: true, agents: [], reference: null,
  requested_at: "2026-10-25T00:00:00Z", activated_at: "2026-10-26T00:00:00Z", last_change: null, ...extra,
});

test("statusLine keeps the full sentence when there is no last change", () => {
  assert.equal(statusLine(row("requested")), "Requested 25 Oct 2026; awaiting activation.");
  assert.equal(
    statusLine(row("active", { reference: "Q-9" })),
    "Activated 26 Oct 2026 by a platform administrator. Reference: Q-9.",
  );
  assert.equal(
    statusLine(row("active", { activated_at: null })), "Activated by a platform administrator.",
  );
  assert.equal(statusLine(row("requested", { requested_at: null })), "Requested; awaiting activation.");
});

test("statusLine drops what the last-change line already says", () => {
  assert.equal(
    statusLine(row("active", { reference: "Q-9", last_change: change("activated") })), "Reference: Q-9.",
  );
  assert.equal(statusLine(row("active", { last_change: change("activated") })), null);
  assert.equal(statusLine(row("requested", { last_change: change("requested") })), "Awaiting activation.");
});

test("statusLine keeps the full sentence when the last change is about something else", () => {
  // The newest change is the request, but the row is active: the activation is not told yet.
  assert.equal(
    statusLine(row("active", { reference: "Q-9", last_change: change("requested") })),
    "Activated 26 Oct 2026 by a platform administrator. Reference: Q-9.",
  );
  assert.equal(
    statusLine(row("requested", { last_change: change("suspended") })),
    "Requested 25 Oct 2026; awaiting activation.",
  );
  // A change that cannot be shown (bad date) does not count as told either.
  assert.equal(
    statusLine(row("requested", { last_change: change("requested", "x", "not a date") })),
    "Requested 25 Oct 2026; awaiting activation.",
  );
});

test("statusLine is null for available and suspended rows", () => {
  for (const status of ["available", "suspended"]) assert.equal(statusLine(row(status)), null, status);
});

test("the saved-but-stale message does not claim a failure", () => {
  assert.equal(SAVED_NOT_REFRESHED, "Saved, but the list could not refresh. Reload to see the change.");
});

test("the agent card words build, setup and connection in plain language, never as raw codes", () => {
  assert.equal(buildText("local"), "Built");
  assert.equal(buildText("prepared_only"), "Prepared, not live yet");
  assert.equal(buildText("not_implemented"), "Not built yet");
  assert.equal(setupText("available"), "Ready");
  assert.equal(setupText("setup_required"), "Setup needed");
  assert.equal(setupText("configured_unverified"), "Set up, not yet checked");
  assert.equal(setupText("reindex_required"), "Knowledge base needs re-indexing");
  assert.equal(connectionText("closed"), "No recent failures");
  assert.equal(connectionText("open"), "Paused after repeated failures");
  assert.equal(connectionText("half_open"), "Testing again");
  // A code the table does not know still reads as words, not as an underscored identifier.
  assert.equal(connectionText("some_new_state"), "some new state");
});
