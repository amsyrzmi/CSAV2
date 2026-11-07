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
const {
  refusalCopy, sizeText, pagesText, categoriesOf, filterItems, uploadProblem, summaryNote, summaryText,
  isItemId, isTrainingPagePath, itemHref, PROVIDER_NOTICE, TRUNCATED_NOTE, UNCATEGORISED, MAX_FILE_BYTES,
} = await import(moduleUrl(
  (await readFile(new URL("../lib/training-view.ts", import.meta.url), "utf8")).replace(/import type [^;]+;/g, ""),
));

const ID = "44444444-4444-4444-8444-444444444444";

// Every code docs/CONSOLE_API.md lists for the training routes, and the platform refusals a
// training call can meet on the way there.
const TRAINING_CODES = [
  "training_not_pdf", "training_pdf_damaged", "training_pdf_unreadable", "training_pdf_protected",
  "training_pdf_active_content", "training_file_too_large", "training_pdf_too_many_pages",
  "training_library_full", "training_name_invalid", "training_upload_invalid", "training_key_conflict",
  "training_storage_unavailable", "training_parser_unavailable", "training_not_found", "training_no_text",
  "training_assistant_unavailable", "training_forbidden",
];
const PLATFORM_CODES = [
  "package_inactive", "write permission required", "machine principal has read-only access",
  "human session required", "active tenant membership required", "tenant is not authorized",
  "tenant not found", "platform admin must select client_id explicitly", "invalid csrf token",
  "invalid request origin", "csrf_required",
];
const FALLBACK = "We could not do that. Try again in a minute.";
const STATUS_NUMBERS = /\b(400|401|403|404|409|413|415|422|429|500|502|503|504)\b/;

test("every training and platform refusal has its own plain sentence", () => {
  for (const code of [...TRAINING_CODES, ...PLATFORM_CODES]) {
    const text = refusalCopy(code);
    assert.notEqual(text, FALLBACK, code);
    assert.doesNotMatch(text, /_/, `${code}: an underscore reads as a code`);
    assert.doesNotMatch(text, STATUS_NUMBERS, `${code}: a status number`);
    assert.doesNotMatch(text, /\bHTTP\b|client_id|training_/i, code);
    assert.match(text, /^[A-Z].*\.$/, `${code}: one or more full sentences`);
    assert.doesNotMatch(text, /\byou entered\b|\binvalid\b/i, `${code}: never blame the person`);
  }
});

test("the sentences the spec fixes are word for word", () => {
  assert.equal(refusalCopy("training_no_text"), "This PDF has no text we can read, so it cannot be summarised.");
  assert.equal(refusalCopy("training_assistant_unavailable"), "We could not reach the assistant. Wait a minute and try again.");
  const manage = "Only admins and your company's HR owner can change the library.";
  for (const code of ["training_forbidden", "write permission required", "machine principal has read-only access"])
    assert.equal(refusalCopy(code), manage, code);
  assert.equal(refusalCopy("package_inactive"), "Training library is not switched on for this company. Ask your administrator.");
  assert.match(refusalCopy("training_file_too_large"), /25 MB/);
  assert.match(refusalCopy("training_pdf_too_many_pages"), /300 pages/);
  assert.equal(refusalCopy("training_pdf_unreadable"), refusalCopy("training_pdf_damaged"));
  assert.match(refusalCopy("training_storage_unavailable"), /try again/i);
  assert.match(refusalCopy("training_parser_unavailable"), /try again/i);
});

test("an unknown code, an HTTP fallback code or nothing gets the fallback sentence", () => {
  for (const code of ["something_new", "Request failed (422)", "", "__proto__", "constructor", "toString"])
    assert.equal(refusalCopy(code), FALLBACK, code);
  assert.doesNotMatch(FALLBACK, /_/);
});

test("sizes and page counts read in plain words", () => {
  assert.equal(sizeText(0), "0 bytes");
  assert.equal(sizeText(900), "900 bytes");
  assert.equal(sizeText(2048), "2 KB");
  assert.equal(sizeText(830 * 1024), "830 KB");
  assert.equal(sizeText(2.44 * 1024 * 1024), "2.4 MB");
  assert.equal(sizeText(3 * 1024 * 1024), "3 MB");
  assert.equal(sizeText(25 * 1024 * 1024), "25 MB");
  assert.equal(sizeText(-1), "0 bytes");
  assert.equal(sizeText(Number.NaN), "0 bytes");
  assert.equal(pagesText(1), "1 page");
  assert.equal(pagesText(12), "12 pages");
  assert.equal(pagesText(0), "0 pages");
});

const item = (title, category, extra = {}) => ({
  id: title, title, description: "", category, view_mode: "slides", page_count: 3, byte_size: 1000,
  created_at: "2026-11-07T01:15:00Z", uploaded_by_name: null, has_summary: false, ...extra,
});

test("categories merge by case and spacing, the first spelling wins, empty is Uncategorised", () => {
  assert.equal(UNCATEGORISED, "Uncategorised");
  assert.deepEqual(categoriesOf([item("a", "HR"), item("b", "hr "), item("c", "")]), ["HR", UNCATEGORISED]);
  assert.deepEqual(categoriesOf([item("a", " safety"), item("b", "Onboarding"), item("c", "SAFETY")]), ["Onboarding", "safety"]);
  assert.deepEqual(categoriesOf([item("a", "  ")]), [UNCATEGORISED]);
  assert.deepEqual(categoriesOf([]), []);
});

