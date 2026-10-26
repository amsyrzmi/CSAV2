import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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
const apiUrl = await load("api.ts");
const client = await import(await load("workspace-api.ts", {
  '"@/lib/api"': JSON.stringify(apiUrl),
}));
const { ApiError } = await import(apiUrl);

const calls = [];
let reply = () => new Response(JSON.stringify({ ok: true }), { status: 200 });
globalThis.fetch = async (url, init = {}) => {
  if (url === "/console/v1/auth/csrf") return new Response(JSON.stringify({ csrf_token: "tok" }), { status: 200 });
  calls.push({ url, method: init.method ?? "GET", headers: new Headers(init.headers), body: init.body });
  return reply();
};
const last = () => calls.at(-1);

test("proposal create posts kind, payload and thread with CSRF", async () => {
  await client.createProposal("email_export", { subject: "Hi" }, "t1");
  assert.equal(last().url, "/console/v1/workspace/proposals");
  assert.equal(last().method, "POST");
  assert.equal(last().headers.get("X-CSRF-Token"), "tok");
  assert.deepEqual(JSON.parse(last().body), { kind: "email_export", payload: { subject: "Hi" }, thread_id: "t1" });
  await client.createProposal("email_export", {});
  assert.deepEqual(JSON.parse(last().body), { kind: "email_export", payload: {} });
});

test("confirm and cancel hit the proposal paths with CSRF", async () => {
  await client.confirmProposal("p 1");
  assert.equal(last().url, "/console/v1/workspace/proposals/p%201/confirm");
  assert.equal(last().method, "POST");
  assert.equal(last().headers.get("X-CSRF-Token"), "tok");
  await client.cancelProposal("p1");
  assert.equal(last().url, "/console/v1/workspace/proposals/p1/cancel");
});

test("people search encodes the query; me has its own path", async () => {
  await client.searchPeople("a&b c");
  assert.equal(last().url, "/console/v1/workspace/people?q=a%26b+c");
  assert.equal(last().method, "GET");
  await client.myPeople();
  assert.equal(last().url, "/console/v1/workspace/people/me");
});

test("the turn body carries page_context", async () => {
  const ctx = { tab: "inbox", record: { kind: "conversation", id: "c1" } };
  await client.createWorkspaceTurn("tenant", "th", "hello", { page: "workspace_home" }, "rk", undefined, ctx);
  assert.match(last().url, /\/turns\?client_id=tenant$/);
  assert.deepEqual(JSON.parse(last().body), {
    request_key: "rk", body: "hello", page_hint: { page: "workspace_home" }, page_context: ctx,
  });
  await client.createWorkspaceTurn("tenant", "th", "hello", { page: "workspace_home" }, "rk");
  assert.equal("page_context" in JSON.parse(last().body), false);
});

test("error codes flow through ApiError.code", async () => {
  for (const code of ["proposal_expired", "proposal_consumed", "proposal_session_mismatch", "proposal_stale", "planner_unavailable", "planner_invalid_output", "package_inactive"]) {
    reply = () => new Response(JSON.stringify({ detail: code }), { status: 409 });
    await assert.rejects(client.confirmProposal("p1"), (e) => e instanceof ApiError && e.code === code);
  }
});

test("workspace endpoints keep thread IDs path-encoded and the tenant explicit", () => {
  assert.equal(
    client.workspaceEndpoint("tenant /one"),
    "/console/v1/workspace/conversations?client_id=tenant+%2Fone",
  );
  assert.equal(client.workspaceEndpoint(null), "/console/v1/workspace/conversations");
  assert.equal(
    client.workspaceEndpoint("tenant-id", "thread/one", true),
    "/console/v1/workspace/conversations/thread%2Fone/turns?client_id=tenant-id",
  );
});

test("a turn without a legacy page hint omits page_hint", async () => {
  reply = () => new Response(JSON.stringify({ ok: true }), { status: 200 });
  await client.createWorkspaceTurn("tenant", "th", "hi", null, "rk", undefined, { tab: "hr" });
  assert.deepEqual(JSON.parse(last().body), { request_key: "rk", body: "hi", page_context: { tab: "hr" } });
});

test("confirm and cancel name the tenant when one is given", async () => {
  reply = () => new Response(JSON.stringify({ ok: true }), { status: 200 });
  await client.confirmProposal("p1", undefined, "tenant /one");
  assert.equal(last().url, "/console/v1/workspace/proposals/p1/confirm?client_id=tenant+%2Fone");
  await client.cancelProposal("p1", undefined, "t");
  assert.equal(last().url, "/console/v1/workspace/proposals/p1/cancel?client_id=t");
});
