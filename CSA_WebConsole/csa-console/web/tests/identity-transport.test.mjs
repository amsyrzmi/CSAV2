import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { parse } from "node:url";
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
const businessSource = await readFile(new URL("../lib/business.ts", import.meta.url), "utf8");
const businessCode = ts.transpileModule(businessSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { localOrderPath, connectionLabels, demoListings, appendDemoListing } = await import(
  `data:text/javascript;base64,${Buffer.from(businessCode).toString("base64")}`
);
const businessWorkspaceSource = await readFile(
  new URL("../components/console/BusinessWorkspace.tsx", import.meta.url),
  "utf8",
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
const hrPathsCode = ts.transpileModule(
  await readFile(new URL("../lib/hr-paths.ts", import.meta.url), "utf8"),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } },
).outputText;
const hrPathsExports = {};
new Function("exports", hrPathsCode)(hrPathsExports);
const trainingViewCode = ts.transpileModule(
  await readFile(new URL("../lib/training-view.ts", import.meta.url), "utf8"),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } },
).outputText;
const trainingViewExports = {};
new Function("exports", trainingViewCode)(trainingViewExports);
const proxyExports = {};
new Function("require", "exports", proxyCode)(
  (name) => (name === "./lib/hr-paths" ? hrPathsExports
    : name === "./lib/training-view" ? trainingViewExports : require(name)),
  proxyExports,
);
const { NextRequest } = require("next/server");

test("A1 staff workspace pages are adopted but require a session cookie", () => {
  for (const route of ["/workspace", "/portal/workspace"]) {
    const url = `https://console.example.test${route}`;
    assert.equal(proxyExports.proxy(new NextRequest(url)).status, 307, route);
    assert.equal(proxyExports.proxy(new NextRequest(url, {
      headers: { Cookie: "csa_session=synthetic" },
    })).status, 200, route);
  }
});

test("Subscriptions pages are adopted but require a session cookie", () => {
  for (const route of ["/subscriptions", "/portal/subscriptions"]) {
    const url = `https://console.example.test${route}`;
    assert.equal(proxyExports.proxy(new NextRequest(url)).status, 307, route);
    assert.equal(proxyExports.proxy(new NextRequest(url, {
      headers: { Cookie: "csa_session=synthetic" },
    })).status, 200, route);
  }
});

test("Training library pages are adopted but require a session cookie; other paths under them are not", () => {
  const item = "44444444-4444-4444-8444-444444444444";
  for (const route of ["/training", "/portal/training", `/training/${item}`, `/portal/training/${item}`]) {
    const url = `https://console.example.test${route}`;
    assert.equal(proxyExports.proxy(new NextRequest(url)).status, 307, route);
    assert.equal(proxyExports.proxy(new NextRequest(url, {
      headers: { Cookie: "csa_session=synthetic" },
    })).status, 200, route);
  }
  for (const route of ["/training/new", `/portal/training/${item}/edit`, "/trainings"]) {
    assert.equal(proxyExports.proxy(new NextRequest(`https://console.example.test${route}`, {
      headers: { Cookie: "csa_session=synthetic" },
    })).status, 404, route);
  }
});

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

test("mutation cancellation also bounds its preceding CSRF request", async () => {
  const original = globalThis.fetch;
  const controller = new AbortController();
  let calls = 0;
  globalThis.fetch = async (_path, init) => {
    calls++;
    assert.equal(init.signal, controller.signal);
    return new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
    });
  };
  try {
    const pending = api("/console/v1/chat/conversations", {
      method: "POST", body: "{}", signal: controller.signal,
    });
    controller.abort(new Error("synthetic request deadline"));
    await assert.rejects(pending, /synthetic request deadline/);
    assert.equal(calls, 1);
  } finally { globalThis.fetch = original; }
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
    "/system",
    "/api/auth/me",
    "/api/v1/channels/tiktok_shop/oauth/authorize",
    "/api/v1/channels/tiktok_shop/oauth/callback",
    "/api/v1/channels/tiktok_shop/webhook",
    "/api/v1/channels/tiktok_shop/webhook/synthetic",
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