test("filterItems narrows by category and by title, ignoring case", () => {
  const items = [item("Fire drill", "Safety"), item("Leave policy", "HR"), item("Welcome", ""), item("Fire exits", "safety ")];
  const titles = (filter) => filterItems(items, filter).map((i) => i.title);
  assert.deepEqual(titles({}), ["Fire drill", "Leave policy", "Welcome", "Fire exits"]);
  assert.deepEqual(titles({ category: "SAFETY" }), ["Fire drill", "Fire exits"]);
  assert.deepEqual(titles({ category: UNCATEGORISED }), ["Welcome"]);
  assert.deepEqual(titles({ query: "  FIRE " }), ["Fire drill", "Fire exits"]);
  assert.deepEqual(titles({ category: "hr", query: "fire" }), []);
  assert.deepEqual(titles({ category: null, query: "" }), ["Fire drill", "Leave policy", "Welcome", "Fire exits"]);
});

test("uploadProblem stops the plain cases before anything is sent", () => {
  assert.equal(MAX_FILE_BYTES, 25 * 1024 * 1024);
  assert.equal(uploadProblem({ name: "Policy.pdf", size: 1000 }), null);
  assert.equal(uploadProblem({ name: "POLICY.PDF", size: MAX_FILE_BYTES }), null);
  assert.equal(uploadProblem({ name: "Policy.docx", size: 1000 }), refusalCopy("training_not_pdf"));
  assert.equal(uploadProblem({ name: "pdf", size: 1000 }), refusalCopy("training_not_pdf"));
  assert.match(uploadProblem({ name: "Empty.pdf", size: 0 }), /empty/i);
  assert.equal(uploadProblem({ name: "Big.pdf", size: MAX_FILE_BYTES + 1 }), refusalCopy("training_file_too_large"));
  assert.equal(uploadProblem({ name: "a/b.pdf", size: 10 }), refusalCopy("training_name_invalid"));
  assert.equal(uploadProblem({ name: "line\nbreak.pdf", size: 10 }), refusalCopy("training_name_invalid"));
  assert.equal(uploadProblem({ name: `${"x".repeat(252)}.pdf`, size: 10 }), refusalCopy("training_name_invalid"));
  // A right-to-left override or a line separator would make the name read differently.
  for (const code of [0x202e, 0x2028, 0x2066, 0x200f])
    assert.equal(uploadProblem({ name: `a${String.fromCharCode(code)}fdp.pdf`, size: 10 }), refusalCopy("training_name_invalid"), code.toString(16));
  assert.equal(uploadProblem({ name: "Latihan keselamatan 2026.pdf", size: 10 }), null);
});

const stored = (extra = {}) => ({
  summary: "  Safety first.\n\nThen the exits.  ", key_points: [" Wear boots ", "", "Know the exits"],
  truncated: false, model: "synthetic-model", summarised_at: "2026-11-07T01:15:00Z", ...extra,
});

test("the summary note carries the provider notice, and the truncation sentence only when cut", () => {
  assert.equal(PROVIDER_NOTICE, "Slide text is sent to our language-model provider when you make a summary.");
  assert.equal(TRUNCATED_NOTE, "Summarised from the first part of this PDF.");
  assert.equal(summaryNote(null), PROVIDER_NOTICE);
  assert.ok(summaryNote(stored()).includes(PROVIDER_NOTICE));
  assert.ok(!summaryNote(stored()).includes(TRUNCATED_NOTE));
  assert.ok(summaryNote(stored({ truncated: true })).includes(TRUNCATED_NOTE));
});

test("summaryText trims the model's text into paragraphs and points, and keeps markup as text", () => {
  assert.deepEqual(summaryText(stored()), { paragraphs: ["Safety first.", "Then the exits."], points: ["Wear boots", "Know the exits"] });
  const hostile = summaryText(stored({ summary: "<img src=x onerror=alert(1)> **bold**", key_points: ["<b>x</b>"] }));
  assert.deepEqual(hostile, { paragraphs: ["<img src=x onerror=alert(1)> **bold**"], points: ["<b>x</b>"] });
});

test("isItemId takes a UUID in either case and nothing else", () => {
  assert.equal(isItemId(ID), true);
  assert.equal(isItemId(ID.toUpperCase()), true);
  for (const bad of ["", "new", `${ID}x`, ` ${ID}`, ID.replaceAll("-", ""), "../x"]) assert.equal(isItemId(bad), false, bad);
});

test("isTrainingPagePath accepts the list and item pages only", () => {
  for (const ok of ["/training", "/portal/training", `/training/${ID}`, `/portal/training/${ID}`, `/training/${ID.toUpperCase()}`])
    assert.equal(isTrainingPagePath(ok), true, ok);
  for (const bad of ["/trainingx", "/training/", "/training/abc", `/training/${ID}/x`, "/portal/training/new",
    `/portal/hr/training/${ID}`, "/portal/trainings", `/training/${ID.slice(0, -1)}`])
    assert.equal(isTrainingPagePath(bad), false, bad);
});

test("itemHref opens an item under the viewer's base and keeps the platform tenant", () => {
  assert.equal(itemHref(ID, false, null), `/portal/training/${ID}`);
  assert.equal(itemHref(ID, false, "ignored"), `/portal/training/${ID}`);
  assert.equal(itemHref(ID, true, "a b/1"), `/training/${ID}?client_id=a+b%2F1`);
  assert.equal(itemHref(ID, true, null), `/training/${ID}`);
  assert.equal(itemHref(null, true, "t1"), "/training?client_id=t1");
  assert.equal(itemHref(null, false, null), "/portal/training");
});
