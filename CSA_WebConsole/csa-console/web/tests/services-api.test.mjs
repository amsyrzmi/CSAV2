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
const source = await readFile(new URL("../lib/services-api.ts", import.meta.url), "utf8");
const { servicesClient } = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));

const IDENTITY = ["Authorization", "X-Role", "X-Employee-Id", "X-Client-Owner", "X-HR-Role"];

async function capture(run) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    if (path.includes("/active")) return Response.json({ packages: [{ key: "hr", status: "active" }] });
    return Response.json(path.endsWith("/services") || path.includes("/services?") ? [] : { key: "hr" });
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}

test("tenant members send no client_id and the active wrapper is unwrapped", async () => {
  const [active, requests] = await capture(async () => {
    const client = servicesClient(null);
    await client.list();
    await client.request("hr");
    await client.activate("hr", "PO-1", "ok");
    await client.suspend("hr");
    await client.suspend("hr", "paused");
    return client.active();
  });
  assert.deepEqual(active, [{ key: "hr", status: "active" }]);
  assert.deepEqual(requests.map(([path, init]) => [init.method ?? "GET", path]), [
    ["GET", "/console/v1/services"],
    ["POST", "/console/v1/services/hr/request"],
    ["POST", "/console/v1/services/hr/activate"],
    ["POST", "/console/v1/services/hr/suspend"],
    ["POST", "/console/v1/services/hr/suspend"],
    ["GET", "/console/v1/services/active"],
  ]);
  assert.deepEqual(JSON.parse(requests[2][1].body), { reference: "PO-1", note: "ok" });
  assert.deepEqual(JSON.parse(requests[3][1].body), { note: null });
  assert.deepEqual(JSON.parse(requests[4][1].body), { note: "paused" });
  for (const [, init] of requests) {
    assert.equal(init.credentials, "same-origin");
    assert.equal(init.cache, "no-store");
    for (const header of IDENTITY) assert.equal(init.headers.has(header), false);
    if (init.method === "POST") assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  }
});

test("platform admins append an encoded client_id on every call", async () => {
  const [, requests] = await capture(async () => {
    const client = servicesClient("a b/1");
    await client.list();
    await client.active();
    await client.request("hr");
    await client.activate("hr", "PO-1");
    await client.suspend("hr");
  });
  assert.deepEqual(requests.map(([path]) => path), [
    "/console/v1/services?client_id=a%20b%2F1",
    "/console/v1/services/active?client_id=a%20b%2F1",
    "/console/v1/services/hr/request?client_id=a%20b%2F1",
    "/console/v1/services/hr/activate?client_id=a%20b%2F1",
    "/console/v1/services/hr/suspend?client_id=a%20b%2F1",
  ]);
  assert.deepEqual(JSON.parse(requests[3][1].body), { reference: "PO-1", note: null });
  assert.equal(requests[2][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
});

test("package keys are URL-encoded", async () => {
  const [, requests] = await capture(() => servicesClient(null).request("a/b"));
  assert.equal(requests[0][0], "/console/v1/services/a%2Fb/request");
});
