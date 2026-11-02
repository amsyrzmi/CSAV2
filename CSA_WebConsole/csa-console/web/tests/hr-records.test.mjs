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
const read = (path) => readFile(new URL(path, import.meta.url), "utf8");
const apiUrl = moduleUrl(await read("../lib/api.ts"));
const peopleUrl = moduleUrl(await read("../lib/hr-people.ts"));
const { recordsClient } = await import(
  moduleUrl((await read("../lib/hr-records-api.ts")).replace('"./api"', JSON.stringify(apiUrl))));
const records = await import(
  moduleUrl((await read("../lib/hr-records.ts")).replace('"./hr-people"', JSON.stringify(peopleUrl))));
const { historyText, STATUS_LABELS, PEOPLE_COPY, peopleRefusalText } = await import(peopleUrl);
const {
  RECORDS_COPY, PERSONAL_FIELDS, personalFormValues, personalBody, leaveBody, leaveUndoBody, rehireBody,
  documentIssue, recordsRefusalText, leavingSummary,
} = records;

const TENANT = "a b/1";
const SUFFIX = "client_id=a%20b%2F1";
const ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
const DOC = "55555555-5555-4555-8555-555555555555";
const MANAGER = "33333333-3333-4333-8333-333333333333";
const KEY = "66666666-6666-4666-8666-666666666666";
const MB = 1024 * 1024;

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

// ---- transport ----

test("the reads are plain GETs that name the tenant and encode the id", async () => {
  const client = recordsClient(TENANT);
  const [, requests] = await capture(async () => {
    await client.personal(ID);
    await client.documents(ID);
    await client.personal("a/b");
  });
  assert.deepEqual(requests.map(([path]) => path), [
    `/console/v1/hr/people/${ID}/personal?${SUFFIX}`,
    `/console/v1/hr/people/${ID}/documents?${SUFFIX}`,
    `/console/v1/hr/people/a%2Fb/personal?${SUFFIX}`,
  ]);
  for (const [, init] of requests) {
    assert.equal((init.method ?? "GET").toUpperCase(), "GET");
    assert.equal(init.body, undefined);
    assert.equal(new Headers(init.headers).has("X-CSRF-Token"), false);
  }
});

test("reveal posts the field name with a CSRF token and returns the reply as sent", async () => {
  const [answer, requests] = await capture(
    () => recordsClient(TENANT).reveal(ID, "ic_or_passport"), () => Response.json({ value: "SYNTHETIC-12345" }));
  assert.deepEqual(answer, { value: "SYNTHETIC-12345" });
  const [path, init] = requests[0];
  assert.equal(path, `/console/v1/hr/people/${ID}/personal/reveal?${SUFFIX}`);
  assert.equal(init.method, "POST");
  assert.deepEqual(JSON.parse(init.body), { field: "ic_or_passport" });
  assert.equal(new Headers(init.headers).get("X-CSRF-Token"), "synthetic-csrf");
  assert.equal(init.cache, "no-store");
});

test("upload sends one multipart file field, the kind in the query and the caller's key", async () => {
  const file = new File(["%PDF-1.4 synthetic"], "offer letter.pdf", { type: "application/pdf" });
  const [, requests] = await capture(() => recordsClient(TENANT).upload(ID, "contract", file, KEY));
  const [path, init] = requests[0];
  assert.equal(path, `/console/v1/hr/people/${ID}/documents?${SUFFIX}&kind=contract`);
  assert.equal(init.method, "POST");
  const headers = new Headers(init.headers);
  assert.equal(headers.get("Idempotency-Key"), KEY);
  assert.equal(headers.get("X-CSRF-Token"), "synthetic-csrf");
  // The browser supplies the multipart boundary, so no content type is set by hand.
  assert.equal(headers.has("Content-Type"), false);
  assert.ok(init.body instanceof FormData);
  assert.deepEqual([...init.body.keys()], ["file"]);
  assert.equal(init.body.get("file").name, "offer letter.pdf");
});

