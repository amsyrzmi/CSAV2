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

const DRAFT = "9b2c0d4e-0000-4000-8000-000000000001";
const VERSION = "a".repeat(64);

function recorder(answer) {
  const requests = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (path, init = {}) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return answer(path, init, requests.length);
  };
  return { requests, restore: () => { globalThis.fetch = original; } };
}
const key = (request) => request[1].headers.get("Idempotency-Key");

test("send posts the confirmed version with a key, and keeps the key only while the outcome is unknown", async () => {
  const replies = [
    () => { throw new Error("synthetic lost acknowledgement"); },
    () => Response.json({ detail: "send_outcome_unknown", message: "Check your Gmail Sent folder before trying again." }, { status: 502 }),
    () => Response.json({ detail: "send_refused" }, { status: 409 }),
    () => Response.json({ outcome: "sent", sent_at: "2026-10-29T02:00:00Z", gmail_message_id: "m1", remaining_this_hour: 19 }),
  ];
  const { requests, restore } = recorder((_p, _i, n) => replies[n - 1]());
  try {
    const client = emailClient("synthetic-tenant");
    await assert.rejects(client.send(DRAFT, VERSION), /lost acknowledgement/);
    const unknown = await client.send(DRAFT, VERSION).catch((e) => e);
    assert.equal(unknown.code, "send_outcome_unknown");
    const refused = await client.send(DRAFT, VERSION).catch((e) => e);
    assert.equal(refused.code, "send_refused");
    const sent = await client.send(DRAFT, VERSION);
    assert.equal(sent.outcome, "sent");
    assert.equal(requests[0][0], `/console/v1/email/drafts/${DRAFT}/send?client_id=synthetic-tenant`);
    assert.equal(requests[0][1].method, "POST");
    assert.deepEqual(JSON.parse(requests[0][1].body), { version: VERSION });
    assert.match(key(requests[0]), /^[0-9a-f-]{36}$/);
    assert.equal(key(requests[1]), key(requests[0]), "a lost answer retakes the same key");
    assert.equal(key(requests[2]), key(requests[1]), "an unknown outcome is never sent again under a new key");
    assert.notEqual(key(requests[3]), key(requests[2]), "a clear refusal frees the key for a fresh attempt");
  } finally { restore(); }
});

test("only a 4xx with a JSON body frees the send key; a 5xx never does", async () => {
  const replies = [
    () => Response.json({ detail: "provider_unreachable" }, { status: 502 }),
    () => Response.json({ detail: "something_failed" }, { status: 503 }),
    () => new Response("Bad gateway", { status: 400 }),
    () => Response.json({ detail: "send_limit_reached", retry_after: 60 }, { status: 429 }),
    () => Response.json({ detail: "send_released" }, { status: 409 }),
    () => Response.json({ outcome: "sent", sent_at: null, gmail_message_id: "m1", remaining_this_hour: 19 }),
  ];
  const { requests, restore } = recorder((_p, _i, n) => replies[n - 1]());
  try {
    const client = emailClient("");
    for (let attempt = 0; attempt < 5; attempt += 1) await assert.rejects(client.send(DRAFT, VERSION));
    await client.send(DRAFT, VERSION);
    assert.equal(key(requests[1]), key(requests[0]), "a 5xx with a JSON body keeps the key");
    assert.equal(key(requests[2]), key(requests[1]), "another 5xx keeps it too");
    assert.equal(key(requests[3]), key(requests[2]), "a 4xx without a JSON body keeps it");
    assert.notEqual(key(requests[4]), key(requests[3]), "a 429 refusal frees it");
    assert.notEqual(key(requests[5]), key(requests[4]), "a released send frees it");
  } finally { restore(); }
});

