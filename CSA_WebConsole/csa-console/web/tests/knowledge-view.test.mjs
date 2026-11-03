import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(name) {
  const source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const {
  groupSources, pollingNeeded, chipLabel, formatBytes, addedText, jobStateText, couldNotReadText, kindText,
  looseFiles, pausedReason, kbStatusLine, LIBRARY_PREVIEW, validateSelection, progressWord, SELECTION_MESSAGE,
} = await import(await load("knowledge-view.ts"));

const file = (id, extra = {}) => ({
  id, name: `${id}.pdf`, media_type: "application/pdf", size_bytes: 2048,
  document_id: null, created_at: "2026-11-02T01:00:00Z", ...extra,
});
const document = (id, extra = {}) => ({
  id, title: `Doc ${id}`, source_uri: null, category: null, language: "en", version: 1,
  is_active: true, file_name: `${id}.pdf`, chunk_count: 12, kind: "doc", embed_model: "m",
  created_at: "2026-11-02T02:00:00Z", updated_at: "2026-11-02T02:00:00Z", ...extra,
});
const job = (id, status, extra = {}) => ({
  id, source: "upload", status, files: [{ name: `${id}.pdf`, file_id: null }], attempts: 1,
  error: null, meta: { results: [] }, created_at: "2026-11-02T03:00:00Z",
  started_at: null, finished_at: null, ...extra,
});

test("groupSources puts documents, queued jobs and failed jobs in the right group", () => {
  const files = [file("f1", { document_id: "d1" }), file("f2")];
  const documents = [document("d1"), document("d2", { file_name: null, source_uri: null })];
  const jobs = [
    job("j1", "queued"), job("j2", "running"), job("j3", "failed", { error: "bad pdf" }),
    job("j4", "done"), job("j5", "partial"),
  ];
  const groups = groupSources(files, documents, jobs);
  assert.deepEqual(groups.library.map((c) => [c.id, c.chip]), [["d1", "ready"], ["d2", "ready"]]);
  assert.deepEqual(groups.reading.map((c) => [c.id, c.chip]), [["j1", "reading"], ["j2", "reading"]]);
  assert.deepEqual(
    groups.attention.map((c) => [c.id, c.chip]),
    [["j3", "needs_attention"], ["j5", "needs_attention"]],
  );
  // A finished job has its document in the library, so it makes no card of its own.
  for (const list of Object.values(groups)) assert.ok(!list.some((c) => c.id === "j4"));
  const first = groups.library[0];
  assert.equal(first.title, "Doc d1");
  assert.equal(first.kind, "PDF");
  assert.equal(first.addedAt, "2026-11-02T02:00:00Z");
  assert.deepEqual(first.fileIds, ["f1"]);
  assert.deepEqual(groups.library[1].fileIds, []);
  const labels = Object.fromEntries(first.details.map((d) => [d.label, d.value]));
  assert.equal(labels["Size"], "2 KB");
  assert.equal(labels["Searchable pieces"], "12");
  const failed = groups.attention[0];
  assert.equal(failed.title, "j3.pdf");
  assert.equal(failed.details.find((d) => d.label === "What went wrong").value, "bad pdf");
});

test("job cards link their files and sum their pieces and sizes", () => {
  const files = [file("f1", { size_bytes: 1024 }), file("f2", { size_bytes: 3072 })];
  const jobs = [job("j1", "partial", {
    files: [{ name: "a.pdf", file_id: "f1" }, { name: "b.csv", file_id: "f2" }, { name: "c.docx", file_id: "gone" }],
    meta: { results: [
      { file: "a.pdf", status: "done", error: null, document_id: "d1", chunks: 5, version: 1 },
      { file: "b.csv", status: "failed", error: "no rows", document_id: null, chunks: null, version: null },
    ] },
  })];
  const [card] = groupSources(files, [], jobs).attention;
  assert.deepEqual(card.fileIds, ["f1", "f2"]);
  assert.equal(card.title, "a.pdf, b.csv, c.docx");
  const labels = Object.fromEntries(card.details.map((d) => [d.label, d.value]));
  assert.equal(labels["Size"], "4 KB");
  assert.equal(labels["Searchable pieces"], "5");
  assert.equal(labels["What went wrong"], "b.csv: no rows");
});

test("pollingNeeded only while reading", () => {
  const empty = { library: [], reading: [], attention: [] };
  assert.equal(pollingNeeded(empty), false);
  assert.equal(pollingNeeded(groupSources([], [document("d1")], [job("j3", "failed"), job("j4", "done")])), false);
  assert.equal(pollingNeeded(groupSources([], [], [job("j1", "queued")])), true);
  assert.equal(pollingNeeded(groupSources([], [], [job("j2", "running")])), true);
});

test("chips for unknown job states fall back to needs_attention", () => {
  const groups = groupSources([], [], [job("j9", "exploded"), job("j10", "")]);
  assert.deepEqual(groups.attention.map((c) => c.chip), ["needs_attention", "needs_attention"]);
  assert.equal(groups.reading.length, 0);
  assert.equal(pollingNeeded(groups), false);
  assert.equal(chipLabel("ready"), "Ready");
  assert.equal(chipLabel("reading"), "Reading");
  assert.equal(chipLabel("needs_attention"), "Needs attention");
  assert.equal(jobStateText("exploded"), "Could not be read");
  assert.equal(jobStateText("queued"), "Waiting to be read");
  assert.equal(jobStateText("running"), "Being read now");
});

test("sizes, dates and kinds read in plain words", () => {
  assert.equal(formatBytes(0), "0 bytes");
  assert.equal(formatBytes(512), "512 bytes");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(10 * 1024 * 1024), "10 MB");
  assert.equal(addedText(null), "Date not recorded");
  assert.equal(addedText("nonsense"), "Date not recorded");
  const at = new Date(2026, 10, 2, 9, 5).toISOString();
  assert.equal(addedText(at), "2 Nov 2026, 09:05");
  assert.equal(kindText("a.pdf", "doc"), "PDF");
  assert.equal(kindText("a.docx", "doc"), "Word document");
  assert.equal(kindText("a.csv", "faq"), "Questions and answers");
  assert.equal(kindText(null, "faq"), "Questions and answers");
  assert.equal(kindText(null, "doc"), "Document");
});