test("a retry of an upload carries the same key", async () => {
  const file = new File(["x"], "a.pdf");
  const client = recordsClient(TENANT);
  const [, requests] = await capture(async () => {
    await client.upload(ID, "other", file, KEY);
    await client.upload(ID, "other", file, KEY);
  });
  assert.equal(new Headers(requests[0][1].headers).get("Idempotency-Key"), KEY);
  assert.equal(new Headers(requests[1][1].headers).get("Idempotency-Key"), KEY);
});

test("downloadHref is a same-origin link that names the tenant, and does not fetch", async () => {
  const [href, requests] = await capture(async () => recordsClient(TENANT).downloadHref(DOC));
  assert.equal(href, `/console/v1/hr/documents/${DOC}/download?${SUFFIX}`);
  assert.equal(requests.length, 0);
  assert.equal(recordsClient("t").downloadHref("a/b"), "/console/v1/hr/documents/a%2Fb/download?client_id=t");
});

test("removeDocument posts with the key and treats 204 as done", async () => {
  const [answer, requests] = await capture(
    () => recordsClient(TENANT).removeDocument(DOC, KEY), () => new Response(null, { status: 204 }));
  assert.equal(answer, undefined);
  const [path, init] = requests[0];
  assert.equal(path, `/console/v1/hr/documents/${DOC}/remove?${SUFFIX}`);
  assert.equal(init.method, "POST");
  assert.equal(new Headers(init.headers).get("Idempotency-Key"), KEY);
  assert.equal(new Headers(init.headers).get("X-CSRF-Token"), "synthetic-csrf");
});

test("a refusal reaches the caller with its status and server code", async () => {
  await capture(async () => {
    await assert.rejects(() => recordsClient("t").upload(ID, "other", new File(["x"], "a.pdf"), KEY),
      (error) => error.status === 503 && error.code === "hr_documents_unavailable");
  }, () => Response.json({ detail: "hr_documents_unavailable" }, { status: 503 }));
});

// ---- pinned copy ----

test("the pinned reasons, kinds and personal fields", () => {
  assert.deepEqual(RECORDS_COPY.reasons, [
    ["resigned", "Resigned"], ["contract_ended", "Contract ended"], ["dismissed", "Dismissed"],
    ["retired", "Retired"], ["other", "Other"],
  ]);
  assert.deepEqual(RECORDS_COPY.kinds, [
    ["contract", "Contract"], ["identity", "Identity"], ["certificate", "Certificate"], ["other", "Other"],
  ]);
  assert.deepEqual(PERSONAL_FIELDS.map((item) => [item.id, item.label, item.encrypted, item.self]), [
    ["ic_or_passport", "IC or passport number", true, false],
    ["date_of_birth", "Date of birth", false, false],
    ["home_address", "Home address", false, true],
    ["personal_phone", "Personal phone", false, true],
    ["emergency_name", "Emergency contact", false, true],
    ["emergency_relationship", "Relationship", false, true],
    ["emergency_phone", "Emergency contact phone", false, true],
    ["bank_name", "Bank", false, false],
    ["bank_account", "Bank account number", true, false],
    ["tax_number", "Income tax number", true, false],
    ["epf_number", "EPF number", true, false],
    ["socso_number", "SOCSO number", true, false],
  ]);
  assert.equal(STATUS_LABELS.leaving, "Leaving");
});

// ---- personalBody ----

const STORED = {
  date_of_birth: "1990-05-17", home_address: "1 Jalan Synthetic\n50000 Kuala Lumpur", personal_phone: "+60 12-000 0000",
  emergency_name: "Sam Synthetic", emergency_relationship: "Sibling", emergency_phone: "+60 13-000 0000",
  bank_name: "Test Bank", ic_or_passport: "•••• 5678", bank_account: "•••• 4321", tax_number: null,
  epf_number: "••••", socso_number: null, version: 4, can_edit: "all",
};

test("personalFormValues prefills strings, with masked numbers as shown", () => {
  const form = personalFormValues(STORED);
  assert.equal(form.ic_or_passport, "•••• 5678");
  assert.equal(form.tax_number, "");
  assert.equal(form.home_address, "1 Jalan Synthetic\n50000 Kuala Lumpur");
  assert.deepEqual(Object.keys(form), PERSONAL_FIELDS.map((item) => item.id));
  assert.deepEqual(Object.values(personalFormValues(null)), PERSONAL_FIELDS.map(() => ""));
});

