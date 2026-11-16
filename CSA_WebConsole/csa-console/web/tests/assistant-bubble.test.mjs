import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(javascript) {
  return `data:text/javascript;base64,${Buffer.from(javascript).toString("base64")}`;
}
const compile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const source = await readFile(new URL("../lib/assistant-bubble.ts", import.meta.url), "utf8");
const {
  BUBBLE_WAIT_MS, DISMISSED_KEY, parseDismissed, serializeDismissed, pickBubbleIdea, bubbleMayShow,
} = await import(moduleUrl(compile(source)));

test("the constants are the agreed values", () => {
  assert.equal(BUBBLE_WAIT_MS, 20_000);
  assert.equal(DISMISSED_KEY, "fs-assistant-dismissed-ideas");
});

test("parseDismissed is tolerant of anything that is not a list of ids", () => {
  assert.deepEqual(parseDismissed(null), []);
  assert.deepEqual(parseDismissed("not json"), []);
  assert.deepEqual(parseDismissed("{}"), []);
  assert.deepEqual(parseDismissed('["a","b"]'), ["a", "b"]);
  assert.deepEqual(parseDismissed('["a",1,null,"b"]'), ["a", "b"]);
});

test("serializeDismissed round-trips through parseDismissed", () => {
  assert.deepEqual(parseDismissed(serializeDismissed([])), []);
  assert.deepEqual(parseDismissed(serializeDismissed(["x", "y"])), ["x", "y"]);
});

const ideas = [
  { id: "one", text: "First idea" },
  { id: "two", text: "Second idea" },
  { id: "three", text: "Third idea" },
];

test("pickBubbleIdea gives the first idea that was not dismissed", () => {
  assert.deepEqual(pickBubbleIdea(ideas, [], false), ideas[0]);
  assert.deepEqual(pickBubbleIdea(ideas, ["one"], false), ideas[1]);
  assert.deepEqual(pickBubbleIdea(ideas, ["one", "two"], false), ideas[2]);
});

test("pickBubbleIdea gives nothing for an empty list, all dismissed, or one already shown this visit", () => {
  assert.equal(pickBubbleIdea([], [], false), null);
  assert.equal(pickBubbleIdea(ideas, ["one", "two", "three"], false), null);
  assert.equal(pickBubbleIdea(ideas, [], true), null);
});

const quiet = {
  panelOpen: false, cardOpen: false, onOverview: false, busy: false,
  shownThisVisit: false, idleMs: 20_000, hidden: false,
};

test("bubbleMayShow is true at exactly the wait with every blocker clear", () => {
  assert.equal(bubbleMayShow(quiet), true);
  assert.equal(bubbleMayShow({ ...quiet, idleMs: 60_000 }), true);
});

test("bubbleMayShow is false for each blocker in turn", () => {
  for (const blocker of ["panelOpen", "cardOpen", "onOverview", "busy", "shownThisVisit", "hidden"])
    assert.equal(bubbleMayShow({ ...quiet, [blocker]: true }), false, blocker);
  assert.equal(bubbleMayShow({ ...quiet, idleMs: 19_999 }), false);
});