test("M2 management and activity pages require a cookie and are explicitly adopted", () => {
  for (const route of ["/agents", "/activity", "/portal/agents", "/portal/activity"]) {
    const anonymous = new NextRequest(`http://console.example.test${route}`);
    assert.equal(proxyExports.proxy(anonymous).status, 307, route);
    const authenticated = new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    });
    assert.equal(proxyExports.proxy(authenticated).status, 200, route);
  }
  const deferredDetail = new NextRequest("http://console.example.test/agents/lhdn_agent", {
    headers: { cookie: "csa_session=synthetic" },
  });
  assert.equal(proxyExports.proxy(deferredDetail).status, 404);
});

test("multipart preserves file body, browser boundary, idempotency and CSRF", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push([path, init]);
    return path.endsWith("/csrf")
      ? Response.json({ csrf_token: "synthetic-csrf" })
      : Response.json({ job_id: "synthetic-job", duplicate_request: false }, { status: 202 });
  };
  try {
    const body = new FormData();
    body.append("files", new Blob(["Question,Answer\nQ,A\n"]), "faq.csv");
    await api("/console/v1/kb/uploads", {
      method: "POST", body, headers: { "Idempotency-Key": "synthetic-key" },
    });
    assert.equal(calls[1][1].body, body);
    assert.equal(calls[1][1].headers.has("Content-Type"), false);
    assert.equal(calls[1][1].headers.get("Idempotency-Key"), "synthetic-key");
    assert.equal(calls[1][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
    assert.equal(calls[1][1].credentials, "same-origin");
  } finally { globalThis.fetch = original; }
});

test("HR tab and People pages are adopted, anything else under HR stays deferred", () => {
  const profile = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
  for (const route of ["/hr/people", "/portal/hr/leave", "/portal/hr/people/org-chart", `/portal/hr/people/${profile}`]) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`)).status, 307, route);
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 200, route);
  }
  for (const route of ["/hr/onboarding", "/portal/hr/people/nope", "/portal/hrx", `/hr/people/${profile}/x`]) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 404, route);
  }
});

test("M3 KB screens are adopted but still require a session", () => {
  for (const route of ["/knowledge-base", "/portal/faq"]) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`)).status, 307);
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 200);
  }
});

test("sub-page routes require a session and unknown sub-paths stay hidden", () => {
  const pages = [
    "/portal/business/products", "/portal/business/stores", "/business/products", "/business/stores",
    "/portal/faq/add", "/portal/faq/questions", "/portal/faq/maintenance",
    "/knowledge-base/add", "/knowledge-base/questions", "/knowledge-base/maintenance",
  ];
  for (const route of pages) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`)).status, 307, route);
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 200, route);
  }
  for (const route of ["/portal/business/xyz", "/portal/faq/add/extra", "/business/products/1", "/portal/faq/library"]) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 404, route);
  }
});

test("M4 sandbox routes require sessions and retain the same request on uncertain retry", async () => {
  for (const route of ["/chat", "/portal/chat"]) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`)).status, 307);
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 200);
  }
  const original = globalThis.fetch;
  const calls = [];
  let sends = 0;
  globalThis.fetch = async (path, init) => {
    if (path.endsWith("/csrf")) return Response.json({ csrf_token: "synthetic-csrf" });
    calls.push([path, init]);
    if (++sends === 1) throw new TypeError("synthetic response loss after accept");
    return Response.json({ id: 1, trace_id: "synthetic-trace", duplicate: true }, { status: 202 });
  };
  try {
    const path = "/console/v1/chat/conversations/synthetic/turns?client_id=synthetic-tenant";
    const body = { request_key: "synthetic-key", body: "Synthetic product question" };
    await assert.rejects(post(path, body), /synthetic response loss/);
    assert.equal((await post(path, body)).duplicate, true);
    assert.equal(calls[0][0], calls[1][0]);
    assert.equal(calls[0][1].body, calls[1][1].body);
    assert.equal(calls[1][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
    assert.equal(calls[1][1].headers.has("Authorization"), false);
  } finally { globalThis.fetch = original; }
});