test("an untouched form sends every plain field and no encrypted field", () => {
  const body = personalBody(personalFormValues(STORED), STORED, "all", ID);
  assert.deepEqual(body, {
    operation: "personal.save", employee_id: ID, expected_version: 4,
    values: {
      date_of_birth: "1990-05-17", home_address: "1 Jalan Synthetic\n50000 Kuala Lumpur",
      personal_phone: "+60 12-000 0000", emergency_name: "Sam Synthetic", emergency_relationship: "Sibling",
      emergency_phone: "+60 13-000 0000", bank_name: "Test Bank",
    },
    encrypted: {},
  });
});

test("a typed number is sent trimmed, a cleared one as null, an untouched one not at all", () => {
  const form = { ...personalFormValues(STORED), ic_or_passport: " 900517-14-9999 ", bank_account: "", tax_number: "SG123456789" };
  const body = personalBody(form, STORED, "all", ID);
  assert.deepEqual(body.encrypted, { ic_or_passport: "900517-14-9999", bank_account: null, tax_number: "SG123456789" });
  assert.equal("epf_number" in body.encrypted, false);
  assert.equal("socso_number" in body.encrypted, false);
});

test("clearing a number that was never stored sends nothing", () => {
  const form = { ...personalFormValues(STORED), tax_number: "   " };
  assert.deepEqual(personalBody(form, STORED, "all", ID).encrypted, {});
});

test("blank plain fields are sent as null (full replace) and the address keeps its line breaks", () => {
  const form = {
    ...personalFormValues(STORED), personal_phone: "  ", date_of_birth: "", bank_name: "",
    home_address: "  1 Jalan A\r\n\r\nKuala Lumpur \n",
  };
  const { values } = personalBody(form, STORED, "all", ID);
  assert.equal(values.personal_phone, null);
  assert.equal(values.date_of_birth, null);
  assert.equal(values.bank_name, null);
  assert.equal(values.home_address, "1 Jalan A\n\nKuala Lumpur");
});

test("contact mode sends only the five self fields and never a number", () => {
  const form = { ...personalFormValues(STORED), bank_name: "Other Bank", date_of_birth: "2000-01-01",
    ic_or_passport: "SHOULD-NOT-GO-1", personal_phone: "012 345 6789" };
  const body = personalBody(form, { ...STORED, can_edit: "contact" }, "contact", ID);
  assert.deepEqual(Object.keys(body.values),
    ["home_address", "personal_phone", "emergency_name", "emergency_relationship", "emergency_phone"]);
  assert.equal(body.values.personal_phone, "012 345 6789");
  assert.deepEqual(body.encrypted, {});
});

test("the first save has no version, and the same body again is identical (safe to retry)", () => {
  const form = { ...personalFormValues(null), personal_phone: "012 345 6789", ic_or_passport: "900517149999" };
  const first = personalBody(form, null, "all", ID);
  assert.equal(first.expected_version, null);
  assert.deepEqual(first.encrypted, { ic_or_passport: "900517149999" });
  assert.equal(JSON.stringify(personalBody(form, null, "all", ID)), JSON.stringify(first));
});

// ---- leave, undo, rehire ----

test("leaveBody carries the day, reason, version and the reports' new manager", () => {
  assert.deepEqual(
    leaveBody({ employeeId: ID, version: 3, lastDay: "2026-11-30", reason: "resigned", note: "", reportsNewManager: MANAGER }),
    { operation: "person.leave", employee_id: ID, expected_version: 3, last_day: "2026-11-30", reason: "resigned",
      reports_new_manager: MANAGER });
  assert.deepEqual(
    leaveBody({ employeeId: ID, version: 3, lastDay: "2026-11-30", reason: "other", note: "  Moved abroad ", reportsNewManager: "" }),
    { operation: "person.leave", employee_id: ID, expected_version: 3, last_day: "2026-11-30", reason: "other",
      note: "Moved abroad", reports_new_manager: null });
});

