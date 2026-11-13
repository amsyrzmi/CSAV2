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
const source = await readFile(new URL("../lib/training-api.ts", import.meta.url), "utf8");
const training = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { ApiError } = await import(apiUrl);

const ID = "44444444-4444-4444-8444-444444444444";
const KEY = "55555555-5555-4555-8555-555555555555";

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
const shape = (requests) => requests.map(([path, init]) => [init.method ?? "GET", path]);
const pdf = () => new File(["%PDF-1.4 synthetic"], "Induction week.pdf", { type: "application/pdf" });

test("tenant members send no client_id; every route has its own path and method", async () => {
  const [, requests] = await capture(async () => {
    await training.listItems(null);
    await training.uploadItem(null, pdf(), KEY);
    await training.patchItem(null, ID, { title: "Induction", category: "" });
    await training.removeItem(null, ID);
    await training.getSummary(null, ID);
    await training.makeSummary(null, ID);
    await training.makeSummary(null, ID, true);
  }, (path, init) => (init.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json({})));
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/training/items"],
    ["POST", "/console/v1/training/items"],
    ["PATCH", `/console/v1/training/items/${ID}`],
    ["DELETE", `/console/v1/training/items/${ID}`],
    ["GET", `/console/v1/training/items/${ID}/summary`],
    ["POST", `/console/v1/training/items/${ID}/summary`],
    ["POST", `/console/v1/training/items/${ID}/summary`],
  ]);
  assert.deepEqual(JSON.parse(requests[2][1].body), { title: "Induction", category: "" });
  // The summary body is required: {} for a plain read-or-make, regenerate only when asked.
  assert.deepEqual(JSON.parse(requests[5][1].body), {});
  assert.deepEqual(JSON.parse(requests[6][1].body), { regenerate: true });
  for (const [, init] of requests) {
    assert.equal(init.credentials, "same-origin");
    if ((init.method ?? "GET") === "GET") assert.equal(init.headers.has("X-CSRF-Token"), false);
    else assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  }
});

test("an upload is one multipart part named file, with the Idempotency-Key header", async () => {
  const file = pdf();
  const item = { id: ID, title: "Induction week", view_mode: "slides" };
  const [created, requests] = await capture(() => training.uploadItem(null, file, KEY), () => Response.json(item, { status: 201 }));
  assert.deepEqual(created, item);
  const [, init] = requests[0];
  assert.ok(init.body instanceof FormData);
  assert.deepEqual([...init.body.keys()], ["file"]);
  assert.equal(init.body.get("file").name, "Induction week.pdf");
  assert.equal(init.headers.get("Idempotency-Key"), KEY);
  // The browser writes the multipart boundary itself.
  assert.equal(init.headers.has("Content-Type"), false);
});

test("platform admins name the company on every call, the file link included", async () => {
  const tenant = "a b/1";
  const named = "client_id=a+b%2F1";
  const [, requests] = await capture(async () => {
    await training.listItems(tenant);
    await training.uploadItem(tenant, pdf(), KEY);
    await training.patchItem(tenant, ID, { view_mode: "document" });
    await training.removeItem(tenant, ID);
    await training.getSummary(tenant, ID);
    await training.makeSummary(tenant, ID, true);
  });
  assert.deepEqual(shape(requests), [
    ["GET", `/console/v1/training/items?${named}`],
    ["POST", `/console/v1/training/items?${named}`],
    ["PATCH", `/console/v1/training/items/${ID}?${named}`],
    ["DELETE", `/console/v1/training/items/${ID}?${named}`],
    ["GET", `/console/v1/training/items/${ID}/summary?${named}`],
    ["POST", `/console/v1/training/items/${ID}/summary?${named}`],
  ]);
  assert.equal(training.fileUrl(null, ID), `/console/v1/training/items/${ID}/file`);
  assert.equal(training.fileUrl(tenant, ID), `/console/v1/training/items/${ID}/file?${named}`);
  assert.equal(training.fileUrl(null, "a/b"), "/console/v1/training/items/a%2Fb/file");
});

