import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const read = (name) => readFile(new URL(name, import.meta.url), "utf8");
const apiUrl = moduleUrl(await read("../lib/api.ts"));
const hrApiUrl = moduleUrl((await read("../lib/hr-api.ts")).replace('"./api"', JSON.stringify(apiUrl)));
const { processClient } = await import(moduleUrl((await read("../lib/hr-process-api.ts"))
  .replace('"./api"', JSON.stringify(apiUrl)).replace('"./hr-api"', JSON.stringify(hrApiUrl))));
const { processErrorText, processSaveError, processLoadError, PROCESS_ERROR_FALLBACK, PROCESS_LOAD_ERROR } = await import(
  moduleUrl((await read("../lib/hr-process-copy.ts")).replace('"./api"', JSON.stringify(apiUrl))));
const { ApiError } = await import(apiUrl);

const TENANT = "a b/1";
const SUFFIX = "client_id=a%20b%2F1";
const ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
const OTHER = "33333333-3333-4333-8333-333333333333";
const BASE = "/console/v1/hr";

async function capture(run, reply = () => Response.json({})) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push({ path, method: init?.method ?? "GET", body: init?.body, headers: new Headers(init?.headers) });
    return reply(requests.length);
  };
  try { await run(processClient(TENANT)); } finally { globalThis.fetch = original; }
  return requests;
}
const one = async (run) => (await capture(run))[0];

test("reads are GETs under /console/v1/hr with the tenant first and no key", async () => {
  const requests = await capture(async (client) => {
    await client.templates();
    await client.template(ID);
    await client.process(ID);
    await client.personProcesses(ID);
    await client.myTasks();
  });
  assert.deepEqual(requests.map((request) => [request.method, request.path]), [
    ["GET", `${BASE}/templates?${SUFFIX}`],
    ["GET", `${BASE}/templates/${ID}?${SUFFIX}`],
    ["GET", `${BASE}/processes/${ID}?${SUFFIX}`],
    ["GET", `${BASE}/people/${ID}/processes?${SUFFIX}`],
    ["GET", `${BASE}/tasks/mine?${SUFFIX}`],
  ]);
  for (const request of requests) {
    assert.equal(request.headers.get("Idempotency-Key"), null);
    assert.equal(request.body, undefined);
  }
});

