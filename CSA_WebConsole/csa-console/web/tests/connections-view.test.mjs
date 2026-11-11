import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

async function load(name, replacements = {}) {
  let source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(replacements)) source = source.replaceAll(from, to);
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const {
  cardGate, arrivals, connectionsHref, tiktokView, tiktokLineWord, calendarView, gmailView, gmailNote, ARRIVAL_KEYS, ROLE_CANNOT, LOCKED,
  MANAGE_LINK,
} = await import(await load("connections-view.ts", {
  '"@/lib/business-view"': JSON.stringify(await load("business-view.ts", {
    '"@/lib/date-range"': JSON.stringify(await load("date-range.ts")),
    '"@/lib/tiktok-connect"': JSON.stringify(await load("tiktok-connect.ts")),
  })),
  '"@/lib/tiktok-connect"': JSON.stringify(await load("tiktok-connect.ts")),
  '"@/lib/bookings-view"': JSON.stringify(await load("bookings-view.ts")),
  '"@/lib/email-recipients"': JSON.stringify(await load("email-recipients.ts")),
}));
const { connectResult } = await import(await load("tiktok-connect.ts"));
const { calendarResult } = await import(await load("bookings-view.ts"));

const on = (...keys) => keys.map((key) => ({ key, status: "active" }));
const PACKAGE = { tiktok: "marketplaces", calendar: "bookings", gmail: "staff_email" };
const STAFF_NOTE = "Only an Admin can connect or disconnect a shop.";

test("the fixed copy is the agreed copy", () => {
  assert.equal(ROLE_CANNOT, "Your role can't connect this.");
  assert.equal(MANAGE_LINK, "Manage in Connections");
  assert.deepEqual(LOCKED, {
    tiktok: "Included in the Marketplaces add-on.",
    calendar: "Included in the Bookings package.",
    gmail: "Included in the Staff email package.",
  });
  assert.deepEqual([...ARRIVAL_KEYS], ["tiktok", "reason", "calendar", "calendar_error", "connected", "error"]);
});

test("cardGate for every kind, role and package state", () => {
  // [kind, role, packages, expected]
  const open = { show: true, locked: null, note: null };
  const table = [
    // Platform admin: only the business card; Google cards are hidden.
    ["tiktok", "platform_admin", on("marketplaces"), { ...open, canAct: true, readStatus: true }],
    ["calendar", "platform_admin", on("bookings"), { show: false, locked: null, canAct: false, readStatus: false, note: null }],
    ["gmail", "platform_admin", on("staff_email"), { show: false, locked: null, canAct: false, readStatus: false, note: null }],
    // Admin acts on everything.
    ["tiktok", "tenant_admin", on("marketplaces"), { ...open, canAct: true, readStatus: true }],
    ["calendar", "tenant_admin", on("bookings"), { ...open, canAct: true, readStatus: true }],
    ["gmail", "tenant_admin", on("staff_email"), { ...open, canAct: true, readStatus: true }],
    // Staff reads TikTok but cannot act on it; acts on the Google rows.
    ["tiktok", "tenant_staff", on("marketplaces"), { show: true, locked: null, canAct: false, readStatus: true, note: STAFF_NOTE }],
    ["calendar", "tenant_staff", on("bookings"), { ...open, canAct: true, readStatus: true }],
    ["gmail", "tenant_staff", on("staff_email"), { ...open, canAct: true, readStatus: true }],
    // Read-only: status is not read, the button is disabled, the role note shows.
    ["tiktok", "read_only", on("marketplaces"), { show: true, locked: null, canAct: false, readStatus: false, note: ROLE_CANNOT }],
    ["calendar", "read_only", on("bookings"), { show: true, locked: null, canAct: false, readStatus: false, note: ROLE_CANNOT }],
    ["gmail", "read_only", on("staff_email"), { show: true, locked: null, canAct: false, readStatus: false, note: ROLE_CANNOT }],
  ];
  for (const [kind, role, packages, expected] of table) {
    assert.deepEqual(cardGate(kind, role, packages), expected, `${kind} / ${role}`);
  }
  // Package off, absent or suspended: the sentence, no status read, no action, no role note.
  for (const kind of ["tiktok", "calendar", "gmail"]) {
    for (const role of ["tenant_admin", "tenant_staff", "read_only"]) {
      for (const packages of [[], on("hr"), [{ key: PACKAGE[kind], status: "suspended" }],
        [{ key: PACKAGE[kind], status: "requested" }], [{ key: PACKAGE[kind], status: "available" }]]) {
        assert.deepEqual(cardGate(kind, role, packages),
          { show: true, locked: LOCKED[kind], canAct: false, readStatus: false, note: null },
          `${kind} / ${role} / ${JSON.stringify(packages)}`);
      }
    }
  }
  assert.deepEqual(cardGate("tiktok", "platform_admin", []),
    { show: true, locked: LOCKED.tiktok, canAct: false, readStatus: false, note: null });
  // The package read failed (null): drawn unlocked, the server's own gates still refuse.
  assert.equal(cardGate("tiktok", "tenant_admin", null).locked, null);
  assert.equal(cardGate("calendar", "tenant_staff", null).canAct, true);
  assert.equal(cardGate("gmail", "read_only", null).readStatus, false);
  // A different package does not unlock the card.
  assert.equal(cardGate("tiktok", "tenant_admin", on("orders_products")).locked, LOCKED.tiktok);
});

