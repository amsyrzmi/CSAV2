import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const source = await readFile(new URL("../lib/hr-api.ts", import.meta.url), "utf8");
const { hrClient } = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { localDictation, speakLocal } = await import(moduleUrl(await readFile(new URL("../lib/hr-voice.ts", import.meta.url), "utf8")));

test("dictation requires local processing, emits text for review and aborts cleanly", () => {
  const original = globalThis.window;
  const instances = []; let starts = 0; let aborts = 0; let transcript = "";
  class LocalRecognition {
    processLocally = false;
    constructor() { instances.push(this); }
    start() { assert.equal(this.processLocally, true); starts++; }
    abort() { aborts++; }
  }
  globalThis.window = { isSecureContext: true, SpeechRecognition: LocalRecognition };
  try {
    const stop = localDictation(text => { transcript = text; }, () => {}, assert.fail);
    assert.equal(starts, 1);
    assert.equal(instances[0].lang, "en-MY");
    instances[0].onresult({ results: [[{ transcript: "Synthetic training request" }]] });
    assert.equal(transcript, "Synthetic training request");
    stop(); assert.equal(aborts, 1); assert.equal(instances[0].onresult, null);
    const stopEnglish = localDictation(() => {}, () => {}, assert.fail, "en-US");
    assert.equal(instances[1].lang, "en-US", "use the selected installed language pack");
    stopEnglish();
    globalThis.window.SpeechRecognition = class { start() { starts++; } };
    assert.throws(() => localDictation(() => {}, () => {}, () => {}), /cannot require local/);
    assert.equal(starts, 2, "never start a cloud-only recognizer");
  } finally { globalThis.window = original; }
});

test("status playback selects a local voice and refuses a remote-only service", () => {
  const original = globalThis.window; const originalUtterance = globalThis.SpeechSynthesisUtterance;
  const remote = { localService: false, lang: "en-MY" };
  const local = { localService: true, lang: "en-MY" };
  const spoken = [];
  globalThis.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  globalThis.window = { speechSynthesis: { getVoices: () => [remote, local], cancel() {}, speak(item) { spoken.push(item); } } };
  try {
    speakLocal("Synthetic saved status"); assert.equal(spoken[0].voice, local);
    globalThis.window.speechSynthesis.getVoices = () => [remote];
    assert.throws(() => speakLocal("Private status"), /No local English voice/);
    assert.equal(spoken.length, 1);
  } finally { globalThis.window = original; globalThis.SpeechSynthesisUtterance = originalUtterance; }
});

test("uncertain HR mutation is explicit, retains exact key and payload, and binds tenant", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    if (requests.length === 1) throw new Error("synthetic lost acknowledgement");
    return Response.json({ kind: "persisted_training", request: { request_id: "saved" } });
  };
  try {
    const client = hrClient("synthetic-tenant");
    const body = { title: "Synthetic training", learning_objective: "Practice" };
    await assert.rejects(client.draft(body), /lost acknowledgement/);
    assert.equal(requests.length, 1, "never retry an uncertain write automatically");
    await client.draft(body);
    assert.equal(requests[0][1].headers.get("Idempotency-Key"), requests[1][1].headers.get("Idempotency-Key"));
    assert.equal(requests[0][1].body, requests[1][1].body);
    await client.draft({ ...body, title: "Changed title" });
    assert.notEqual(requests[1][1].headers.get("Idempotency-Key"), requests[2][1].headers.get("Idempotency-Key"));
    await hrClient("other-tenant").draft(body);
    assert.match(requests[3][0], /client_id=other-tenant/);
    assert.notEqual(requests[1][1].headers.get("Idempotency-Key"), requests[3][1].headers.get("Idempotency-Key"));
    for (const [, init] of requests) {
      assert.equal(init.credentials, "same-origin");
      assert.equal(init.cache, "no-store");
      assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
      for (const header of ["Authorization", "X-Role", "X-Employee-Id", "X-Client-Owner"])
        assert.equal(init.headers.has(header), false);
    }
  } finally { globalThis.fetch = original; }
});

test("human confirmation sends opaque reference with empty body; pages encode cursors", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]); return Response.json({ items: [], next_cursor: null });
  };
  try {
    const client = hrClient("tenant&other=1");
    await client.confirm("synthetic-reference");
    assert.equal(requests[0][1].body, "{}");
    assert.equal(new URL(requests[0][0], "https://example.test").searchParams.get("client_id"), "tenant&other=1");
    await client.workflows("cursor&domain=payroll");
    const parsed = new URL(requests[1][0], "https://example.test");
    assert.equal(parsed.searchParams.get("after"), "cursor&domain=payroll");
    assert.equal(parsed.searchParams.has("domain"), false);
  } finally { globalThis.fetch = original; }
});

test("HR pages are adopted but require sessions", async () => {
  const require = createRequire(import.meta.url);
  const code = ts.transpileModule(await readFile(new URL("../proxy.ts", import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const exports = {}; new Function("require", "exports", code)(require, exports);
  const { NextRequest } = require("next/server");
  for (const path of ["/hr", "/portal/hr"]) {
    assert.equal(exports.proxy(new NextRequest(`https://example.test${path}`)).status, 307);
    assert.equal(exports.proxy(new NextRequest(`https://example.test${path}`, {
      headers: { Cookie: "csa_session=synthetic-session" },
    })).status, 200);
  }
});
