import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// shortDate formats in the runtime's zone (the viewer's, in a browser). Pin it so the expected
// dates hold wherever the suite runs; Node re-reads TZ when it is assigned.
process.env.TZ = "Asia/Kuala_Lumpur";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const read = (file) => readFile(new URL(`../lib/${file}`, import.meta.url), "utf8");
// training-form reuses three libraries that have no runtime imports of their own: each is loaded
// as its own module and the form's import of it points there.
const deps = {
  "@/lib/training-view": moduleUrl(await read("training-view.ts")),
  "@/lib/email-recipients": moduleUrl(await read("email-recipients.ts")),
  "@/lib/services-view": moduleUrl(await read("services-view.ts")),
};
let source = await read("training-form.ts");
for (const [name, url] of Object.entries(deps)) source = source.replaceAll(`"${name}"`, `"${url}"`);
const form = await import(moduleUrl(source));
const view = await import(deps["@/lib/training-view"]);

const ITEM = {
  id: "44444444-4444-4444-8444-444444444444",
  title: "Fire safety",
  description: "Where the exits are.",
  category: "Safety",
  view_mode: "slides",
  page_count: 12,
  byte_size: 2.5 * 1024 * 1024,
  created_at: "2026-11-07T01:15:00Z",
  uploaded_by_name: "Aisyah",
  has_summary: false,
};
const SUMMARY = {
  summary: "First paragraph.\n\n<b>Second</b> paragraph.",
  key_points: ["Know the exits", "  ", "Call 999"],
  truncated: false,
  model: "synthetic-model",
  summarised_at: "2026-11-07T01:15:00Z",
};
const blank = { title: "", description: "", category: "", view_mode: null };

// ---- The form ---------------------------------------------------------------------------------

test("the form starts from an item, or empty for a new PDF", () => {
  assert.deepEqual(form.formFromItem(ITEM), {
    title: "Fire safety", description: "Where the exits are.", category: "Safety", view_mode: "slides",
  });
  assert.deepEqual(form.EMPTY_FORM, blank);
});

test("the title from a file name drops .pdf in any case and keeps 120 characters", () => {
  assert.equal(form.titleFromFileName("Fire safety.pdf"), "Fire safety");
  assert.equal(form.titleFromFileName("  Induction 2026.PDF "), "Induction 2026");
  assert.equal(form.titleFromFileName("notes"), "notes");
  assert.equal([...form.titleFromFileName(`${"é".repeat(130)}.pdf`)].length, 120);
  assert.equal(form.titleFromFileName(`${"😀".repeat(121)}.pdf`), "😀".repeat(120));
});

test("a valid form has no problems; the limits are counted after trimming, in characters", () => {
  assert.deepEqual(form.formProblems({ ...blank, title: "Fire safety" }), {});
  assert.deepEqual(form.formProblems({
    title: `  ${"a".repeat(120)}  `, description: `${"d".repeat(1000)}\n`, category: "c".repeat(60), view_mode: "document",
  }), {});
  // Astral characters count once each, as the server counts them.
  assert.deepEqual(form.formProblems({ ...blank, title: "😀".repeat(120) }), {});
  assert.deepEqual(form.formProblems({ ...blank, title: "Ok", description: "Line one\nLine two\twith a tab\r\nend" }), {});
});

test("each field over its limit, blank or with hidden characters has its own sentence", () => {
  assert.deepEqual(form.formProblems(blank), { title: "Enter a title." });
  assert.deepEqual(form.formProblems({ ...blank, title: "   " }), { title: "Enter a title." });
  assert.equal(form.formProblems({ ...blank, title: "a".repeat(121) }).title,
    "Keep the title to 120 characters or fewer.");
  assert.equal(form.formProblems({ ...blank, title: "Two\nlines" }).title,
    "Remove the line breaks and hidden characters from the title.");
  assert.equal(form.formProblems({ ...blank, title: "Bell\u0007" }).title,
    "Remove the line breaks and hidden characters from the title.");
  assert.equal(form.formProblems({ ...blank, title: "Ok", description: "d".repeat(1001) }).description,
    "Keep the description to 1,000 characters or fewer.");
  assert.equal(form.formProblems({ ...blank, title: "Ok", description: "nul\u0000" }).description,
    "Remove the hidden characters from the description.");
  assert.equal(form.formProblems({ ...blank, title: "Ok", category: "c".repeat(61) }).category,
    "Keep the category to 60 characters or fewer.");
  assert.equal(form.formProblems({ ...blank, title: "Ok", category: "a\tb" }).category,
    "Remove the line breaks and hidden characters from the category.");
  const all = form.formProblems({ title: "", description: "d".repeat(1001), category: "c".repeat(61), view_mode: null });
  assert.deepEqual(Object.keys(all).sort(), ["category", "description", "title"]);
});