test("looseFiles keeps only the files no card shows", () => {
  const files = [file("f1", { document_id: "d1" }), file("f2"), file("f3")];
  const jobs = [job("j1", "queued", { files: [{ name: "x", file_id: "f2" }] })];
  const groups = groupSources(files, [document("d1")], jobs);
  assert.deepEqual(looseFiles(files, groups).map((f) => f.id), ["f3"]);
  assert.deepEqual(looseFiles([], groups), []);
});

test("a list that never loaded shows Unknown, a loaded absence keeps the original words", () => {
  const files = [file("f1", { document_id: "d1" })];
  const docs = [document("d1")];
  const jobs = [job("j1", "queued")];
  const values = (card) => Object.fromEntries(card.details.map((d) => [d.label, d.value]));
  // Loaded but nothing matches: the original strings.
  const loaded = groupSources([], docs, jobs);
  assert.equal(values(loaded.library[0])["Size"], "Not stored");
  assert.equal(values(loaded.library[0])["Reading job"], "Not recorded");
  assert.equal(values(loaded.reading[0])["Size"], "Not stored");
  // The files list failed or is not there yet.
  const noFiles = groupSources([], docs, jobs, { files: false, jobs: true });
  assert.equal(values(noFiles.library[0])["Size"], "Unknown");
  assert.equal(values(noFiles.reading[0])["Size"], "Unknown");
  assert.equal(values(noFiles.library[0])["Reading job"], "Not recorded");
  // The jobs list failed.
  const noJobs = groupSources(files, docs, [], { files: true, jobs: false });
  assert.equal(values(noJobs.library[0])["Reading job"], "Unknown");
  assert.equal(values(noJobs.library[0])["Size"], "2 KB");
});

