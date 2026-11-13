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
  MAX_VIDEO_BYTES, SOUND_NOTICE, HEVC_NOTE, watchHref, fileKind, durationText, videoStateText, failureCopy,
  canRetry, videoSummaryState, videoLengthProblem, VIDEO_SUMMARY_NOTE, SUMMARY_MAKING, NO_SUMMARY_TEXT,
  videoSummaryStatus, SUMMARY_AFTER_UPLOAD, SUMMARY_UNAVAILABLE, NO_SPEECH_TEXT, NO_AUDIO_TEXT,
} = await import(moduleUrl(
  (await readFile(new URL("../lib/training-view.ts", import.meta.url), "utf8")).replace(/import type [^;]+;/g, ""),
));

const UNSUPPORTED = "Only PDF files and MP4, M4V, MOV or WebM videos can be added. Choose one of those files.";
const ID = "44444444-4444-4444-8444-444444444444";

// Every code docs/CONSOLE_API.md lists for the training routes, and the platform refusals a
// training call can meet on the way there.
const TRAINING_CODES = [
  "training_not_pdf", "training_pdf_damaged", "training_pdf_unreadable", "training_pdf_protected",
  "training_pdf_active_content", "training_file_too_large", "training_pdf_too_many_pages",
  "training_library_full", "training_name_invalid", "training_upload_invalid", "training_key_conflict",
  "training_storage_unavailable", "training_parser_unavailable", "training_not_found", "training_no_text",
  "training_assistant_unavailable", "training_forbidden",
  // Videos (docs/CONSOLE_API.md, "Video uploads", "Video processing", "The video route").
  "training_not_video", "training_piece_invalid", "training_piece_corrupt", "training_piece_missing",
  "training_upload_closed", "training_busy", "training_uploads_in_progress", "training_video_unavailable",
  "training_summary_automatic", "training_retry_not_allowed", "training_transcription_unavailable",
  "training_summary_unavailable", "training_video_unreadable", "training_video_too_long", "training_video_codec",
  "training_range_not_satisfiable",
];
const PLATFORM_CODES = [
  "package_inactive", "write permission required", "machine principal has read-only access",
  "human session required", "active tenant membership required", "tenant is not authorized",
  "tenant not found", "platform admin must select client_id explicitly", "invalid csrf token",
  "invalid request origin", "csrf_required", "authentication required",
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

test("an ended session asks the person to sign in again, not to retry", () => {
  const text = refusalCopy("authentication required");
  assert.equal(text, "Your session has ended. Sign in again to continue.");
  assert.doesNotMatch(text, /_/);
  assert.doesNotMatch(text, STATUS_NUMBERS);
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

test("a category literally named Uncategorised and items with none give one entry", () => {
  assert.deepEqual(categoriesOf([item("a", "Uncategorised"), item("b", ""), item("c", "HR")]), ["HR", "Uncategorised"]);
  assert.deepEqual(categoriesOf([item("a", ""), item("b", " uncategorised ")]), ["uncategorised"]);
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
  assert.equal(uploadProblem({ name: "Policy.docx", size: 1000 }), UNSUPPORTED);
  assert.equal(uploadProblem({ name: "pdf", size: 1000 }), UNSUPPORTED);
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

const MIB = 1024 * 1024;

test("uploadProblem accepts a video beside a PDF, up to 500 MiB", () => {
  assert.equal(MAX_VIDEO_BYTES, 500 * MIB);
  for (const name of ["Induction.mp4", "Induction.M4V", "clip.mov", "Recording.webm"])
    assert.equal(uploadProblem({ name, size: 1000 }), null, name);
  assert.equal(uploadProblem({ name: "Induction.mp4", size: MAX_VIDEO_BYTES }), null);
  assert.equal(uploadProblem({ name: "Induction.mp4", size: MAX_VIDEO_BYTES + 1 }), refusalCopy("training_file_too_large", "video"));
  assert.match(uploadProblem({ name: "Big.mp4", size: 501 * MIB }), /500 MB/);
  assert.equal(uploadProblem({ name: "Old.avi", size: 1000 }), UNSUPPORTED);
  assert.match(refusalCopy("training_not_video"), /MP4/);
  assert.match(uploadProblem({ name: "Empty.mp4", size: 0 }), /empty/i);
  assert.equal(uploadProblem({ name: "a/b.mp4", size: 10 }), refusalCopy("training_name_invalid"));
  // A PDF keeps its own limit; a video over it is fine.
  assert.equal(uploadProblem({ name: "Big.pdf", size: MAX_FILE_BYTES + 1 }), refusalCopy("training_file_too_large"));
});

test("the file's kind follows its extension, in any case", () => {
  assert.equal(fileKind("a.PDF"), "pdf");
  for (const name of ["a.mp4", "a.M4V", "a.mov", "a.WebM"]) assert.equal(fileKind(name), "video", name);
  assert.equal(fileKind("a.avi"), null);
  assert.equal(fileKind("mp4"), null);
});

test("a video's size sentence says 500 MB, not 25 MB, and a full library says videos", () => {
  assert.match(refusalCopy("training_file_too_large", "video"), /500 MB/);
  assert.match(refusalCopy("training_file_too_large"), /25 MB/);
  assert.match(refusalCopy("training_library_full", "video"), /video/i);
  assert.equal(refusalCopy("training_library_full"), refusalCopy("training_library_full", "pdf"));
});

test("the video's length is refused over 60 minutes when it is known, and never when it is not", () => {
  assert.equal(videoLengthProblem(3600), null);
  assert.equal(videoLengthProblem(3601), refusalCopy("training_video_too_long"));
  assert.equal(videoLengthProblem(null), null);
  assert.match(refusalCopy("training_video_too_long"), /60 minutes/);
});

test("the sound notice says the audio goes to the speech service, in plain words", () => {
  assert.match(SOUND_NOTICE, /sound/i);
  assert.match(SOUND_NOTICE, /speech/i);
  assert.match(SOUND_NOTICE, /^[A-Z].*\.$/);
  assert.match(HEVC_NOTE, /some browsers/i);
});

test("watchHref adds the second to the item's page and keeps a platform admin's company", () => {
  assert.equal(watchHref(ID, 195, false, null), `/portal/training/${ID}?t=195`);
  assert.equal(watchHref(ID, 195, true, "t 9"), `/training/${ID}?client_id=t+9&t=195`);
  assert.equal(watchHref(ID, 0, true, null), `/training/${ID}?t=0`);
});

test("durations read in plain words", () => {
  assert.equal(durationText(null), "");
  assert.equal(durationText(45_000), "45 seconds");
  assert.equal(durationText(1_000), "1 second");
  assert.equal(durationText(60_000), "1 min");
  assert.equal(durationText(15 * 60_000 + 20_000), "15 min");
  assert.equal(durationText(3_600_000), "1 h");
  assert.equal(durationText(3_900_000), "1 h 5 min");
});

const video = (extra = {}) => ({
  id: ID, kind: "video", title: "T", description: "", category: "", view_mode: null, page_count: null,
  byte_size: 10, created_at: "2026-11-13T00:00:00Z", uploaded_by_name: null, has_summary: false,
  status: "ready", duration_ms: 3000, failure: null, processing: null, notice: null, ...extra,
});

test("managers see what a video is doing, in words", () => {
  const processing = (extra) => ({ stage: "transcribing", chunks_done: 3, chunks_total: 12, waiting: false, ...extra });
  assert.equal(videoStateText(video()), null);
  assert.equal(videoStateText(video({ status: "uploading" })), "Uploading");
  assert.equal(videoStateText(video({ status: "uploaded", processing: processing({ chunks_done: 0, chunks_total: null }) })),
    "Waiting to be processed");
  assert.equal(videoStateText(video({ status: "transcribing", processing: processing() })), "Transcribing, 3 of 12 parts");
  assert.equal(videoStateText(video({ status: "transcribing", processing: processing({ chunks_total: null }) })), "Transcribing");
  assert.equal(videoStateText(video({ status: "summarising", processing: processing({ stage: "summarising" }) })), "Writing the summary");
  const waiting = videoStateText(video({ status: "uploaded", processing: processing({ waiting: true, chunks_total: null }) }));
  assert.match(waiting, /today/i);
  assert.match(waiting, /^[A-Z].*\.$/);
  assert.equal(videoStateText(video({ status: "failed", failure: "training_video_unreadable" })), refusalCopy("training_video_unreadable"));
});

test("a failed video's sentence has a Try again only for the codes worth another go", () => {
  for (const code of ["training_transcription_unavailable", "training_summary_unavailable", "training_storage_unavailable"])
    assert.equal(canRetry(video({ status: "failed", failure: code })), true, code);
  for (const code of ["training_video_unreadable", "training_video_too_long", "training_video_codec"])
    assert.equal(canRetry(video({ status: "failed", failure: code })), false, code);
  assert.equal(canRetry(video({ status: "ready" })), false);
  assert.equal(canRetry(video({ status: "failed", failure: null })), false);
  assert.equal(failureCopy(null), refusalCopy(""));
  assert.equal(failureCopy("training_video_codec"), refusalCopy("training_video_codec"));
});

test("the summary panel for a video never asks for a summary and never says what is not true", () => {
  assert.equal(videoSummaryState(video({ has_summary: true }), "speech"), "summary");
  assert.equal(videoSummaryState(video({ has_summary: true }), null), "summary");
  assert.equal(videoSummaryState(video(), "no_speech"), "no_speech");
  assert.equal(videoSummaryState(video(), "no_audio"), "no_audio");
  assert.equal(videoSummaryState(video({ has_summary: true }), "no_speech"), "no_speech");
  // Being processed: the summary really is on its way.
  for (const status of ["uploaded", "transcribing", "summarising"])
    assert.equal(videoSummaryState(video({ status }), null), "making", status);
  // Still uploading: nothing is being made yet.
  assert.equal(videoSummaryState(video({ status: "uploading" }), null), "uploading");
  // A failed video has no summary coming: its failure is the only thing to say.
  assert.equal(videoSummaryState(video({ status: "failed", failure: "training_video_unreadable" }), null), "failed");
  assert.equal(videoSummaryState(video({ status: "failed", failure: "training_summary_unavailable" }), "speech"), "failed");
  // A ready video with no summary yet: until the transcript says whether there is speech, say nothing false.
  assert.equal(videoSummaryState(video(), null), "loading");
  assert.equal(videoSummaryState(video(), "speech"), "none");
  assert.doesNotMatch(VIDEO_SUMMARY_NOTE, /slide/i);
});

test("the summary panel's failed state says the failure's sentence, not 'being made'", () => {
  assert.notEqual(failureCopy("training_video_codec"), SUMMARY_MAKING);
  assert.match(NO_SUMMARY_TEXT, /^[A-Z].*\.$/);
});

test("the summary panel says one true sentence per state, and is busy only while something is coming", () => {
  // A failed video's failure is said once, above the player; the summary panel only says there is none.
  assert.deepEqual(videoSummaryStatus("failed", false), { sentence: NO_SUMMARY_TEXT, busy: false });
  assert.deepEqual(videoSummaryStatus("none", false), { sentence: NO_SUMMARY_TEXT, busy: false });
  assert.deepEqual(videoSummaryStatus("making", false), { sentence: SUMMARY_MAKING, busy: true });
  // An upload still arriving: no summary is being made yet.
  assert.deepEqual(videoSummaryStatus("uploading", false), { sentence: SUMMARY_AFTER_UPLOAD, busy: false });
  assert.notEqual(SUMMARY_AFTER_UPLOAD, SUMMARY_MAKING);
  assert.doesNotMatch(SUMMARY_AFTER_UPLOAD, /being made/);
  assert.deepEqual(videoSummaryStatus("no_speech", false), { sentence: NO_SPEECH_TEXT, busy: false });
  assert.deepEqual(videoSummaryStatus("no_audio", false), { sentence: NO_AUDIO_TEXT, busy: false });
  assert.deepEqual(videoSummaryStatus("loading", false), { sentence: "Loading the summary…", busy: true });
  // The transcript could not be read: a short neutral sentence, never an empty busy panel.
  assert.deepEqual(videoSummaryStatus("loading", true), { sentence: SUMMARY_UNAVAILABLE, busy: false });
  assert.match(SUMMARY_UNAVAILABLE, /^[A-Z].*\.$/);
  for (const failure of ["training_video_unreadable", "training_summary_unavailable"])
    assert.notEqual(videoSummaryStatus("failed", false).sentence, failureCopy(failure));
});

test("two open uploads: the sentence says a stopped one can be removed to make room", () => {
  assert.equal(
    refusalCopy("training_uploads_in_progress"),
    "Two videos are already being added. Wait for one to finish, or remove one that stopped, then add this one.",
  );
});
