import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(name) {
  const source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const {
  connectResult, syncLine, isAuthorizeUrl, navigate, connectErrorText, TIKTOK_AUTHORIZE_HOSTS,
} = await import(await load("tiktok-connect.ts"));

const params = (text) => new URLSearchParams(text);

test("connectResult says one plain sentence for every reason code", () => {
  const expected = {
    tiktok_unavailable: "TikTok Shop isn't set up on this server yet.",
    provider_denied: "TikTok said the connection wasn't approved. Nothing was changed.",
    state_invalid: "That connection link has expired or was already used. Start again with Connect TikTok Shop.",
    multiple_shops: "This TikTok account has more than one shop. Contact support to connect the right one.",
    no_authorized_shop: "TikTok didn't share any shop with us. Connect again and choose your shop.",
    shop_in_use: "That TikTok shop is already connected. If it is yours, use Reconnect on its card.",
    insufficient_scope: "TikTok didn't give us all the access we need. Connect again and allow every permission.",
    connect_failed:
      "We couldn't confirm the shop you approved. If you chose a different shop than before, connect the original one.",
  };
  for (const [reason, text] of Object.entries(expected)) {
    assert.deepEqual(connectResult(params(`tiktok=failed&reason=${reason}`)), { tone: "bad", text }, reason);
    assert.doesNotMatch(text, /_|tenant admin/i, reason);
  }
});

test("connectResult: connected is good, an unknown reason falls back to the general sentence", () => {
  const good = connectResult(params("tiktok=connected"));
  assert.equal(good?.tone, "good");
  assert.match(good.text, /^TikTok Shop is connected\./);
  const unknown = connectResult(params("tiktok=failed&reason=something_new"));
  assert.equal(unknown?.tone, "bad");
  assert.equal(unknown.text, connectResult(params("tiktok=failed&reason=connect_failed")).text);
  assert.equal(connectResult(params("tiktok=failed")).tone, "bad");
});

test("connectResult is null without a TikTok answer and keeps other parameters out of it", () => {
  assert.equal(connectResult(params("")), null);
  assert.equal(connectResult(params("client_id=abc")), null);
  assert.equal(connectResult(params("tiktok=maybe")), null);
  assert.equal(connectResult(params("reason=multiple_shops")), null);
  assert.equal(connectResult(params("client_id=abc&tiktok=connected"))?.tone, "good");
});

test("syncLine reads minutes, hours, days and never", () => {
  const now = new Date("2026-11-04T10:00:00Z");
  const ago = (ms) => new Date(now.getTime() - ms).toISOString();
  assert.equal(syncLine("Orders", ago(3 * 60_000), now), "Orders synced 3 min ago");
  assert.equal(syncLine("Products", ago(60 * 60_000), now), "Products synced 1 h ago");
  assert.equal(syncLine("Products", ago(5 * 3_600_000 + 40 * 60_000), now), "Products synced 5 h ago");
  assert.equal(syncLine("Orders", ago(2 * 86_400_000), now), "Orders synced 2 d ago");
  assert.equal(syncLine("Orders", ago(20_000), now), "Orders synced just now");
  assert.equal(syncLine("Orders", ago(-60_000), now), "Orders synced just now", "a clock a little ahead");
  assert.equal(syncLine("Orders", null, now), "Orders not synced yet");
  assert.equal(syncLine("Products", "not a time", now), "Products sync time not recorded");
  // With no label it is the sentence for one record.
  assert.equal(syncLine(null, ago(3 * 60_000), now), "Synced 3 min ago");
  assert.equal(syncLine(null, ago(3 * 3_600_000), now), "Synced 3 h ago");
  assert.equal(syncLine(null, ago(20_000), now), "Synced just now");
  assert.equal(syncLine(null, null, now), "Not synced yet");
  assert.equal(syncLine(null, "not a time", now), "Sync time not recorded");
});

test("only an https URL on the TikTok authorize host is followed", () => {
  assert.deepEqual([...TIKTOK_AUTHORIZE_HOSTS], ["services.tiktokshop.com"]);
  const ok = "https://services.tiktokshop.com/open/authorize?service_id=SVC&state=abc";
  assert.equal(isAuthorizeUrl(ok), true);
  for (const bad of [
    "http://services.tiktokshop.com/open/authorize?x=1",
    "https://evil.example/open/authorize",
    "https://services.tiktokshop.com.evil.example/open/authorize",
    "https://user@evil.example/",
    "https://evil.example/?u=services.tiktokshop.com",
    "javascript:alert(1)",
    "data:text/html,hi",
    "//services.tiktokshop.com/open/authorize",
    "/portal/business/stores",
    "",
    "not a url",
    null,
    undefined,
    42,
  ]) assert.equal(isAuthorizeUrl(bad), false, String(bad));
});

test("navigate hands only a checked URL to the injected assign", () => {
  const seen = [];
  const assign = (url) => seen.push(url);
  const ok = "https://services.tiktokshop.com/open/authorize?service_id=SVC&state=abc";
  assert.equal(navigate(ok, assign), true);
  assert.equal(navigate("javascript:alert(1)", assign), false);
  assert.equal(navigate("http://services.tiktokshop.com/open/authorize", assign), false);
  assert.equal(navigate("https://evil.example/", assign), false);
  assert.deepEqual(seen, [ok]);
});

test("connectErrorText explains the unset-up server and keeps other messages", () => {
  assert.equal(connectErrorText("tiktok_unavailable", "x"), "TikTok Shop isn't set up on this server yet.");
  assert.equal(connectErrorText("store_admin_required", "x"), "Only an Admin can connect or disconnect a shop.");
  assert.equal(connectErrorText("store_not_found", "x"), "That shop is no longer here. Refresh the page.");
  assert.match(connectErrorText("store_changed", "x"), /^The shop changed while you were connecting it\./);
  assert.equal(connectErrorText("not_available", "x"), "Disconnecting isn't available on this server yet.");
  assert.equal(connectErrorText("other", "Request failed (409)"), "Request failed (409)");
});