test("M5 inbox has distinct protected routes and retries an uncertain decision explicitly", async () => {
  for (const route of ["/inbox", "/portal/inbox"]) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`)).status, 307);
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 200);
  }
  const original = globalThis.fetch;
  const mutations = [];
  globalThis.fetch = async (path, init) => {
    if (path.endsWith("/csrf")) return Response.json({ csrf_token: "synthetic-csrf" });
    mutations.push([path, init]);
    if (mutations.length === 1) throw new TypeError("synthetic committed response loss");
    return Response.json({ outcome: "applied", queued: true, status: "edited", warnings: [] });
  };
  try {
    const path = "/console/v1/inbox/approvals/42/decide?client_id=synthetic-tenant";
    const payload = { request_key: "synthetic-key", decision: "edit", text: "Synthetic answer" };
    await assert.rejects(post(path, payload), /response loss/);
    assert.equal(mutations.length, 1, "ambiguous mutations must not retry automatically");
    assert.equal((await post(path, payload)).queued, true);
    assert.equal(mutations[0][1].body, mutations[1][1].body);
    assert.equal(mutations[1][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
  } finally { globalThis.fetch = original; }
});

test("M6 business routes require a session and local reads never add mutation authority", async () => {
  for (const route of ["/business", "/portal/business"]) {
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`)).status, 307);
    assert.equal(proxyExports.proxy(new NextRequest(`http://console.example.test${route}`, {
      headers: { cookie: "csa_session=synthetic" },
    })).status, 200);
  }
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push([path, init]);
    return Response.json({ source: "local_records", marketplace_sync: "not_connected", items: [], next_cursor: null });
  };
  try {
    const result = await api("/console/v1/business/orders?limit=20");
    assert.equal(result.marketplace_sync, "not_connected");
    assert.equal(calls.length, 1);
    assert.equal(calls[0][1].credentials, "same-origin");
    assert.equal(calls[0][1].headers.has("Authorization"), false);
    assert.equal(calls[0][1].headers.has("X-Client-Id"), false);
  } finally { globalThis.fetch = original; }
});

test("M6 local order filters encode platform-scoped store IDs without changing tenant authority", async () => {
  const shopId = "SYNTH/store+?&=%";
  const path = localOrderPath("client_id=synthetic-tenant", "tiktok_shop", shopId);
  const parsed = new URL(path, "http://console.example.test");
  assert.equal(parsed.pathname, "/console/v1/business/orders");
  assert.equal(parsed.searchParams.get("client_id"), "synthetic-tenant");
  assert.equal(parsed.searchParams.get("channel"), "tiktok_shop");
  assert.equal(parsed.searchParams.get("shop_id"), shopId);
  assert.equal(parsed.searchParams.has("before"), false);
  assert.notEqual(path, localOrderPath("client_id=other-tenant", "tiktok_shop", shopId));
  assert.notEqual(path, localOrderPath("client_id=synthetic-tenant", "shopee", shopId));
  assert.notEqual(path, localOrderPath("client_id=synthetic-tenant", "tiktok_shop", "other"));
  assert.equal(localOrderPath("", "", ""), "/console/v1/business/orders?");
  assert.equal(new URL(localOrderPath("", "web", ""), parsed).searchParams.has("shop_id"), false);
  assert.throws(() => localOrderPath("", "", shopId), /source\/platform/);
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (requestPath, init) => {
    calls.push([requestPath, init]);
    return Response.json({ source: "local_records", marketplace_sync: "not_connected", items: [], next_cursor: null });
  };
  try {
    await api(path);
    assert.equal(calls[0][0], path);
    assert.equal(calls[0][1].credentials, "same-origin");
    assert.equal(calls[0][1].headers.has("X-Client-Id"), false);
    assert.equal(calls[0][1].headers.has("Authorization"), false);
  } finally { globalThis.fetch = original; }
});

