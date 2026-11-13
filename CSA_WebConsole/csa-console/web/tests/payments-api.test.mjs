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
const source = await readFile(new URL("../lib/payments-api.ts", import.meta.url), "utf8");
const pay = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { ApiError } = await import(apiUrl);

const INV = "inv_0001";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

async function capture(run, answer = () => Response.json({})) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return answer(requests.length);
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}
const shape = (requests) => requests.map(([path, init]) => [init.method ?? "GET", path]);

test("tenant members send no client_id; the list sends only the filters given", async () => {
  const [, requests] = await capture(async () => {
    await pay.listInvoices(null, {});
    await pay.listInvoices(null, { status: "proof_submitted", q: "  k7qx 4mf ", before: "inv_0042", limit: 25 });
    await pay.listInvoices(null, { status: "all", q: "   " });
    await pay.getInvoice(null, INV);
    await pay.markPaid(null, INV, {
      request_key: "11111111-1111-4111-8111-111111111111", method_kind: "transfer",
      received_at: "2026-11-12T04:00:00.000Z", note: "Seen in the bank app",
    });
    await pay.voidInvoice(null, "inv/odd id", { reason: "Sent twice" });
    await pay.listAttention(null);
  });
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/payments/invoices"],
    ["GET", "/console/v1/payments/invoices?status=proof_submitted&q=k7qx+4mf&before=inv_0042&limit=25"],
    ["GET", "/console/v1/payments/invoices?status=all"],
    ["GET", `/console/v1/payments/invoices/${INV}`],
    ["POST", `/console/v1/payments/invoices/${INV}/mark-paid`],
    ["POST", "/console/v1/payments/invoices/inv%2Fodd%20id/void"],
    ["GET", "/console/v1/payments/attention"],
  ]);
  // The mark-paid body never carries an amount: the server uses the invoice's.
  assert.deepEqual(JSON.parse(requests[4][1].body), {
    request_key: "11111111-1111-4111-8111-111111111111", method_kind: "transfer",
    received_at: "2026-11-12T04:00:00.000Z", note: "Seen in the bank app",
  });
  assert.deepEqual(JSON.parse(requests[5][1].body), { reason: "Sent twice" });
  for (const [, init] of requests) {
    assert.equal(init.credentials, "same-origin");
    if ((init.method ?? "GET") === "GET") assert.equal(init.headers.has("X-CSRF-Token"), false);
    else assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  }
});

test("platform admins name the tenant first on every call", async () => {
  const [, requests] = await capture(async () => {
    await pay.listInvoices("t1", { status: "open" });
    await pay.getInvoice("t1", INV);
    await pay.markPaid("t1", INV, { request_key: "11111111-1111-4111-8111-111111111111", method_kind: "cash" });
    await pay.voidInvoice("t1", INV, { reason: "x" });
    await pay.listAttention("t1");
  });
  assert.deepEqual(requests.map(([path]) => path), [
    "/console/v1/payments/invoices?client_id=t1&status=open",
    `/console/v1/payments/invoices/${INV}?client_id=t1`,
    `/console/v1/payments/invoices/${INV}/mark-paid?client_id=t1`,
    `/console/v1/payments/invoices/${INV}/void?client_id=t1`,
    "/console/v1/payments/attention?client_id=t1",
  ]);
});

test("money stays the string the server sent", async () => {
  const page = { items: [{ id: INV, amount: "12345678901234567.89", currency: "MYR" }], next_before: null };
  const [result] = await capture(() => pay.listInvoices(null, {}), () => Response.json(page));
  assert.equal(result.items[0].amount, "12345678901234567.89");
  assert.equal(typeof result.items[0].amount, "string");
});

test("a retry or double click on one opened dialog replays the same request key", async () => {
  // The first try fails (a timeout on our side), the retry and a second click succeed.
  const answers = [
    () => Response.json({ detail: "synthetic outage" }, { status: 503 }),
    () => Response.json({ id: INV, status: "paid" }),
    () => Response.json({ id: INV, status: "paid" }),
    () => Response.json({ id: INV, status: "paid" }),
  ];
  const [keys, requests] = await capture(async () => {
    const dialog = pay.openMarkPaid(null, INV);
    assert.match(dialog.requestKey, UUID);
    await assert.rejects(dialog.submit({ method_kind: "transfer" }));
    await dialog.submit({ method_kind: "transfer" });
    await dialog.submit({ method_kind: "transfer", note: "Second click" });
    // Closing and opening the dialog again is a new payment attempt: a new key.
    const again = pay.openMarkPaid(null, INV);
    await again.submit({ method_kind: "cash" });
    return [dialog.requestKey, again.requestKey];
  }, (count) => answers[count - 1]());
  const sent = requests.map(([, init]) => JSON.parse(init.body).request_key);
  assert.deepEqual(sent, [keys[0], keys[0], keys[0], keys[1]]);
  assert.notEqual(keys[0], keys[1]);
  assert.match(keys[1], UUID);
  for (const [, init] of requests) assert.equal("amount" in JSON.parse(init.body), false);
});

