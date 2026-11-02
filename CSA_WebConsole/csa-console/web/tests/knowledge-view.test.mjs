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
  groupSources, pollingNeeded, chipLabel, formatBytes, addedText, jobStateText, kindText,
  looseFiles,
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