test("leaveBody needs the note for Other and keeps it within 200 characters", () => {
  const base = { employeeId: ID, version: 3, lastDay: "2026-11-30", reportsNewManager: null };
  assert.throws(() => leaveBody({ ...base, reason: "other", note: "  " }), /note/i);
  assert.throws(() => leaveBody({ ...base, reason: "other", note: "x".repeat(201) }), /200/);
  assert.equal(leaveBody({ ...base, reason: "other", note: "x".repeat(200) }).note.length, 200);
  assert.throws(() => leaveBody({ ...base, reason: "resigned", note: "", lastDay: "" }), /last day/i);
  assert.throws(() => leaveBody({ ...base, reason: "resigned", note: "", version: undefined }), /version/i);
});

test("a note on a non-Other reason is dropped", () => {
  const body = leaveBody({ employeeId: ID, version: 1, lastDay: "2026-11-30", reason: "retired", note: "left over text", reportsNewManager: null });
  assert.equal("note" in body, false);
});

test("leaveUndoBody and rehireBody", () => {
  assert.deepEqual(leaveUndoBody({ employeeId: ID, version: 5 }),
    { operation: "person.leave_undo", employee_id: ID, expected_version: 5 });
  assert.deepEqual(rehireBody({ employeeId: ID, version: 6, startDate: "2027-01-04" }),
    { operation: "person.rehire", employee_id: ID, expected_version: 6, start_date: "2027-01-04" });
  assert.throws(() => rehireBody({ employeeId: ID, version: 6, startDate: " " }), /start date/i);
  assert.throws(() => rehireBody({ employeeId: ID, version: undefined, startDate: "2027-01-04" }), /version/i);
});

test("leavingSummary reads in plain words", () => {
  const person = (extra) => ({ status: "leaving", last_day: "2026-11-30", leaving: { reason: "resigned", note: null }, ...extra });
  assert.equal(leavingSummary(person()), "Leaving on 30 Nov 2026 (Resigned)");
  assert.equal(leavingSummary(person({ leaving: { reason: "other", note: "Moved abroad" } })),
    "Leaving on 30 Nov 2026 (Other: Moved abroad)");
  assert.equal(leavingSummary(person({ leaving: null })), "Leaving on 30 Nov 2026");
  assert.equal(leavingSummary(person({ status: "left" })), "Left on 30 Nov 2026 (Resigned)");
  assert.equal(leavingSummary(person({ status: "active", last_day: null, leaving: null })), null);
  assert.equal(leavingSummary({ status: "active" }), null);
});

// ---- documentIssue ----

test("documentIssue accepts the four file types in any letter case", () => {
  for (const name of ["a.pdf", "a.PDF", "photo.jpg", "photo.JPEG", "scan.png", "Letter.DocX", "two.dots.pdf"])
    assert.equal(documentIssue({ name, size: 1000 }), null, name);
});

test("documentIssue refuses other types with the pinned sentence", () => {
  for (const name of ["a.exe", "a.doc", "a.txt", "a.pdf.exe", "pdf", "a.", ".pdf.zip", "noextension", "a.xlsx"])
    assert.equal(documentIssue({ name, size: 1000 }), "Choose a PDF, JPG, PNG or Word (.docx) file.", name);
});

test("documentIssue stops at exactly 10 MB", () => {
  assert.equal(documentIssue({ name: "a.pdf", size: 10 * MB }), null);
  assert.equal(documentIssue({ name: "a.pdf", size: 10 * MB + 1 }), "Files can be up to 10 MB.");
});

test("documentIssue refuses an empty file and a name the server would refuse", () => {
  assert.equal(documentIssue({ name: "a.pdf", size: 0 }), "That file is empty.");
  assert.equal(documentIssue({ name: `${"x".repeat(252)}.pdf`, size: 1 }), "Rename the file and try again.");
  assert.equal(documentIssue({ name: `${"x".repeat(251)}.pdf`, size: 1 }), null);
  assert.equal(documentIssue({ name: "a\u0000b.pdf", size: 1 }), "Rename the file and try again.");
  assert.equal(documentIssue({ name: "dir/a.pdf", size: 1 }), "Rename the file and try again.");
  assert.equal(documentIssue({ name: "", size: 1 }), "Rename the file and try again.");
  // Quotes and Unicode are stored as given, so they are fine.
  assert.equal(documentIssue({ name: 'Ahmad "Z" – résumé.pdf', size: 1 }), null);
  assert.equal(documentIssue(new File(["x"], "real.png")), null);
});