test("formToPatch sends every chosen value trimmed when there is nothing to compare with", () => {
  assert.deepEqual(form.formToPatch({
    title: "  Fire safety ", description: " Exits\r\nand drills ", category: " Safety ", view_mode: "document",
  }, null), { title: "Fire safety", description: "Exits\nand drills", category: "Safety", view_mode: "document" });
  // No mode chosen for a new PDF: the server's choice from the first page stays.
  assert.deepEqual(form.formToPatch({ ...blank, title: "x" }, null), { title: "x", description: "", category: "" });
});

test("formToPatch sends only what changed, so an unchanged form sends nothing", () => {
  assert.deepEqual(form.formToPatch(form.formFromItem(ITEM), ITEM), {});
  assert.deepEqual(form.formToPatch({ ...form.formFromItem(ITEM), title: " Fire safety  " }, ITEM), {});
  assert.deepEqual(form.formToPatch({ ...form.formFromItem(ITEM), category: "" }, ITEM), { category: "" });
  assert.deepEqual(form.formToPatch({ ...form.formFromItem(ITEM), view_mode: "document" }, ITEM),
    { view_mode: "document" });
  assert.deepEqual(form.formToPatch({ ...form.formFromItem(ITEM), view_mode: null }, ITEM), {});
  // After an upload the item carries the title from the file name; a kept title is not sent.
  const uploaded = { ...ITEM, title: "Fire safety", description: "", category: "", view_mode: "document" };
  assert.deepEqual(form.formToPatch({ ...blank, title: "Fire safety", category: "Safety" }, uploaded),
    { category: "Safety" });
});

test("the sentence after a failed second step is exact", () => {
  assert.equal(form.PATCH_FAILED, "The PDF was added under its file name. Edit its details to rename it.");
});

// ---- One idempotency key per picked file ------------------------------------------------------

test("a retry of the same file reuses its key; another file gets a new one", () => {
  let n = 0;
  const fresh = () => `key-${++n}`;
  const a = { name: "a.pdf", size: 10, lastModified: 1 };
  const first = form.attemptFor(null, a, fresh);
  assert.deepEqual(first, { file: a, key: "key-1" });
  assert.equal(form.attemptFor(first, { ...a }, fresh), first, "the same file picked again keeps the key");
  assert.equal(n, 1);
  for (const other of [{ ...a, name: "b.pdf" }, { ...a, size: 11 }, { ...a, lastModified: 2 }]) {
    const next = form.attemptFor(first, other, fresh);
    assert.notEqual(next.key, first.key);
    assert.deepEqual(next.file, other);
  }
});

test("a failed upload keeps its key for the retry, except when the server says the key is mixed up", () => {
  const attempt = { file: { name: "a.pdf", size: 10, lastModified: 1 }, key: "key-1" };
  for (const code of ["training_storage_unavailable", "training_parser_unavailable", "Request failed (502)", ""])
    assert.equal(form.afterUploadFailure(attempt, code), attempt, code);
  assert.equal(form.afterUploadFailure(attempt, "training_key_conflict"), null);
  assert.equal(form.afterUploadFailure(null, "training_storage_unavailable"), null);
});

// ---- Dates and the facts on a row --------------------------------------------------------------

test("dates read as day, short month and year, and the summary line adds 24-hour time", () => {
  assert.equal(form.addedText(ITEM), "Added 7 Nov 2026");
  assert.equal(form.addedText({ ...ITEM, created_at: "not a date" }), "");
  assert.equal(form.madeOnText("2026-11-07T01:15:00Z", "Asia/Kuala_Lumpur"), "Made on 7 Nov 2026, 09:15");
  assert.equal(form.madeOnText("2026-11-07T14:05:00Z", "Asia/Kuala_Lumpur"), "Made on 7 Nov 2026, 22:05");
  assert.equal(form.madeOnText("nonsense"), "");
});