test("a failed or partial job whose file is already on a library card folds into that card", () => {
  const files = [file("f1", { document_id: "d1" }), file("f2")];
  const results = [{ file: "f1.pdf", status: "done", error: null, document_id: "d1", chunks: 3, version: 1 }];
  const single = job("j1", "partial", {
    files: [{ name: "f1.pdf", file_id: "f1" }], error: "one page unreadable", meta: { results },
  });
  const groups = groupSources(files, [document("d1")], [single]);
  assert.equal(groups.attention.length, 0);
  assert.deepEqual(groups.library[0].foldedJobIds, ["j1"]);
  const note = groups.library[0].details.find((d) => d.label === "What went wrong");
  assert.equal(note.value, "one page unreadable");

  // Two files: the unread one keeps its own card and no file sits on two cards.
  const two = job("j2", "partial", {
    files: [{ name: "f1.pdf", file_id: "f1" }, { name: "f2.pdf", file_id: "f2" }],
    meta: { results: [...results, { file: "f2.pdf", status: "failed", error: "no text", document_id: null, chunks: null, version: null }] },
  });
  const mixed = groupSources(files, [document("d1")], [two]);
  assert.deepEqual(mixed.library[0].foldedJobIds, ["j2"]);
  assert.deepEqual(mixed.attention.map((c) => c.id), ["j2"]);
  assert.deepEqual(mixed.attention[0].fileIds, ["f2"]);
  const all = [...mixed.library, ...mixed.reading, ...mixed.attention].flatMap((c) => c.fileIds);
  assert.equal(new Set(all).size, all.length);

  // A running job for a file already in the library stays its own Being read card.
  const again = job("j3", "running", { files: [{ name: "f1.pdf", file_id: "f1" }] });
  assert.equal(groupSources(files, [document("d1")], [again]).reading.length, 1);
  // A failed job with an unknown file id cannot be proven covered and keeps its card.
  const unknown = job("j4", "failed", { files: [{ name: "x.pdf", file_id: null }] });
  assert.equal(groupSources(files, [document("d1")], [unknown]).attention.length, 1);
});

test("pausedReason gives one plain reason for every paused state", () => {
  assert.equal(pausedReason("ready", true), null);
  assert.match(pausedReason("ready", false), /switched off/);
  assert.match(pausedReason("failed", false), /could not check/);
  assert.match(pausedReason("missing", false), /not available/);
  assert.match(pausedReason("loading", false), /Checking/);
});

test("kbStatusLine counts and never shows 0 for a failed list", () => {
  assert.equal(kbStatusLine({ known: null, attention: 2, questions: 3 }), "We couldn't count everything just now.");
  assert.equal(kbStatusLine({ known: null, attention: null, questions: null }), "We couldn't count everything just now.");
  assert.equal(kbStatusLine({ known: 4, attention: 1, questions: 2 }), "Your assistant knows 4 things. 3 need your attention.");
  // A failed attention or questions list drops the second sentence rather than saying 0.
  assert.equal(kbStatusLine({ known: 4, attention: null, questions: 2 }), "Your assistant knows 4 things.");
  assert.equal(kbStatusLine({ known: 4, attention: 1, questions: null }), "Your assistant knows 4 things.");
  assert.equal(kbStatusLine({ known: 4, attention: 0, questions: 0 }), "Your assistant knows 4 things. Nothing needs your attention.");
  assert.equal(kbStatusLine({ known: 0, attention: 0, questions: 0 }), "Your assistant knows 0 things. Nothing needs your attention.");
});

test("kbStatusLine singular and plural", () => {
  assert.equal(kbStatusLine({ known: 1, attention: 1, questions: 0 }), "Your assistant knows 1 thing. 1 needs your attention.");
  assert.equal(kbStatusLine({ known: 2, attention: 0, questions: 1 }), "Your assistant knows 2 things. 1 needs your attention.");
  assert.equal(kbStatusLine({ known: 1, attention: 1, questions: 1 }), "Your assistant knows 1 thing. 2 need your attention.");
});

