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
const logic = await import(moduleUrl(
  await readFile(new URL("../lib/training-viewer-logic.ts", import.meta.url), "utf8"),
));
const {
  FileResponseError, OPENING, viewerStateFor, viewerMessage, fitScale, MAX_CANVAS_PIXELS,
  ZOOM_STEPS, zoomIn, zoomOut, swipeAction, renderWindow, inWindow, slideCounter, ignoresKeys,
} = logic;

const named = (name, message = "", extra = {}) => Object.assign(new Error(message), { name }, extra);

test("the loading sentence is the brief's", () => {
  assert.equal(OPENING, "Opening the PDF…");
});

test("a missing file says it is no longer in the library", () => {
  assert.equal(viewerStateFor(new FileResponseError(404)), "gone");
  assert.equal(viewerMessage("gone"), "This PDF is no longer in the library.");
});

test("storage trouble, a network failure and anything unknown ask to try again", () => {
  assert.equal(viewerStateFor(new FileResponseError(503)), "unavailable");
  assert.equal(viewerStateFor(new FileResponseError(500)), "unavailable");
  assert.equal(viewerStateFor(new TypeError("Failed to fetch")), "unavailable");
  assert.equal(viewerStateFor(named("UnknownErrorException", "boom", { details: "Error: boom" })), "unavailable");
  assert.equal(viewerStateFor("a string"), "unavailable");
  assert.equal(viewerStateFor(null), "unavailable");
  assert.equal(viewerMessage("unavailable"), "We could not open this PDF. Try again in a minute.");
});

test("a damaged file names the damage and who can fix it", () => {
  assert.equal(viewerStateFor(named("InvalidPDFException", "Invalid PDF structure.")), "damaged");
  assert.equal(viewerStateFor(named("FormatError", "Bad xref")), "damaged");
  // A FormatError thrown in the worker reaches the page wrapped as an unknown error.
  assert.equal(
    viewerStateFor(named("UnknownErrorException", "Bad xref", { details: "FormatError: Bad xref" })),
    "damaged",
  );
  assert.equal(
    viewerMessage("damaged"),
    "This PDF is damaged, so we cannot show it. Ask the person who added it to upload it again.",
  );
});

test("a password-protected file says so", () => {
  assert.equal(viewerStateFor(named("PasswordException", "No password given", { code: 1 })), "protected");
  assert.equal(viewerMessage("protected"), "This PDF is protected, so we cannot show it.");
});

test("no sentence carries raw error text or a status number", () => {
  for (const state of ["unavailable", "gone", "damaged", "protected"]) {
    assert.doesNotMatch(viewerMessage(state), /\d{3}|Exception|Error/);
  }
});

test("a slide fits inside its box and keeps its shape (letterboxed)", () => {
  // A 4:3 page in a wide box is limited by the height.
  assert.deepEqual(fitScale(800, 600, 1600, 600, 1), { scale: 1, outputScale: 1 });
  // A landscape page in a tall box is limited by the width.
  assert.deepEqual(fitScale(800, 450, 400, 900, 1), { scale: 0.5, outputScale: 1 });
  // An open-ended height fits the width (document mode).
  assert.deepEqual(fitScale(600, 800, 900, Number.POSITIVE_INFINITY, 1), { scale: 1.5, outputScale: 1 });
});

test("the pixel ratio is at least 1 and capped at 2", () => {
  assert.equal(fitScale(800, 600, 800, 600, 3).outputScale, 2);
  assert.equal(fitScale(800, 600, 800, 600, 1.5).outputScale, 1.5);
  assert.equal(fitScale(800, 600, 800, 600, 0.5).outputScale, 1);
  assert.equal(fitScale(800, 600, 800, 600, Number.NaN).outputScale, 1);
});

test("a huge page never asks for a canvas over the pixel budget", () => {
  const { scale, outputScale } = fitScale(1000, 1000, 6000, Number.POSITIVE_INFINITY, 2);
  assert.equal(scale, 6);
  const pixels = (1000 * scale * outputScale) ** 2;
  assert.ok(pixels <= MAX_CANVAS_PIXELS, `${pixels} pixels`);
  assert.ok(outputScale > 0);
});

test("nothing is drawn before the box or the page has a size", () => {
  assert.equal(fitScale(800, 600, 0, 600, 1), null);
  assert.equal(fitScale(800, 600, 800, 0, 1), null);
  assert.equal(fitScale(0, 600, 800, 600, 1), null);
  assert.equal(fitScale(Number.NaN, 600, 800, 600, 1), null);
  assert.equal(fitScale(800, 600, -5, 600, 1), null);
});

test("zoom steps up and down and stops at both ends", () => {
  assert.equal(ZOOM_STEPS.includes(1), true);
  assert.equal(zoomIn(1), 1.25);
  assert.equal(zoomOut(1), 0.75);
  assert.equal(zoomIn(ZOOM_STEPS.at(-1)), ZOOM_STEPS.at(-1));
  assert.equal(zoomOut(ZOOM_STEPS[0]), ZOOM_STEPS[0]);
  // A value between steps moves to the next step in that direction.
  assert.equal(zoomIn(1.1), 1.25);
  assert.equal(zoomOut(1.1), 1);
});

test("a swipe to the left goes forward, to the right goes back", () => {
  assert.equal(swipeAction(-80, 5), "next");
  assert.equal(swipeAction(80, -5), "previous");
});

test("short or mostly vertical swipes do nothing", () => {
  assert.equal(swipeAction(-20, 0), null);
  assert.equal(swipeAction(-80, 70), null);
  assert.equal(swipeAction(0, 0), null);
  assert.equal(swipeAction(Number.NaN, 0), null);
});

test("pages near the visible ones are drawn, the rest wait", () => {
  assert.deepEqual(renderWindow([5, 6], 300, 2), { first: 3, last: 8 });
  assert.deepEqual(renderWindow(new Set([1]), 3, 2), { first: 1, last: 3 });
  assert.deepEqual(renderWindow([300], 300, 2), { first: 298, last: 300 });
  assert.equal(renderWindow([], 300, 2), null);
  const window = renderWindow([10], 300, 1);
  assert.equal(inWindow(9, window), true);
  assert.equal(inWindow(12, window), false);
  assert.equal(inWindow(1, null), false);
});

test("the counter reads Slide n of total", () => {
  assert.equal(slideCounter(3, 12), "Slide 3 of 12");
  assert.equal(slideCounter(1, 1), "Slide 1 of 1");
});

test("keys typed into a field are not slide keys", () => {
  assert.equal(ignoresKeys({ tagName: "INPUT" }), true);
  assert.equal(ignoresKeys({ tagName: "TEXTAREA" }), true);
  assert.equal(ignoresKeys({ tagName: "SELECT" }), true);
  assert.equal(ignoresKeys({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(ignoresKeys({ tagName: "BUTTON" }), false);
  assert.equal(ignoresKeys({ tagName: "BODY" }), false);
  assert.equal(ignoresKeys(null), false);
});
