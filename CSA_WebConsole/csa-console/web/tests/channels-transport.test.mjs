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

const SECRET = "EAAG-synthetic-access-token-0123456789";
const BODY = { phone_number_id: "109876543210", waba_id: "20001234567", access_token: SECRET };

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
    await client.getWhatsAppNumber(null);
    await client.getWhatsAppNumber("a b/1");
  });
  assert.deepEqual(requests.map(([path, init]) => [init.method ?? "GET", path]), [
    ["GET", "/console/v1/channels/whatsapp"],
    ["GET", "/console/v1/channels/whatsapp?client_id=a%20b%2F1"],
  ]);
  for (const [, init] of requests) assert.equal(init.credentials, "same-origin");
});

test("connect sends PUT with the CSRF header and never logs the token", async () => {
  const [, requests, logs] = await capture(() => client.connectWhatsAppNumber("t-1", BODY));
  const [path, init] = requests[0];
  assert.equal(path, "/console/v1/channels/whatsapp?client_id=t-1");
  assert.equal(init.method, "PUT");
  assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  assert.deepEqual(JSON.parse(init.body), BODY);
  assert.ok(!logs.join("\n").includes(SECRET));
});

test("disconnect sends DELETE with the CSRF header", async () => {
  const [result, requests] = await capture(() => client.disconnectWhatsAppNumber(null),
    () => new Response(null, { status: 204 }));
  assert.equal(result, undefined);
  assert.equal(requests[0][1].method, "DELETE");
  assert.equal(requests[0][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
});

test("a number already in use is explained in plain words and keeps its code", async () => {
  await assert.rejects(
    capture(() => client.connectWhatsAppNumber(null, BODY),
      () => Response.json({ detail: "whatsapp_number_in_use" }, { status: 409 })),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, 409);
      assert.equal(error.code, "whatsapp_number_in_use");
      assert.equal(error.message, "That WhatsApp number is already connected to another company.");
      assert.ok(!error.message.includes(SECRET));
      return true;
    },
  );
});

const WINDOW = "WhatsApp only allows replies within 24 hours of the customer's last message. Wait for the customer to message again.";
const row = (overrides) => ({ delivery_status: "delivered", delivery_error: null,
  provider_status: null, provider_error: null, ...overrides });

test("a failed WhatsApp status shows not delivered instead of delivered", () => {
  const failed = row({ provider_status: "failed", provider_error: "131026" });
  assert.equal(client.deliveryLabel(failed), "not delivered");
  assert.equal(client.deliveryNotice(failed), "Not delivered. WhatsApp could not pass this reply to the customer.");
  assert.equal(client.deliveryLabel(row({ provider_status: "read" })), "delivered");
  assert.equal(client.deliveryLabel(row({ delivery_status: "pending" })), "queued");
  assert.equal(client.deliveryLabel(row({ delivery_status: null })), null);
  assert.equal(client.deliveryNotice(row({ provider_status: "read" })), null);
});

test("the 24-hour rule reads the same for a refused send and a late failed status", () => {
  assert.equal(client.deliveryNotice(row({ provider_status: "failed", provider_error: "131047" })), WINDOW);
  assert.equal(client.deliveryNotice(row({ delivery_status: "dead", delivery_error: "outside_service_window" })), WINDOW);
});

test("dead WhatsApp deliveries are explained in plain words without codes", () => {
  for (const code of ["recipient_not_on_whatsapp", "token_invalid", "whatsapp_endpoint_unavailable", "channel_not_configured"]) {
    const notice = client.deliveryNotice(row({ delivery_status: "dead", delivery_error: code }));
    assert.ok(notice && notice.startsWith("Not delivered."), code);
    assert.ok(!notice.includes("_"), notice);
  }
  assert.equal(client.deliveryNotice(row({ delivery_status: "dead", delivery_error: "telegram_http_400" })), null);
});

test("connect errors from the Graph check are explained in plain words", async () => {
  for (const [status, code] of [[422, "whatsapp_token_rejected"], [502, "whatsapp_unreachable"]]) {
    await assert.rejects(
      capture(() => client.connectWhatsAppNumber(null, BODY),
        () => Response.json({ detail: code }, { status })),
      (error) => {
        assert.equal(error.code, code);
        assert.ok(!error.message.includes("_"), error.message);
        assert.ok(!error.message.includes(SECRET));
        return true;
      },
    );
  }
});