test("email refusals the assistant dock shows are plain sentences", async () => {
  const { api } = await import(apiUrl);
  const copy = {
    reply_target_unavailable: "I can't tell who to reply to in that conversation.",
    email_recipient_invalid: "Check the email address.",
    recipients_invalid: "Check the email addresses.",
    draft_rejected: "I couldn't write that email. Try rephrasing.",
    thread_body_unavailable: "I couldn't read that conversation from Gmail. Sync and try again.",
    resource_unavailable: "That item isn't available any more.",
  };
  for (const [code, sentence] of Object.entries(copy)) {
    const { restore } = recorder(() => Response.json({ detail: code }, { status: 409 }));
    try {
      const error = await api("/console/v1/workspace/conversations").catch((e) => e);
      assert.equal(error.message, sentence);
      assert.equal(error.code, code);
    } finally { restore(); }
  }
});

test("a released send gets a fresh key; the refusal keeps the wait and the message", async () => {
  const replies = [
    () => Response.json({ detail: "send_outcome_unknown" }, { status: 502 }),
    () => Response.json({ detail: "draft_not_releasable" }, { status: 409 }),
    () => Response.json({ id: DRAFT, status: "draft" }),
    () => Response.json({ detail: "send_limit_reached", retry_after: 120 }, { status: 429, headers: { "Retry-After": "120" } }),
  ];
  const { requests, restore } = recorder((_p, _i, n) => replies[n - 1]());
  try {
    const client = emailClient("");
    await assert.rejects(client.send(DRAFT, VERSION));
    const early = await client.release(DRAFT).catch((e) => e);
    assert.equal(early.code, "draft_not_releasable");
    await client.release(DRAFT);
    assert.equal(requests[1][0], `/console/v1/email/drafts/${DRAFT}/release`);
    assert.equal(requests[1][1].method, "POST");
    assert.equal(requests[1][1].body, undefined);
    assert.equal(key(requests[2]), key(requests[1]), "the release retakes its own key");
    const limited = await client.send(DRAFT, VERSION).catch((e) => e);
    assert.notEqual(key(requests[3]), key(requests[0]), "after a release the old send key is never reused");
    assert.equal(limited.status, 429);
    assert.equal(limited.code, "send_limit_reached");
    assert.equal(limited.body.retry_after, 120);
  } finally { restore(); }
});

test("load more, attention, one draft and edits use their own routes", async () => {
  const { requests, restore } = recorder(() => Response.json({ items: [], as_of: null, detail: { threads: 0, done: true } }));
  try {
    const client = emailClient("synthetic-tenant");
    await client.syncMore();
    await client.syncMore();
    await client.attention();
    await client.getDraft(DRAFT);
    await client.updateDraft(DRAFT, { to: ["a@x.test"], cc: ["b@x.test"], bcc: [], subject: "S", body: "B" });
    const [more, again, attention, one, update] = requests;
    assert.equal(more[0], "/console/v1/email/sync/more?client_id=synthetic-tenant");
    assert.equal(more[1].method, "POST");
    assert.equal(more[1].body, undefined);
    assert.match(key(more), /^[0-9a-f-]{36}$/);
    assert.notEqual(key(again), key(more), "each finished page asks with a new key");
    assert.equal(attention[0], "/console/v1/email/attention?client_id=synthetic-tenant");
    assert.equal(attention[1].method ?? "GET", "GET");
    assert.equal(new Headers(attention[1].headers).has("Idempotency-Key"), false);
    assert.equal(one[0], `/console/v1/email/drafts/${DRAFT}?client_id=synthetic-tenant`);
    assert.equal(new Headers(one[1].headers).has("Idempotency-Key"), false);
    assert.equal(update[0], `/console/v1/email/drafts/${DRAFT}?client_id=synthetic-tenant`);
    assert.equal(update[1].method, "PUT");
    assert.deepEqual(JSON.parse(update[1].body), { to: ["a@x.test"], cc: ["b@x.test"], bcc: [], subject: "S", body: "B" });
    assert.match(key(update), /^[0-9a-f-]{36}$/);
  } finally { restore(); }
});