test("payment refusals read in plain words; unknown codes fall back to the generic sentence", async () => {
  const verbatim = {
    invoice_already_paid: "Someone already marked this invoice paid.",
    invoice_not_payable: "This invoice can't be paid: it is void.",
    invoice_paid: "A paid invoice can't be voided.",
    received_in_future: "The payment date can't be in the future.",
  };
  for (const [code, sentence] of Object.entries(verbatim)) {
    assert.equal(pay.PAYMENT_ERRORS[code], sentence);
    assert.equal(pay.paymentMessage(new ApiError(409, code, code)), sentence);
  }
  for (const code of ["invoice_not_found", "invoice_void", "request_key_reused", "invalid_note", "invalid_reason"]) {
    const said = pay.paymentMessage(new ApiError(409, code, code));
    assert.notEqual(said, code);
    assert.doesNotMatch(said, /_/, code);
  }
  // A code another page explains keeps that sentence.
  const inactive = pay.paymentMessage(new ApiError(403, "This area is not part", "package_inactive"));
  assert.match(inactive, /not part of your organisation's active service packages/);
  // An unknown code, a FastAPI field list and a network failure all read the same.
  assert.equal(pay.paymentMessage(new ApiError(409, "brand_new_code", "brand_new_code")),
    "The request failed. Try again.");
  assert.equal(pay.paymentMessage(new ApiError(422, "Request failed (422)", "Request failed (422)")),
    "The request failed. Try again.");
  assert.equal(pay.paymentMessage(new TypeError("fetch failed")), "The request failed. Try again.");
  // A server failure keeps the server-failure sentence.
  assert.equal(pay.paymentMessage(new ApiError(503, "synthetic outage", "synthetic outage")),
    "Something went wrong on our side. Try again.");
});

test("only the role refusal means this login has no access", () => {
  assert.equal(pay.isNoAccess(new ApiError(403, "payments access requires an administrator or staff role")), true);
  assert.equal(pay.isNoAccess(new ApiError(403, "x", "package_inactive")), false);
  // A platform admin on a company they cannot reach is not told the page is for admins and staff.
  assert.equal(pay.isNoAccess(new ApiError(403, "payments tenant is not authorized")), false);
  assert.equal(pay.isNoAccess(new ApiError(404, "invoice_not_found")), false);
  assert.equal(pay.isNoAccess(new Error("boom")), false);
});

test("a company this login cannot reach reads as a plain refusal that ends the page", () => {
  const tenant = new ApiError(403, "payments tenant is not authorized");
  assert.equal(pay.paymentMessage(tenant), "You can't open Payments for this company.");
  assert.doesNotMatch(pay.paymentMessage(tenant), /admins and staff/);
  assert.equal(pay.isRefusal(tenant), true);
  assert.equal(pay.isRefusal(new ApiError(403, "x", "package_inactive")), true);
  assert.equal(pay.isRefusal(new ApiError(403, "payments access requires an administrator or staff role")), false);
  assert.equal(pay.isRefusal(new ApiError(409, "invoice_paid")), false);
  assert.equal(pay.isRefusal(new Error("boom")), false);
});

test("a refusal because someone else got there first keeps its sentence for the drawer", () => {
  const stale = {
    invoice_already_paid: "Someone already marked this invoice paid.",
    invoice_not_payable: "This invoice can't be paid: it is void.",
    invoice_paid: "A paid invoice can't be voided.",
    invoice_void: "This invoice is already void.",
  };
  for (const [code, sentence] of Object.entries(stale)) {
    assert.equal(pay.staleMessage(new ApiError(409, code, code)), sentence, code);
  }
  for (const cause of [
    new ApiError(422, "received_in_future", "received_in_future"),
    new ApiError(503, "synthetic outage", "synthetic outage"),
    new ApiError(404, "invoice_not_found", "invoice_not_found"),
    new TypeError("fetch failed"),
  ]) assert.equal(pay.staleMessage(cause), null, String(cause.message));
});

test("invoice statuses have their plain labels", () => {
  assert.deepEqual(pay.STATUS_LABELS, {
    issued: "Waiting for payment",
    overdue: "Overdue",
    proof_submitted: "Customer says paid",
    paid: "Paid",
    void: "Void",
    cash_pending: "Cash at meeting",
    draft: "Draft",
  });
});

// ---------------------------------------------------------------- Settings and methods (Task 13)

const METHOD = "aaaaaaaa-0000-4000-8000-000000000011";

test("settings and methods use their routes; writes carry the CSRF token and the version", async () => {
  const settings = { enabled: true, lead_days: 5, reminder_days: [-3, 0, 7], suspend_after_days: null, version: 2 };
  const transfer = {
    kind: "transfer", label: "Main account",
    details: { bank_name: "Bank Contoh", account_name: "Kedai Contoh", account_number: "512000000012" },
    instructions: "", is_active: true,
  };
  const [, requests] = await capture(async () => {
    await pay.getSettings(null);
    await pay.putSettings(null, settings);
    await pay.listMethods(null);
    await pay.createMethod(null, transfer);
    await pay.updateMethod(null, METHOD, { is_active: false, version: 3 });
    await pay.getSettings("t1");
    await pay.putSettings("t1", settings);
    await pay.listMethods("t1");
    await pay.createMethod("t1", transfer);
    await pay.updateMethod("t1", "odd/id", { label: "Other", version: 1 });
  });
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/payments/settings"],
    ["PUT", "/console/v1/payments/settings"],
    ["GET", "/console/v1/payments/methods"],
    ["POST", "/console/v1/payments/methods"],
    ["PATCH", `/console/v1/payments/methods/${METHOD}`],
    ["GET", "/console/v1/payments/settings?client_id=t1"],
    ["PUT", "/console/v1/payments/settings?client_id=t1"],
    ["GET", "/console/v1/payments/methods?client_id=t1"],
    ["POST", "/console/v1/payments/methods?client_id=t1"],
    ["PATCH", "/console/v1/payments/methods/odd%2Fid?client_id=t1"],
  ]);
  assert.deepEqual(JSON.parse(requests[1][1].body), settings);
  assert.deepEqual(JSON.parse(requests[3][1].body), transfer);
  assert.deepEqual(JSON.parse(requests[4][1].body), { is_active: false, version: 3 });
  for (const [, init] of requests) {
    if ((init.method ?? "GET") === "GET") assert.equal(init.headers.has("X-CSRF-Token"), false);
    else assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  }
});