test("a row says category, pages, size, date and who added it, skipping what is missing", () => {
  assert.deepEqual(form.itemFacts(ITEM), ["Safety", "12 pages", "2.5 MB", "Added 7 Nov 2026", "Added by Aisyah"]);
  assert.deepEqual(form.itemFacts({ ...ITEM, category: "  ", uploaded_by_name: null, page_count: 1, byte_size: 900 }),
    ["Uncategorised", "1 page", "900 bytes", "Added 7 Nov 2026"]);
});

// ---- The summary panel --------------------------------------------------------------------------

test("while the stored summary is read the panel is busy and offers nothing", () => {
  const v = form.summaryView({ phase: "reading" }, true);
  assert.equal(v.busy, true);
  assert.equal(v.status, "Loading the summary…");
  assert.equal(v.ask, false);
  assert.equal(v.notice, null);
  assert.equal(v.stored, null);
  assert.equal(v.regenerate, false);
  assert.equal(v.error, null);
});

test("with no summary the button and the provider notice show, for every role", () => {
  for (const canManage of [true, false]) {
    const v = form.summaryView({ phase: "ready", summary: null, error: null }, canManage);
    assert.equal(v.ask, true);
    assert.equal(v.askDisabled, false);
    assert.equal(v.notice, view.PROVIDER_NOTICE);
    assert.equal(v.busy, false);
    assert.equal(v.status, null);
    assert.equal(v.stored, null);
    assert.equal(v.regenerate, false);
  }
});

test("making the first summary keeps the notice, disables the button and says so", () => {
  const v = form.summaryView({ phase: "making", summary: null, regenerate: false }, false);
  assert.equal(v.busy, true);
  assert.equal(v.status, "Making the summary…");
  assert.equal(v.ask, true);
  assert.equal(v.askDisabled, true);
  assert.equal(v.notice, view.PROVIDER_NOTICE);
});

test("a stored summary is plain paragraphs and points with its notes; only managers may replace it", () => {
  const reader = form.summaryView({ phase: "ready", summary: SUMMARY, error: null }, false, "Asia/Kuala_Lumpur");
  assert.equal(reader.ask, false);
  assert.equal(reader.notice, null);
  assert.deepEqual(reader.stored.paragraphs, ["First paragraph.", "<b>Second</b> paragraph."]);
  assert.deepEqual(reader.stored.points, ["Know the exits", "Call 999"]);
  assert.equal(reader.stored.note, view.PROVIDER_NOTICE);
  assert.equal(reader.stored.madeOn, "Made on 7 Nov 2026, 09:15");
  assert.equal(reader.stored.model, "synthetic-model");
  assert.equal(reader.regenerate, false);
  const manager = form.summaryView({ phase: "ready", summary: { ...SUMMARY, truncated: true }, error: null }, true);
  assert.equal(manager.regenerate, true);
  assert.equal(manager.regenerateDisabled, false);
  assert.equal(manager.stored.note, `${view.TRUNCATED_NOTE} ${view.PROVIDER_NOTICE}`);
});

test("making a new summary keeps the old one on screen until the new one arrives", () => {
  const v = form.summaryView({ phase: "making", summary: SUMMARY, regenerate: true }, true);
  assert.equal(v.busy, true);
  assert.equal(v.status, "Making a new summary…");
  assert.equal(v.ask, false);
  assert.deepEqual(v.stored.points, ["Know the exits", "Call 999"]);
  assert.equal(v.regenerate, true);
  assert.equal(v.regenerateDisabled, true);
});

test("a failure is a plain sentence and leaves the way to try again", () => {
  for (const [code, sentence] of [
    ["training_no_text", "This PDF has no text we can read, so it cannot be summarised."],
    ["training_assistant_unavailable", "We could not reach the assistant. Wait a minute and try again."],
    ["training_parser_unavailable", "We could not check the PDF just now. Wait a minute and try again."],
    ["training_pdf_unreadable", view.refusalCopy("training_pdf_damaged")],
    ["training_forbidden", "Only admins and your company's HR owner can change the library."],
    ["Request failed (502)", "We could not do that. Try again in a minute."],
  ]) {
    const none = form.summaryView({ phase: "ready", summary: null, error: code }, false);
    assert.equal(none.error, sentence, code);
    assert.equal(none.ask, true);
    const kept = form.summaryView({ phase: "ready", summary: SUMMARY, error: code }, true);
    assert.equal(kept.error, sentence, code);
    assert.notEqual(kept.stored, null);
    assert.equal(kept.regenerate, true);
  }
});