test("ids are UUIDs: anything else is rejected before any request", async () => {
  const requests = await capture(async (client) => {
    for (const bad of ["..", "x/y", "a/b?c", "x y", "../e", "", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4", `${ID}/cancel`]) {
      for (const call of [
        () => client.template(bad), () => client.process(bad), () => client.personProcesses(bad),
        () => client.updateTemplate(bad, { expected_version: 1, name: "n", rules: null, steps: [] }),
        () => client.deactivateTemplate(bad, 1), () => client.cancelProcess(bad),
        () => client.closeProcess(bad, "note"), () => client.taskAction(bad, "approve", { expected_version: 1 }),
      ]) await assert.rejects(call(), /Not a \w+ id/, bad);
    }
  });
  assert.deepEqual(requests, []);
});

test("a UUID in capitals is accepted and sent as given", async () => {
  const request = await one((client) => client.process(ID.toUpperCase()));
  assert.equal(request.path, `${BASE}/processes/${ID.toUpperCase()}?${SUFFIX}`);
});

test("processes carries only the filters that are set, in a fixed order, then the cursor", async () => {
  const requests = await capture(async (client) => {
    await client.processes({});
    await client.processes({ kind: "onboarding", status: "active", overdue: true, department: ID, owner: OTHER });
    await client.processes({ overdue: false, kind: "", status: undefined, department: "" }, OTHER);
    await client.processes({ status: "cancelled", limit: 25 }, "c/1");
  });
  assert.deepEqual(requests.map((request) => request.path), [
    `${BASE}/processes?${SUFFIX}`,
    `${BASE}/processes?${SUFFIX}&kind=onboarding&status=active&overdue=true&department=${ID}&owner=${OTHER}`,
    `${BASE}/processes?${SUFFIX}&after=${OTHER}`,
    `${BASE}/processes?${SUFFIX}&status=cancelled&limit=25&after=c%2F1`,
  ]);
  assert.ok(requests.every((request) => request.method === "GET"));
});

test("myTasks carries the status and the cursor", async () => {
  const requests = await capture(async (client) => {
    await client.myTasks("awaiting_approval");
    await client.myTasks(undefined, OTHER);
    await client.myTasks("todo", OTHER);
  });
  assert.deepEqual(requests.map((request) => request.path), [
    `${BASE}/tasks/mine?${SUFFIX}&status=awaiting_approval`,
    `${BASE}/tasks/mine?${SUFFIX}&after=${OTHER}`,
    `${BASE}/tasks/mine?${SUFFIX}&status=todo&after=${OTHER}`,
  ]);
});

test("template writes use the right method, path and exact body, each with a key", async () => {
  const step = { title_en: "Collect the contract", owner_role: "hr", anchor: "start_date", due_offset_days: -2, required: true };
  const create = { kind: "onboarding", name: "Standard", rules: null, steps: [step] };
  const update = { expected_version: 3, name: "Standard", rules: { region: "sabah" }, steps: [step], is_active: false };
  const requests = await capture(async (client) => {
    await client.createTemplate(create);
    await client.updateTemplate(ID, update);
    await client.deactivateTemplate(ID, 4);
  });
  assert.deepEqual(requests.map((request) => [request.method, request.path, JSON.parse(request.body)]), [
    ["POST", `${BASE}/templates?${SUFFIX}`, create],
    ["PUT", `${BASE}/templates/${ID}?${SUFFIX}`, update],
    ["POST", `${BASE}/templates/${ID}/deactivate?${SUFFIX}`, { expected_version: 4 }],
  ]);
  const keys = requests.map((request) => request.headers.get("Idempotency-Key"));
  assert.ok(keys.every(Boolean));
  assert.equal(new Set(keys).size, 3);
  assert.ok(requests.every((request) => request.headers.get("X-CSRF-Token") === "synthetic-csrf"));
});

test("an update always sends rules, even null, so a save can never clear them by omission", async () => {
  const request = await one((client) => client.updateTemplate(ID, { expected_version: 1, name: "n", rules: null, steps: [] }));
  assert.ok("rules" in JSON.parse(request.body));
  assert.equal(JSON.parse(request.body).rules, null);
});

test("previewTemplate is a POST with the draft rules, CSRF and no idempotency key", async () => {
  const rules = { department_id: ID, employment_type: "full_time" };
  const request = await one((client) => client.previewTemplate("onboarding", rules));
  assert.equal(request.method, "POST");
  assert.equal(request.path, `${BASE}/templates/preview?${SUFFIX}`);
  assert.deepEqual(JSON.parse(request.body), { kind: "onboarding", rules });
  assert.equal(request.headers.get("X-CSRF-Token"), "synthetic-csrf");
  assert.equal(request.headers.get("Idempotency-Key"), null);
  const none = await one((client) => client.previewTemplate("custom", null));
  assert.deepEqual(JSON.parse(none.body), { kind: "custom", rules: null });
});

test("startProcess sends replace only when it is given", async () => {
  const body = { employee_id: ID, kind: "onboarding", template_id: OTHER };
  const requests = await capture(async (client) => {
    await client.startProcess(body);
    await client.startProcess({ ...body, replace: true });
    await client.startProcess({ ...body, replace: false });
  });
  assert.ok(requests.every((request) => request.method === "POST" && request.path === `${BASE}/processes?${SUFFIX}`));
  assert.deepEqual(JSON.parse(requests[0].body), body);
  assert.equal("replace" in JSON.parse(requests[0].body), false);
  assert.equal(JSON.parse(requests[1].body).replace, true);
  assert.equal(JSON.parse(requests[2].body).replace, false);
  const keys = requests.map((request) => request.headers.get("Idempotency-Key"));
  assert.equal(new Set(keys).size, 3, "a replace start is a different operation from a plain one");
});

test("cancelProcess has no body and closeProcess sends the note", async () => {
  const requests = await capture(async (client) => {
    await client.cancelProcess(ID);
    await client.closeProcess(ID, "Left the company early");
  });
  assert.equal(requests[0].method, "POST");
  assert.equal(requests[0].path, `${BASE}/processes/${ID}/cancel?${SUFFIX}`);
  assert.equal(requests[0].body, undefined);
  assert.ok(requests[0].headers.get("Idempotency-Key"));
  assert.equal(requests[1].method, "POST");
  assert.equal(requests[1].path, `${BASE}/processes/${ID}/close?${SUFFIX}`);
  assert.deepEqual(JSON.parse(requests[1].body), { note: "Left the company early" });
  assert.ok(requests[1].headers.get("Idempotency-Key"));
});

test("taskAction posts each action to its own path with the body as given", async () => {
  const cases = [
    ["complete", { expected_version: 2, note: "Done on site\nsecond line" }],
    ["approve", { expected_version: 2 }],
    ["send-back", { expected_version: 2, reason: "Wrong form" }],
    ["skip", { expected_version: 2, reason: "Not needed" }],
    ["reschedule", { expected_version: 2, due_on: "2026-12-01" }],
    ["reassign", { expected_version: 2, assignee_employee_id: null }],
    ["note", { note: "" }],
  ];
  const requests = await capture(async (client) => {
    for (const [action, body] of cases) await client.taskAction(ID, action, body);
  });
  assert.deepEqual(requests.map((request) => [request.method, request.path, JSON.parse(request.body)]),
    cases.map(([action, body]) => ["POST", `${BASE}/tasks/${ID}/${action}?${SUFFIX}`, body]));
  assert.ok(requests.every((request) => request.headers.get("Idempotency-Key")));
});

test("taskAction refuses an action the API does not have, before any request", async () => {
  const requests = await capture(async (client) => {
    await assert.rejects(async () => client.taskAction(ID, "delete", {}), /Unknown task action/);
    await assert.rejects(async () => client.taskAction(ID, "../processes", {}), /Unknown task action/);
  });
  assert.deepEqual(requests, []);
});

test("a write keeps its key after an uncertain answer and drops it after success", async () => {
  const original = globalThis.fetch;
  const keys = [];
  let calls = 0;
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "t" });
    keys.push(new Headers(init.headers).get("Idempotency-Key"));
    if (++calls === 1) throw new Error("synthetic lost acknowledgement");
    return Response.json({ operation: "task.approve" });
  };
  try {
    const client = processClient(TENANT);
    const body = { expected_version: 5 };
    await assert.rejects(client.taskAction(ID, "approve", body), /lost acknowledgement/);
    assert.equal(calls, 1, "no automatic retry");
    await client.taskAction(ID, "approve", body);
    assert.equal(keys[0], keys[1], "the same body keeps the key");
    await client.taskAction(ID, "approve", body);
    assert.notEqual(keys[1], keys[2], "success drops the key");
    await client.taskAction(ID, "approve", { expected_version: 6 });
    assert.notEqual(keys[2], keys[3]);
  } finally { globalThis.fetch = original; }
});