test("arrivals reads each outcome with the existing sentences", () => {
  const read = (query) => arrivals(new URLSearchParams(query));
  assert.deepEqual(read(""), []);
  assert.deepEqual(read("client_id=t1"), []);

  const failed = read("tiktok=failed&reason=multiple_shops&client_id=t1");
  assert.deepEqual(failed, [{
    kind: "tiktok", tone: "bad",
    text: connectResult(new URLSearchParams("tiktok=failed&reason=multiple_shops")).text,
  }]);
  assert.match(failed[0].text, /more than one shop/);
  const tiktokOk = read("tiktok=connected");
  assert.equal(tiktokOk[0].tone, "good");
  assert.equal(tiktokOk[0].text, "TikTok Shop is connected. Orders and products will start syncing shortly.");

  const calendar = read("calendar_error=scopes_not_granted");
  assert.deepEqual(calendar, [{
    kind: "calendar", tone: "bad",
    text: calendarResult(new URLSearchParams("calendar_error=scopes_not_granted")).text,
  }]);
  assert.match(calendar[0].text, /^Google didn't allow access to the calendar/);
  assert.deepEqual(read("calendar=connected"), [{
    kind: "calendar", tone: "good",
    text: calendarResult(new URLSearchParams("calendar=connected")).text,
  }]);

  assert.deepEqual(read("connected=1"), [{ kind: "gmail", tone: "good", text: "Mailbox connected." }]);
  const state = read("error=oauth_state_invalid");
  assert.equal(state.length, 1);
  assert.equal(state[0].kind, "gmail");
  assert.equal(state[0].tone, "bad");
  assert.notEqual(state[0].text, "Gmail could not be connected. Try again.");
  assert.deepEqual(read("error=something_new"), [{
    kind: "gmail", tone: "bad", text: "Gmail could not be connected. Try again.",
  }]);
  // An empty error is not an outcome.
  assert.deepEqual(read("error="), []);

  // Two outcomes at once keep the card order: TikTok, Calendar, Gmail.
  assert.deepEqual(read("connected=1&calendar=connected&tiktok=connected").map((a) => a.kind),
    ["tiktok", "calendar", "gmail"]);
});

test("connectionsHref keeps a platform admin's company and sends members to the portal", () => {
  assert.equal(connectionsHref(false, null), "/portal/connections");
  assert.equal(connectionsHref(false, "t1"), "/portal/connections");
  assert.equal(connectionsHref(true, "t&x"), "/connections?client_id=t%26x");
  assert.equal(connectionsHref(true, null), "/connections");
  assert.equal(connectionsHref(true, ""), "/connections");
});

test("the Connections pages are adopted and need a session", async () => {
  const require = createRequire(import.meta.url);
  const compile = async (name) => {
    const out = {};
    const code = ts.transpileModule(await readFile(new URL(`../${name}`, import.meta.url), "utf8"), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText;
    return [out, code];
  };
  const [pathExports, pathsCode] = await compile("lib/hr-paths.ts");
  new Function("exports", pathsCode)(pathExports);
  const [trainingExports, trainingCode] = await compile("lib/training-view.ts");
  new Function("exports", trainingCode)(trainingExports);
  const [exports, proxyCode] = await compile("proxy.ts");
  new Function("require", "exports", proxyCode)((name) => (name === "./lib/hr-paths" ? pathExports
    : name === "./lib/training-view" ? trainingExports : require(name)), exports);
  const { NextRequest } = require("next/server");
  for (const path of ["/connections", "/portal/connections"]) {
    assert.equal(exports.proxy(new NextRequest(`https://example.test${path}`)).status, 307, path);
    assert.equal(exports.proxy(new NextRequest(`https://example.test${path}`, {
      headers: { Cookie: "csa_session=synthetic-session" },
    })).status, 200, path);
  }
  assert.equal(exports.proxy(new NextRequest("https://example.test/connectionsx", {
    headers: { Cookie: "csa_session=synthetic-session" },
  })).status, 404);
});

test("tiktokView for every stored status and for no shop", () => {
  const now = new Date("2026-11-02T02:00:00Z");
  const store = (status, sync = { orders_synced_at: "2026-11-02T01:57:00Z", products_synced_at: null, problem: null }) => ({
    id: "s1", platform: "tiktok_shop", shop_id: "SHOP-A", expires_at: null, updated_at: "2026-10-03T00:00:00Z",
    expiry: "unknown", authorization: "verified", permissions: "granted", provider_reads: "available",
    connection: { status, live_integration: "available" }, sync, can_manage: true,
  });
  assert.deepEqual(tiktokView(null, now), {
    tone: "neutral", word: "Not connected",
    sentence: "Connect your TikTok Shop to sync its orders and products.", lastActivity: null,
  });
  const reconnect = "Syncing has stopped. Reconnect to start it again.";
  const expected = {
    unsupported: ["neutral", "Not supported yet", "Syncing isn't available for this marketplace yet."],
    unverified: ["neutral", "Not verified", "Reconnect to check this shop with TikTok."],
    revoked: ["attention", "Needs reconnecting", reconnect],
    disconnected: ["neutral", "Disconnected", "Syncing is off. Customer details are removed 30 days after disconnecting."],
    expired: ["attention", "Needs reconnecting", reconnect],
    app_unavailable: ["attention", "Needs reconnecting", reconnect],
    binding_missing: ["attention", "Needs reconnecting", reconnect],
    permissions_missing: ["attention", "Needs reconnecting",
      "TikTok didn't give us all the access we need. Reconnect and allow every permission."],
    authorization_recorded: ["success", "Connected", "Orders and products sync from this shop."],
  };
  for (const [status, [tone, word, sentence]] of Object.entries(expected)) {
    assert.deepEqual(tiktokView(store(status), now),
      { tone, word, sentence, lastActivity: "Synced 3 min ago" }, status);
  }
  // Never synced, or no sync block at all.
  assert.equal(tiktokView(store("authorization_recorded", { orders_synced_at: null, products_synced_at: null, problem: null }), now)
    .lastActivity, "Not synced yet");
  assert.equal(tiktokView(store("authorization_recorded", null), now).lastActivity, "Not synced yet");
  // A pending store has nothing synced and says so by having no last activity.
  assert.deepEqual(tiktokView(store("missing_shop_id"), now), {
    tone: "attention", word: "Waiting for TikTok approval",
    sentence: "Finish approving the connection on TikTok.", lastActivity: null,
  });
});

test("calendarView for every state, off and not linked", () => {
  const history = "Your confirmed bookings appear on a calendar called Bookings in your own Google account. "
    + "Only that calendar is touched, and changing an event in Google does not change the booking.";
  const status = (state, extra = {}) => ({ available: true, can_connect: true, status: state, people: null, ...extra });
  assert.deepEqual(calendarView(status("not_connected")),
    { tone: "neutral", word: "Not connected", sentence: history, lastActivity: null });
  assert.deepEqual(calendarView(status("connected")),
    { tone: "success", word: "Connected", sentence: history, lastActivity: null });
  assert.deepEqual(calendarView(status("needs_reconnect")), {
    tone: "attention", word: "Needs reconnecting", lastActivity: null,
    sentence: "Google stopped accepting the connection, so new bookings aren't reaching your calendar. "
      + "Reconnect to start them again.",
  });
  const off = {
    tone: "neutral", word: "Not switched on", lastActivity: null,
    sentence: "Google Calendar isn't switched on for this company yet. Ask your Admin.",
  };
  assert.deepEqual(calendarView(status(null, { available: false, can_connect: false })), off);
  assert.deepEqual(calendarView(null), off);
  assert.deepEqual(calendarView(status(null, { can_connect: false })), {
    tone: "neutral", word: "Not available", lastActivity: null,
    sentence: "Your account isn't linked to a person in Bookings yet. Ask your Admin to add you on People & rooms.",
  });
});

test("gmailView for every mailbox state, switched off and no provider", () => {
  const context = (mailbox, extra = {}) => ({
    enabled: true, can_manage: false, can_connect: true, provider_configured: true, model_configured: true,
    mailbox, disclosure: "", last_synced_at: null, more_available: false, ...extra,
  });
  const box = (status, extra = {}) => ({ status, address: "ada@example.test", last_sync_at: null, ...extra });
  assert.deepEqual(gmailView(context(null, { enabled: false })), {
    tone: "neutral", word: "Switched off", lastActivity: null,
    sentence: "The email assistant is switched off for your company.",
  });
  assert.deepEqual(gmailView(context(null)), {
    tone: "neutral", word: "Not connected", lastActivity: null, sentence: "No mailbox connected for your account.",
  });
  assert.deepEqual(gmailView(context(box("connected"))), {
    tone: "success", word: "Connected", lastActivity: "Not checked yet.",
    sentence: "Your Gmail ada@example.test is connected to the Email assistant.",
  });
  // The mailbox's own last sync wins; otherwise the thread list's.
  assert.match(gmailView(context(box("connected", { last_sync_at: "2026-11-02T01:57:00Z" }))).lastActivity,
    /^Last checked .+\.$/);
  assert.match(gmailView(context(box("connected"), { last_synced_at: "2026-11-02T01:57:00Z" })).lastActivity,
    /^Last checked .+\.$/);
  assert.deepEqual(gmailView(context(box("pending", { address: null }))), {
    tone: "attention", word: "Not finished", lastActivity: "Not checked yet.",
    sentence: "Gmail has not finished connecting.",
  });
  assert.deepEqual(gmailView(context(box("revoked"))), {
    tone: "error", word: "Access removed", lastActivity: "Not checked yet.",
    sentence: "Gmail access was removed, so new email will not arrive.",
  });
  assert.equal(gmailView(null).word, "Not connected");
  // The Email page's own fallback: a status it has no word for reads Not connected, in a neutral tone.
  assert.deepEqual(gmailView(context(box("something_new"))), {
    tone: "neutral", word: "Not connected", lastActivity: null, sentence: "No mailbox connected for your account.",
  });
  // No Google client on this server: the existing sentence goes under a disabled Connect.
  assert.equal(gmailNote(context(null, { provider_configured: false })),
    "Connecting Gmail isn't available yet. Contact fourstacks support.");
  assert.equal(gmailNote(context(null)), null);
  assert.equal(gmailNote(null), null);
});

test("tiktokLineWord names the state of each TikTok shop for the old Store connections page", () => {
  const store = (status, platform = "tiktok_shop") => ({ platform, connection: { status } });
  assert.equal(tiktokLineWord([]), "Not connected");
  assert.equal(tiktokLineWord([store("authorization_recorded")]), "Connected");
  assert.equal(tiktokLineWord([store("expired")]), "Needs reconnecting");
  assert.equal(tiktokLineWord([store("authorization_recorded"), store("expired")]), "Connected, Needs reconnecting");
  assert.equal(tiktokLineWord([store("expired"), store("expired")]), "Needs reconnecting");
  // Another marketplace's store is not a TikTok shop.
  assert.equal(tiktokLineWord([store("authorization_recorded", "shopee")]), "Not connected");
});
