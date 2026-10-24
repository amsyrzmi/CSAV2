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
const source = await readFile(new URL("../lib/email-api.ts", import.meta.url), "utf8");
const { emailClient } = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));

test("email writes keep one key per exact operation, bind the tenant and never carry identity headers", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    if (requests.length === 1) throw new Error("synthetic lost acknowledgement");
    return Response.json({ id: "draft-1", status: "draft" });
  };
  try {
    const client = emailClient("synthetic-tenant");
    await assert.rejects(client.draft("t1", "Say thanks", "auto"), /lost acknowledgement/);
    assert.equal(requests.length, 1, "never retry an uncertain write automatically");
    await client.draft("t1", "Say thanks", "auto");
    assert.equal(requests[0][1].headers.get("Idempotency-Key"), requests[1][1].headers.get("Idempotency-Key"));
    assert.equal(requests[0][1].body, requests[1][1].body);
    await client.draft("t1", "Say thanks in Malay", "ms");
    assert.notEqual(requests[1][1].headers.get("Idempotency-Key"), requests[2][1].headers.get("Idempotency-Key"));
    await client.sync();
    assert.equal(requests[3][1].body, undefined, "body-less writes send no JSON body");
    assert.equal(requests[3][1].headers.has("Content-Type"), false);
    await emailClient("other-tenant").sync();
    assert.match(requests[4][0], /client_id=other-tenant/);
    assert.notEqual(requests[3][1].headers.get("Idempotency-Key"), requests[4][1].headers.get("Idempotency-Key"));
    for (const [path, init] of requests) {
      assert.match(path, /^\/console\/v1\/email\//);
      assert.equal(init.credentials, "same-origin");
      assert.equal(init.cache, "no-store");
      assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
      for (const header of ["Authorization", "X-Role", "X-Employee-Id", "X-Client-Owner", "X-HR-Role"])
        assert.equal(init.headers.has(header), false);
    }
  } finally { globalThis.fetch = original; }
});

test("thread ids are URL-encoded and reads carry no idempotency key", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => { requests.push([path, init]); return Response.json({ items: [] }); };
  try {
    const client = emailClient("");
    await client.thread("a/b c");
    assert.equal(requests[0][0], "/console/v1/email/threads/a%2Fb%20c");
    assert.equal(requests[0][1].headers.has("Idempotency-Key"), false);
    await client.threads();
    assert.equal(requests[1][0], "/console/v1/email/threads");
  } finally { globalThis.fetch = original; }
});