test("card plain rows for documents, reading and failed jobs", () => {
  const files = [file("f1", { document_id: "d1" }), file("f2"), file("f3")];
  const documents = [document("d1", { chunk_count: 12 }), document("d2", { chunk_count: 1, file_name: null })];
  const jobs = [
    job("j1", "queued", { files: [{ name: "j1.pdf", file_id: "f2" }] }),
    job("j3", "failed", { error: "bad pdf", files: [{ name: "j3.pdf", file_id: "f3" }] }),
  ];
  const groups = groupSources(files, documents, jobs);
  const rows = (card) => Object.fromEntries(card.plain.map((r) => [r.label, r.value]));
  assert.deepEqual(groups.library[0].plain.map((r) => r.label), ["Added", "Size", "What your assistant can use"]);
  assert.equal(rows(groups.library[0]).Added, addedText("2026-11-02T02:00:00Z"));
  assert.equal(rows(groups.library[0]).Size, "2 KB");
  assert.equal(rows(groups.library[0])["What your assistant can use"], "12 parts your assistant can look up");
  assert.equal(rows(groups.library[1])["What your assistant can use"], "1 part your assistant can look up");
  assert.equal(rows(groups.library[1]).Size, "Not stored");
  assert.equal(rows(groups.reading[0])["What your assistant can use"], "Nothing yet. It is still reading.");
  assert.equal(rows(groups.attention[0])["What your assistant can use"], "Nothing. It could not read this file.");
  // The details rows are untouched.
  assert.deepEqual(groups.library[0].details.map((r) => r.label), ["Size", "Searchable pieces", "Reading job"]);
  // A list that did not load says Unknown for the size.
  const unknown = groupSources(files, documents, jobs, { files: false, jobs: true });
  assert.equal(rows(unknown.library[0]).Size, "Unknown");
});

test("the library shows five cards before Show all", () => {
  assert.equal(LIBRARY_PREVIEW, 5);
});

test("validateSelection rules and message", () => {
  const MESSAGE = "Select one to three non-empty PDF, DOCX or CSV files, at most 10 MB each.";
  assert.equal(SELECTION_MESSAGE, MESSAGE);
  const ok = (name, size = 5) => ({ name, size });
  assert.equal(validateSelection([ok("a.pdf")]), null);
  assert.equal(validateSelection([ok("a.PDF"), ok("b.docx"), ok("c.csv")]), null);
  assert.equal(validateSelection([ok("a.pdf", 10 * 1024 * 1024)]), null);
  assert.equal(validateSelection([ok("a.pdf"), ok("b.pdf"), ok("c.pdf"), ok("d.pdf")]), MESSAGE);
  assert.equal(validateSelection([ok("notes.txt")]), MESSAGE);
  assert.equal(validateSelection([ok("a.pdf", 0)]), MESSAGE);
  assert.equal(validateSelection([ok("a.pdf", 10 * 1024 * 1024 + 1)]), MESSAGE);
  assert.equal(validateSelection([ok("a.pdf"), ok("b.txt")]), MESSAGE);
  assert.equal(validateSelection([ok("pdf")]), MESSAGE);
});

test("progressWord maps every job status", () => {
  assert.equal(progressWord(null), "Waiting");
  assert.equal(progressWord("queued"), "Waiting");
  assert.equal(progressWord("running"), "Reading");
  assert.equal(progressWord("done"), "Ready");
  assert.equal(progressWord("partial"), "Partly read");
  assert.equal(progressWord("failed"), "Could not be read");
  assert.equal(progressWord("something_new"), "Could not be read");
});

test("couldNotReadText says how many tries a failed file had", () => {
  assert.equal(couldNotReadText(3), "Could not be read after 3 tries");
  assert.equal(couldNotReadText(1), "Could not be read after 1 try");
  assert.equal(couldNotReadText(0), "Could not be read");
  assert.equal(couldNotReadText(Number.NaN), "Could not be read");
});
