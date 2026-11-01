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
const source = await readFile(new URL("../lib/hr-people-api.ts", import.meta.url), "utf8");
const { peopleClient } = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));

const TENANT = "a b/1";
const SUFFIX = "client_id=a%20b%2F1";
const ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
const MANAGER = "33333333-3333-4333-8333-333333333333";

async function capture(run, reply = () => Response.json({})) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return reply(path, init);
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}

test("page names the tenant first, keeps the query order and leaves out empty filters", async () => {
  const client = peopleClient(TENANT);
  const [, requests] = await capture(async () => {
    await client.page({});
    await client.page({ q: "Aina Rahman", department: "", status: "starting", cursor: undefined });
    await client.page({ cursor: "50", status: "left", manager: MANAGER, department: "Sales & Ops", q: "x/y" });
  });
  assert.deepEqual(requests.map(([path]) => path), [
    `/console/v1/hr/people?${SUFFIX}`,
    `/console/v1/hr/people?${SUFFIX}&q=Aina+Rahman&status=starting`,
    `/console/v1/hr/people?${SUFFIX}&q=x%2Fy&department=Sales+%26+Ops&manager=${MANAGER}&status=left&cursor=50`,
  ]);
});

test("every other read is a plain GET on its own path", async () => {
  const client = peopleClient(TENANT);
  const [, requests] = await capture(async () => {
    await client.person(ID);
    await client.history(ID);
    await client.access(ID);
    await client.orgChart();
    await client.departments();
    await client.home();
  });
  assert.deepEqual(requests.map(([path]) => path), [
    `/console/v1/hr/people/${ID}?${SUFFIX}`,
    `/console/v1/hr/people/${ID}/history?${SUFFIX}`,
    `/console/v1/hr/people/${ID}/access?${SUFFIX}`,
    `/console/v1/hr/org-chart?${SUFFIX}`,
    `/console/v1/hr/departments?${SUFFIX}`,
    `/console/v1/hr/home?${SUFFIX}`,
  ]);
});

test("every request is a GET without a body or a CSRF header", async () => {
  const client = peopleClient(TENANT);
  const [, requests] = await capture(async () => {
    await client.page({ q: "a" });
    await client.person(ID);
    await client.history(ID);
    await client.access(ID);
    await client.orgChart();
    await client.departments();
    await client.home();
  });
  assert.equal(requests.length, 7);
  for (const [, init] of requests) {
    assert.equal((init.method ?? "GET").toUpperCase(), "GET");
    assert.equal(init.body, undefined);
    assert.equal(init.credentials, "same-origin");
    assert.equal(new Headers(init.headers).has("X-CSRF-Token"), false);
  }
});

test("an id with path characters is encoded", async () => {
  const [, requests] = await capture(() => peopleClient("t").person("a/b"));
  assert.equal(requests[0][0], "/console/v1/hr/people/a%2Fb?client_id=t");
});

test("the reply is returned as sent", async () => {
  const reply = { items: [], next_cursor: null, total: 0 };
  const [answer] = await capture(() => peopleClient("t").page({}), () => Response.json(reply));
  assert.deepEqual(answer, reply);
});