test("M6 store metadata reads carry the recorded connection values and errors stay explicit", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push([path, init]);
    if (path.includes("before=")) return Response.json({ detail: "local store cursor not found" }, { status: 404 });
    return Response.json({
      source: "local_credential_records", marketplace_sync: "not_connected",
      items: [{
        id: "synthetic-record", platform: "tiktok_shop", shop_id: "synthetic-store",
        expires_at: null, updated_at: "2026-01-01T00:00:00Z", expiry: "unknown",
        authorization: "verified", permissions: "granted", provider_reads: "available",
        connection: { status: "authorization_recorded", live_integration: "available" },
        sync: { orders_synced_at: null, products_synced_at: null, problem: null }, can_manage: false,
      }], next_cursor: null,
    });

  };
  try {
    const path = "/console/v1/business/stores?client_id=synthetic-tenant&platform=tiktok_shop";
    const result = await api(path);
    assert.equal(result.source, "local_credential_records");
    assert.equal(result.items[0].authorization, "verified");
    assert.equal(result.items[0].permissions, "granted");
    assert.equal(result.items[0].provider_reads, "available");
    assert.equal(result.items[0].connection.live_integration, "available");
    assert.equal(connectionLabels[result.items[0].connection.status], "Connected and checked with TikTok");
    assert.equal(result.items[0].expires_at, null);
    await assert.rejects(api(`${path}&before=foreign-record`), { status: 404, message: "local store cursor not found" });
    assert.equal(calls.length, 2);
    for (const [, init] of calls) {
      assert.equal(init.credentials, "same-origin");
      assert.equal(init.cache, "no-store");
      assert.equal(init.headers.has("Authorization"), false);
      assert.equal(init.headers.has("X-Client-Id"), false);
    }
  } finally { globalThis.fetch = original; }
});