test("a refusal surfaces as an ApiError with the server's code", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (path) => path === "/console/v1/auth/csrf"
    ? Response.json({ csrf_token: "t" })
    : Response.json({ detail: "process_already_active" }, { status: 409 });
  try {
    await assert.rejects(processClient(TENANT).startProcess({ employee_id: ID, kind: "onboarding" }),
      (error) => error.status === 409 && error.code === "process_already_active");
  } finally { globalThis.fetch = original; }
});

// ---- Refusal sentences ----

// The service's sources are outside the staged release, which runs this file on its own.
const processesPy = new URL("../../../../csa/hr/processes.py", import.meta.url);
const noService = !existsSync(processesPy) && "staged release";
const SNAKE = /\b[a-z0-9]+(?:_[a-z0-9]+)+\b/;

/** The `PROBLEMS` table of csa/hr/processes.py as code -> sentence (adjacent literals joined). */
function problems(source) {
  const table = /^PROBLEMS: dict\[str, str\] = \{\n([\s\S]*?)\n\}/m.exec(source)?.[1];
  assert.ok(table, "PROBLEMS not found in processes.py");
  const entries = [...table.matchAll(/^ {4}"(\w+)":\s*(\([^)]*\)|"[^"\n]*"),$/gm)];
  assert.equal(entries.length, table.match(/^ {4}"\w+":/gm).length, "an entry of PROBLEMS was not parsed");
  return new Map(entries.map(([, code, value]) =>
    [code, [...value.matchAll(/"([^"\n]*)"/g)].map((part) => part[1]).join("")]));
}

test("every code in csa/hr/processes.py PROBLEMS has the same plain sentence", { skip: noService }, async () => {
  const table = problems(await readFile(processesPy, "utf8"));
  assert.ok(table.size >= 40, `parsed only ${table.size} problem codes`);
  for (const [code, sentence] of table) {
    const text = processErrorText(code);
    assert.ok(text.length > 0, code);
    assert.equal(text, sentence, code);
    assert.doesNotMatch(text, SNAKE, `${code}: ${text}`);
  }
});

test("process_already_active keeps the backend sentence for the dialog to extend", () => {
  assert.equal(processErrorText("process_already_active"), "This person already has an active process of this kind.");
});

test("the route-level codes the process routes add have sentences too", () => {
  for (const code of ["read_only_session", "invalid_hr_input", "invalid_hr_query", "hr_request_too_large", "hr_database_unavailable", "hr_retry_required"]) {
    const text = processErrorText(code);
    assert.ok(text.length > 10, code);
    assert.notEqual(text, PROCESS_ERROR_FALLBACK, code);
    assert.doesNotMatch(text, SNAKE, code);
  }
});

test("codes the process table lacks fall back to the console-wide sentence before the generic one", () => {
  for (const code of ["csrf_required", "package_inactive"]) {
    const text = processErrorText(code);
    assert.notEqual(text, PROCESS_ERROR_FALLBACK, code);
    assert.doesNotMatch(text, SNAKE, code);
  }
  assert.match(processErrorText("csrf_required"), /session/);
  assert.equal(processErrorText("hr_retry_required"), "Another change was in progress. Try again.");
  // The process table wins over the console-wide one.
  assert.equal(processErrorText("resource_unavailable"), "That item does not exist, or you cannot see it.");
});

test("an unknown code or one from Object.prototype gets the generic sentence, never the code", () => {
  for (const code of ["brand_new_code", "", "__proto__", "constructor", "toString", "Request failed (500)"]) {
    assert.equal(processErrorText(code), PROCESS_ERROR_FALLBACK, code);
  }
  assert.doesNotMatch(PROCESS_ERROR_FALLBACK, SNAKE);
});

// ---- Save and load errors ----

const refused = (status, code) => new ApiError(status, code, code);

test("a failed write: a 4xx gets the refusal sentence, a 5xx or network error the uncertain one", () => {
  assert.equal(processSaveError(refused(409, "task_changed"), "complete"), processErrorText("task_changed"));
  assert.equal(processSaveError(refused(404, "resource_unavailable"), "approve"), processErrorText("resource_unavailable"));
  assert.equal(processSaveError(refused(409, "process_already_active"), "start"), processErrorText("process_already_active"));
  assert.equal(processSaveError(refused(422, "brand_new_code"), "save"), PROCESS_ERROR_FALLBACK);
  for (const failure of [refused(500, "Request failed (500)"), refused(502, "hr_database_unavailable"), refused(503, "x"),
    new Error("synthetic lost acknowledgement"), new TypeError("Failed to fetch"), "oops", null, undefined, {}, { status: 409 }]) {
    const text = processSaveError(failure, "start");
    assert.equal(text, "We couldn't confirm that. Choose Start process again; it won't be done twice.", String(failure));
  }
  assert.match(processSaveError(null, "close"), /Choose Close anyway again/);
  assert.match(processSaveError(null, "save"), /Choose Save again/);
  assert.match(processSaveError(null, "send-back"), /Choose Send back again/);
});

test("a failed read: a known refusal says so, anything else is a plain retry", () => {
  assert.equal(processLoadError(refused(404, "resource_unavailable")), "That item does not exist, or you cannot see it.");
  assert.equal(processLoadError(refused(403, "read_only_session")), processErrorText("read_only_session"));
  assert.equal(processLoadError(refused(400, "brand_new_code")), PROCESS_LOAD_ERROR);
  for (const failure of [refused(500, "Request failed (500)"), new Error("offline"), null, undefined])
    assert.equal(processLoadError(failure), PROCESS_LOAD_ERROR);
  assert.equal(PROCESS_LOAD_ERROR, "We couldn't load this. Try again.");
});

test("no fallback, uncertain or load sentence tells the person to reload", () => {
  const actions = ["start", "cancel", "close", "save", "deactivate", "complete", "approve", "send-back", "skip", "reschedule", "reassign", "note"];
  const sentences = [PROCESS_ERROR_FALLBACK, PROCESS_LOAD_ERROR, processLoadError(null),
    ...actions.map((action) => processSaveError(null, action)), ...actions.map((action) => processSaveError(refused(500, "x"), action))];
  for (const sentence of sentences) {
    assert.doesNotMatch(sentence, /reload|refresh/i, sentence);
    assert.doesNotMatch(sentence, SNAKE, sentence);
  }
});