test("settings refusals read as the agreed sentences", () => {
  const verbatim = {
    no_active_method: "Add an active bank account or DuitNow ID before switching payments on.",
    last_active_method: "Keep one active method while payments are on.",
    version_conflict: "Someone else changed this. Check and save again.",
  };
  for (const [code, sentence] of Object.entries(verbatim)) {
    assert.equal(pay.PAYMENT_ERRORS[code], sentence);
    // The server's real answers: 409 {"detail": "<code>"} (csa/payments/settings.py).
    assert.equal(pay.setupMessage(new ApiError(409, code, code)), sentence);
  }
  // The api.ts sentence for version_conflict is not the one this page uses.
  assert.equal(pay.paymentMessage(new ApiError(409, "version_conflict", "version_conflict")),
    "Someone else changed this. Check and save again.");
  assert.equal(pay.isVersionConflict(new ApiError(409, "version_conflict", "version_conflict")), true);
  assert.equal(pay.isVersionConflict(new ApiError(409, "no_active_method", "no_active_method")), false);
  assert.equal(pay.isVersionConflict(new Error("version_conflict")), false);
  // Bad details are 422 {"detail": "invalid_details"}; a gone method is 404 method_not_found.
  for (const code of ["invalid_details", "method_not_found"]) {
    const said = pay.setupMessage(new ApiError(code === "invalid_details" ? 422 : 404, code, code));
    assert.notEqual(said, code);
    assert.doesNotMatch(said, /_/);
  }
  // A FastAPI field list (the client checks failed to catch something) is not the generic sentence.
  assert.equal(pay.setupMessage(new ApiError(422, "Request failed (422)", "Request failed (422)")),
    "Some of these values aren't allowed. Check them and save again.");
  assert.equal(pay.setupMessage(new ApiError(503, "synthetic outage", "synthetic outage")),
    "Something went wrong on our side. Try again.");
});
