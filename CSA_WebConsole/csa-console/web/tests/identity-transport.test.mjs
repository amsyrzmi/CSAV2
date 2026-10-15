import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

const apiSource = await readFile(
  new URL("../lib/api.ts", import.meta.url),
  "utf8",
);
const apiCode = ts.transpileModule(apiSource, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ES2022,
  },
}).outputText;
const { api, post } = await import(
  `data:text/javascript;base64,${Buffer.from(apiCode).toString("base64")}`
);
const require = createRequire(import.meta.url);
const proxySource = await readFile(
  new URL("../proxy.ts", import.meta.url),
  "utf8",
);
const proxyCode = ts.transpileModule(proxySource, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
  },
}).outputText;
const proxyExports = {};
new Function("require", "exports", proxyCode)(require, proxyExports);
const { NextRequest } = require("next/server");

test("browser mutation obtains CSRF before sending same-origin cookie request and handles logout 204", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push([path, init]);
    return path.endsWith("/csrf")
      ? Response.json({ csrf_token: "synthetic-csrf" })
      : new Response(null, { status: 204 });
  };
  try {
    assert.equal(await post("/console/v1/auth/logout"), undefined);
    assert.equal(calls.length, 2);
    assert.equal(calls[0][0], "/console/v1/auth/csrf");
    assert.equal(calls[1][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
    assert.equal(calls[1][1].credentials, "same-origin");
    assert.equal(calls[1][1].cache, "no-store");
    assert.equal(calls[1][1].headers.has("Authorization"), false);
    assert.equal(calls[1][1].headers.has("X-Client-Id"), false);
  } finally {
    globalThis.fetch = original;
  }
});

test("failed CSRF request prevents mutation and preserves status", async () => {
  const original = globalThis.fetch;
  let count = 0;
  globalThis.fetch = async () => {
    count += 1;
    return Response.json({ detail: "identity unavailable" }, { status: 503 });
  };
  try {
    await assert.rejects(
      post("/console/v1/auth/login", {
        email: "synthetic@example.test",
        password: "unused",
      }),
      { status: 503, message: "identity unavailable" },
    );
    assert.equal(count, 1);
  } finally {
    globalThis.fetch = original;
  }
});

test("legacy API and external destinations cannot enter adopted browser transport", async () => {
  await assert.rejects(api("/api/auth/me"), /Only the CSAV2/);
  await assert.rejects(
    api("https://example.test/console/v1/auth/me"),
    /Only the CSAV2/,
  );
});

test("deferred UI and legacy API routes return 404 even with a session cookie", () => {
  for (const route of [
    "/projects",
    "/projects/synthetic/terminal",
    "/portal/projects",
    "/portal/shared-projects/synthetic",
    "/portal/chat",
    "/system",
    "/api/auth/me",
    "/invite/raw-token",
  ]) {
    const request = new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    });
    assert.equal(proxyExports.proxy(request).status, 404, route);
  }
});

test("session API reaches CSAV2 without optimistic UI redirect; protected UI redirects without cookie", () => {
  assert.equal(
    proxyExports.proxy(
      new NextRequest("http://console.example.test/console/v1/auth/login"),
    ).status,
    200,
  );
  const response = proxyExports.proxy(
    new NextRequest("http://console.example.test/portal"),
  );
  assert.equal(response.status, 307);
  assert.equal(
    response.headers.get("location"),
    "http://console.example.test/login",
  );
});