test("M6 synthetic demo listings are temporary local state with explicit no-API boundary", () => {
  assert.equal(demoListings.length, 2);
  assert.ok(demoListings.every((listing) => listing.id.startsWith("demo-listing-")));
  const added = appendDemoListing(demoListings);
  assert.equal(demoListings.length, 2, "appending demo state must not mutate the fixture");
  assert.equal(added.length, 3);
  assert.equal(added[2].name, "Sample listing 3");
  assert.equal(added[2].status, "Demo draft");
  assert.match(businessWorkspaceSource, /Example data only, made up for this view\. Not connected to TikTok; nothing here is sent\s+to TikTok/);
  assert.match(businessWorkspaceSource, /No TikTok request was made/);
  const demoSection = businessWorkspaceSource.split("function DemoBusinessWorkspace()")[1]
    .split("function BusinessRecords")[0];
  assert.doesNotMatch(demoSection, /\bapi\s*(?:<[^>]+>)?\s*\(/);
  assert.doesNotMatch(demoSection, /\bpost\s*\(/);
});

test("connection lifecycle labels are plain, and Connect and Reconnect are real, Admin-only buttons", async () => {
  assert.deepEqual(Object.keys(connectionLabels).sort(), [
    "unsupported", "missing_shop_id", "unverified", "revoked", "disconnected",
    "expired", "app_unavailable", "binding_missing", "permissions_missing", "authorization_recorded",
  ].sort());
  for (const label of Object.values(connectionLabels)) assert.doesNotMatch(label, /locally|not verified live|evidence/i);
  const source = await readFile(
    new URL("../components/console/business/StoreConnections.tsx", import.meta.url), "utf8",
  );
  const workspace = await readFile(
    new URL("../components/console/BusinessWorkspace.tsx", import.meta.url), "utf8",
  );
  assert.doesNotMatch(source + workspace, /Connect unavailable|Reconnect unavailable/);
  assert.doesNotMatch(source, /Account registration alone|saved records only|coming soon|Partner Center eligibility/i);
  assert.match(workspace, /<StoreConnections /);
  assert.match(source, /connectionLabels\[store.connection.status\]/);
  // Connect is shown only to an Admin or platform admin; per-card buttons follow can_manage.
  assert.match(source, /me\.role === "platform_admin" \|\| me\.role === "tenant_admin"/);
  assert.match(source, /storeActions\(store\)/);
  // The only way the browser leaves for TikTok is the checked navigate helper.
  assert.doesNotMatch(source, /window\.location/);
  assert.match(source, /navigate\(started\.authorization_url\)/);
});

test("lost upstream response becomes a proxy error; explicit upload retry keeps the durable job", async () => {
  const { proxyRequest } = require("next/dist/server/lib/router-utils/proxy-request");
  const originalFetch = globalThis.fetch;
  const originalError = console.error;
  const uploads = [];
  const proxyErrors = [];
  const key = "synthetic-idempotency-key";
  const jobId = "synthetic-durable-job";
  let committed = false;
  const listen = (server) => new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
  const close = (server) => new Promise((resolve, reject) => {
    server.closeAllConnections();
    server.close((error) => error ? reject(error) : resolve());
  });
  const upstream = createServer(async (req, res) => {
    if (req.url === "/console/v1/auth/csrf") {
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ csrf_token: "synthetic-csrf" }));
      return;
    }
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    uploads.push({ headers: req.headers, body: Buffer.concat(chunks).toString() });
    if (!committed) {
      committed = true;
      req.socket.destroy();
      return;
    }
    res.writeHead(202, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ job_id: jobId, duplicate_request: true }));
  });
  const upstreamPort = await listen(upstream);
  const proxy = createServer((req, res) => {
    void proxyRequest(
      req, res, parse(`http://127.0.0.1:${upstreamPort}${req.url}`, true),
    ).catch((error) => proxyErrors.push(error.code));
  });
  try {
    const proxyPort = await listen(proxy);
    console.error = (...args) => proxyErrors.push(String(args[0]));
    globalThis.fetch = (path, init) => originalFetch(
      new URL(path, `http://127.0.0.1:${proxyPort}`), init,
    );
    const upload = () => {
      const body = new FormData();
      body.append("files", new Blob(["question,answer\nQ,Seventeen days\n"]), "faq.csv");
      return api("/console/v1/kb/uploads", {
        method: "POST", body, headers: { "Idempotency-Key": key },
      });
    };
    await assert.rejects(upload(), { status: 500 });
    assert.equal(committed, true);
    assert.equal(uploads.length, 1, "transport must not retry an ambiguous mutation automatically");
    assert.deepEqual(await upload(), { job_id: jobId, duplicate_request: true });
    assert.equal(uploads.length, 2);
    assert.ok(proxyErrors.length > 0, "upstream failure must be surfaced");
    for (const request of uploads) {
      assert.equal(request.headers["idempotency-key"], key);
      assert.equal(request.headers["x-csrf-token"], "synthetic-csrf");
      assert.match(request.headers["content-type"], /multipart\/form-data; boundary=/);
      assert.match(request.body, /Seventeen days/);
    }
  } finally {
    globalThis.fetch = originalFetch;
    console.error = originalError;
    await close(proxy);
    await close(upstream);
  }
});


test("recorded audio preserves MIME, raw bytes, CSRF and same-origin credentials", async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (path, init) => {
    calls.push([path, init]);
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    return Response.json({ status: "completed", transcript: "Synthetic draft" });
  };
  try {
    const blob = new Blob(["synthetic-audio"], { type: "audio/webm;codecs=opus" });
    await api("/console/v1/chat/conversations/synthetic/voice/synthetic", { method: "POST", body: blob });
    assert.equal(calls.length, 2);
    const [, init] = calls[1];
    assert.equal(init.body, blob);
    assert.equal(init.headers.get("Content-Type"), blob.type);
    assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
    assert.equal(init.credentials, "same-origin");
  } finally { globalThis.fetch = original; }
});