test("a missing summary reads as null; a stored one comes back as sent", async () => {
  const [none] = await capture(() => training.getSummary(null, ID), () => Response.json({ summary: null }));
  assert.equal(none, null);
  const stored = {
    summary: "Safety first.", key_points: ["Wear boots"], truncated: false,
    model: "synthetic-model", summarised_at: "2026-11-07T01:15:00Z",
  };
  const [found] = await capture(() => training.getSummary(null, ID), () => Response.json(stored));
  assert.deepEqual(found, stored);
  const [made] = await capture(() => training.makeSummary(null, ID), () => Response.json(stored));
  assert.deepEqual(made, stored);
});

test("the list comes back as sent", async () => {
  const list = { items: [], categories: ["HR"], can_manage: false };
  const [read] = await capture(() => training.listItems(null), () => Response.json(list));
  assert.deepEqual(read, list);
});

test("a refusal keeps the server's code and status on ApiError", async () => {
  const cases = [
    [422, "training_not_pdf"],
    [413, "training_file_too_large"],
    [409, "training_library_full"],
    [403, "training_forbidden"],
    [403, "write permission required"],
    [403, "package_inactive"],
    [404, "training_not_found"],
    [503, "training_assistant_unavailable"],
  ];
  for (const [status, code] of cases) {
    await assert.rejects(
      capture(() => training.uploadItem(null, pdf(), KEY), () => Response.json({ detail: code }, { status })),
      (error) => error instanceof ApiError && error.code === code && error.status === status,
      code,
    );
  }
});

test("an upload's pieces share one CSRF token, fetched again once when the server refuses it", async () => {
  const original = globalThis.fetch;
  let csrfReads = 0;
  let refuseNext = 0;
  const puts = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") {
      csrfReads += 1;
      return Response.json({ csrf_token: `synthetic-csrf-${csrfReads}` });
    }
    puts.push([path, init.headers["X-CSRF-Token"]]);
    if (refuseNext > 0) {
      refuseNext -= 1;
      return Response.json({ detail: "invalid csrf token" }, { status: 403 });
    }
    return Response.json({});
  };
  try {
    const csrf = training.pieceCsrf();
    const piece = new Blob(["synthetic piece"]);
    await Promise.all([1, 2, 3].map((n) => training.putPiece(null, ID, n, piece, "ab", csrf)));
    assert.equal(csrfReads, 1, "one token for every piece of the upload");
    assert.deepEqual(puts.map(([, token]) => token), ["synthetic-csrf-1", "synthetic-csrf-1", "synthetic-csrf-1"]);

    // The session's token changed: the refused piece fetches a new one once and is sent again.
    puts.length = 0;
    refuseNext = 1;
    await training.putPiece(null, ID, 4, piece, "ab", csrf);
    assert.equal(csrfReads, 2);
    assert.deepEqual(puts.map(([, token]) => token), ["synthetic-csrf-1", "synthetic-csrf-2"]);
    await training.putPiece(null, ID, 5, piece, "ab", csrf);
    assert.equal(csrfReads, 2, "the new token is kept for the next pieces");

    // Refused again with a fresh token: that is the answer, not a loop.
    refuseNext = 2;
    await assert.rejects(
      training.putPiece(null, ID, 6, piece, "ab", csrf),
      (error) => error instanceof ApiError && error.status === 403 && error.code === "invalid csrf token",
    );
    assert.equal(csrfReads, 3);

    // Another 403 is not a CSRF problem: no new token.
    globalThis.fetch = async (path) => {
      if (path === "/console/v1/auth/csrf") { csrfReads += 1; return Response.json({ csrf_token: "x" }); }
      return Response.json({ detail: "training_forbidden" }, { status: 403 });
    };
    await assert.rejects(
      training.putPiece(null, ID, 7, piece, "ab", csrf),
      (error) => error instanceof ApiError && error.code === "training_forbidden",
    );
    assert.equal(csrfReads, 3);
  } finally {
    globalThis.fetch = original;
  }
});
