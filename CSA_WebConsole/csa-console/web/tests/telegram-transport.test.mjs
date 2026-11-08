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
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const source = await readFile(new URL("../lib/channels-api.ts", import.meta.url), "utf8");
const client = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { ApiError } = await import(apiUrl);

const TOKEN = "123456789:synthetic_Token-0123456789_abcdefghij";
const BASE = "/console/v1/channels/telegram";

async function capture(run, respond) {
  const original = globalThis.fetch;
  const originalLog = console.log;
  const logs = [];
  console.log = (...args) => logs.push(args.join(" "));
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return respond ? respond(path, init) : Response.json({ connected: true });
  };
  try { return [await run(), requests, logs]; } finally {
    globalThis.fetch = original;
    console.log = originalLog;
  }
}

test("a member reads without client_id and a platform admin appends it", async () => {
  const [, requests] = await capture(async () => {
    await client.getTelegram(null);
    await client.getTelegram("a b/1");
  });
  assert.deepEqual(requests.map(([path, init]) => [init.method ?? "GET", path]), [
    ["GET", BASE],
    ["GET", `${BASE}?client_id=a%20b%2F1`],
  ]);
  for (const [, init] of requests) assert.equal(init.credentials, "same-origin");
});

test("connect sends the token only in the PUT body, with the CSRF header, and never logs it", async () => {
  const [, requests, logs] = await capture(() => client.connectTelegram("t-1", { bot_token: TOKEN }));
  const [path, init] = requests[0];
  assert.equal(path, `${BASE}?client_id=t-1`);
  assert.equal(init.method, "PUT");
  assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  assert.deepEqual(JSON.parse(init.body), { bot_token: TOKEN });
  assert.ok(!path.includes(TOKEN) && !JSON.stringify([...init.headers]).includes(TOKEN));
  assert.ok(!logs.join("\n").includes(TOKEN));
});

test("connect carries the staff chat and the replace choice only when they are set", async () => {
  const [, requests] = await capture(async () => {
    await client.connectTelegram(null, { bot_token: TOKEN, staff_chat_id: "-100123", replace_webhook: true });
    await client.connectTelegram(null, { bot_token: TOKEN, replace_webhook: false });
  });
  assert.deepEqual(JSON.parse(requests[0][1].body),
    { bot_token: TOKEN, staff_chat_id: "-100123", replace_webhook: true });
  assert.deepEqual(JSON.parse(requests[1][1].body), { bot_token: TOKEN });
});

test("staff chat, check and disconnect use their own routes and the CSRF header", async () => {
  const [, requests] = await capture(async () => {
    await client.setTelegramStaffChat("t-1", "-100123");
    await client.checkTelegram("t-1");
    await client.disconnectTelegram("t-1");
  }, (path, init) => init.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({}));
  assert.deepEqual(requests.map(([path, init]) => [init.method, path]), [
    ["PUT", `${BASE}/staff-chat?client_id=t-1`],
    ["POST", `${BASE}/check?client_id=t-1`],
    ["DELETE", `${BASE}?client_id=t-1`],
  ]);
  assert.deepEqual(JSON.parse(requests[0][1].body), { chat_id: "-100123" });
  for (const [, init] of requests) assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
});

const CODES = [
  [422, "telegram_token_malformed"], [422, "telegram_token_rejected"], [502, "telegram_unreachable"],
  [503, "hooks_origin_not_configured"], [409, "telegram_bot_in_use"], [409, "telegram_bot_replace"],
  [409, "telegram_webhook_in_use"], [409, "telegram_connect_in_progress"],
  [502, "telegram_webhook_failed"], [409, "telegram_not_connected"], [422, "telegram_chat_invalid"],
  [422, "telegram_chat_unreachable"], [422, "telegram_chat_forbidden"],
  [503, "telegram_credential_unavailable"], [403, "platform_admin_only"],
];

test("every Telegram refusal is a plain sentence of its own and keeps its code", async () => {
  const seen = new Set();
  for (const [status, code] of CODES) {
    await assert.rejects(
      capture(() => client.connectTelegram(null, { bot_token: TOKEN }),
        () => Response.json({ detail: code }, { status })),
      (error) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, status);
        assert.equal(error.code, code);
        assert.notEqual(error.message, code, code);
        assert.ok(error.message.length > 10 && !error.message.includes("_"), error.message);
        assert.ok(!error.message.includes(TOKEN));
        assert.ok(!seen.has(error.message), `duplicate sentence for ${code}`);
        seen.add(error.message);
        return true;
      },
    );
  }
});