// ---- history ----

test("historyText for a personal field never prints a value", () => {
  for (const item of PERSONAL_FIELDS) {
    const expected = `Changed ${item.label}`;
    assert.equal(historyText({ field: item.id, old: "SECRET-OLD-123", new: "SECRET-NEW-456" }), expected, item.id);
    assert.equal(historyText({ field: item.id, old: null, new: null }), expected, item.id);
    assert.equal(historyText({ field: item.id, old: null, new: "SECRET-NEW-456" }), expected, item.id);
  }
});

test("historyText for a document says added or removed", () => {
  assert.equal(historyText({ field: "document", old: null, new: "Contract: offer.pdf" }), "Added Contract: offer.pdf");
  assert.equal(historyText({ field: "document", old: "Contract: offer.pdf", new: null }), "Removed Contract: offer.pdf");
  assert.equal(historyText({ field: "document", old: null, new: null }), "Changed Document");
});

test("historyText for the leaving fields", () => {
  assert.equal(historyText({ field: "last_day", old: null, new: "2026-11-30" }), 'Last day set to "30 Nov 2026"');
  assert.equal(historyText({ field: "last_day", old: "2026-11-30", new: null }), 'Last day cleared (was "30 Nov 2026")');
  assert.equal(historyText({ field: "leaving_reason", old: null, new: "contract_ended" }), 'Leaving reason set to "Contract ended"');
});

// ---- refusals ----

const NEW_CODES = [
  "last_owner_leaving", "leaver_has_access", "last_day_past", "leaving_final", "not_left", "person_not_active",
  "personal_field_refused", "hr_document_type_unsupported", "hr_document_empty", "hr_document_name_invalid",
  "hr_documents_unavailable", "hr_document_file_unavailable", "idempotency_conflict", "unsupported_operation",
  "file exceeds 10 MB", "upload one file", "invalid_hr_input", "hr_retry_required",
];

test("recordsRefusalText has one plain sentence per new code", () => {
  const seen = new Set();
  for (const code of NEW_CODES) {
    const text = recordsRefusalText(code);
    assert.notEqual(text, PEOPLE_COPY.refusalFallback, code);
    assert.ok(text.length > 15, code);
    assert.doesNotMatch(text, /_/, code);
    seen.add(text);
  }
  assert.equal(seen.size, NEW_CODES.length);
});

test("the pinned refusal sentences", () => {
  assert.equal(recordsRefusalText("last_owner_leaving"), "Make someone else the HR owner first. The company always needs one.");
  assert.equal(recordsRefusalText("leaver_has_access"), "Only the HR owner can mark someone with HR access as leaving.");
  assert.equal(recordsRefusalText("last_day_past"), "Choose a last day from today onwards.");
  assert.equal(recordsRefusalText("leaving_final"), "This person has already left. Use Rehire instead.");
  assert.equal(recordsRefusalText("not_left"), "Only someone who has left can be rehired.");
  assert.equal(recordsRefusalText("person_not_active"), "This person has already left.");
  assert.equal(recordsRefusalText("personal_field_refused"),
    "You can only change your own address, personal phone and emergency contact.");
  assert.equal(recordsRefusalText("hr_document_type_unsupported"), "Choose a PDF, JPG, PNG or Word (.docx) file.");
  assert.equal(recordsRefusalText("hr_document_empty"), "That file is empty.");
  assert.equal(recordsRefusalText("hr_document_name_invalid"), "Rename the file and try again.");
  assert.equal(recordsRefusalText("hr_documents_unavailable"),
    "Documents aren't set up for this company yet. Ask your administrator.");
});

test("recordsRefusalText falls back to the people sentences, then to the generic one", () => {
  assert.equal(recordsRefusalText("person_changed"), PEOPLE_COPY.stale);
  assert.equal(recordsRefusalText("manager_loop"), peopleRefusalText("manager_loop"));
  for (const bad of ["__proto__", "constructor", "", "toString", "nope"])
    assert.equal(recordsRefusalText(bad), PEOPLE_COPY.refusalFallback, bad);
});
