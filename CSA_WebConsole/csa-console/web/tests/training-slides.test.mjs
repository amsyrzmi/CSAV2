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
const { clampPage, nextPage, previousPage, keyToAction, pageForAction, slideKeyAction } = await import(moduleUrl(
  await readFile(new URL("../lib/training-slides.ts", import.meta.url), "utf8"),
));

test("clampPage keeps the page between the first and the last", () => {
  assert.equal(clampPage(0, 3), 1);
  assert.equal(clampPage(-4, 3), 1);
  assert.equal(clampPage(4, 3), 3);
  assert.equal(clampPage(2, 3), 2);
  assert.equal(clampPage(2.7, 3), 2);
  assert.equal(clampPage(Number.NaN, 3), 1);
  // A deck that has not loaded yet, or reports no pages, still sits on page 1.
  assert.equal(clampPage(5, 0), 1);
  assert.equal(clampPage(5, Number.NaN), 1);
});

test("next and previous stop at both ends", () => {
  assert.equal(nextPage(1, 3), 2);
  assert.equal(nextPage(3, 3), 3);
  assert.equal(previousPage(3, 3), 2);
  assert.equal(previousPage(1, 3), 1);
  assert.equal(nextPage(9, 3), 3);
});

test("a one-page deck never moves", () => {
  for (const action of ["next", "previous", "first", "last"]) assert.equal(pageForAction(action, 1, 1), 1, action);
  assert.equal(nextPage(1, 1), 1);
  assert.equal(previousPage(1, 1), 1);
});

test("pageForAction moves, jumps and leaves fullscreen to the viewer", () => {
  assert.equal(pageForAction("next", 2, 5), 3);
  assert.equal(pageForAction("previous", 2, 5), 1);
  assert.equal(pageForAction("first", 4, 5), 1);
  assert.equal(pageForAction("last", 2, 5), 5);
  assert.equal(pageForAction("fullscreen", 2, 5), 2);
});

test("keys map to slide actions", () => {
  assert.equal(keyToAction("ArrowRight"), "next");
  assert.equal(keyToAction("ArrowDown"), "next");
  assert.equal(keyToAction("PageDown"), "next");
  assert.equal(keyToAction("ArrowLeft"), "previous");
  assert.equal(keyToAction("ArrowUp"), "previous");
  assert.equal(keyToAction("PageUp"), "previous");
  assert.equal(keyToAction("Home"), "first");
  assert.equal(keyToAction("End"), "last");
  assert.equal(keyToAction("f"), "fullscreen");
  assert.equal(keyToAction("F"), "fullscreen");
  for (const other of ["Enter", " ", "Escape", "Tab", "g", "", "Shift", "Control", "Alt", "Meta", "__proto__", "toString"])
    assert.equal(keyToAction(other), null, JSON.stringify(other));
});

test("a key held with a modifier is left to the browser", () => {
  for (const modifier of ["ctrlKey", "metaKey", "altKey", "shiftKey"]) {
    assert.equal(keyToAction("ArrowRight", { [modifier]: true }), null, modifier);
    assert.equal(keyToAction("f", { [modifier]: true }), null, modifier);
    assert.equal(keyToAction("Home", { [modifier]: true }), null, modifier);
  }
  assert.equal(keyToAction("End", { ctrlKey: false, metaKey: false, altKey: false, shiftKey: false }), "last");
});

const ALL_KEYS = ["ArrowRight", "ArrowDown", "PageDown", "ArrowLeft", "ArrowUp", "PageUp", "Home", "End", "f", "F"];
const inViewer = { insideViewer: true, editable: false, canFullscreen: true };

test("slide keys do nothing while focus is outside the viewer, so the page keeps its own scrolling", () => {
  for (const key of ALL_KEYS) {
    assert.equal(slideKeyAction(key, {}, { ...inViewer, insideViewer: false }), null, key);
    assert.equal(slideKeyAction(key, {}, { insideViewer: false, editable: true, canFullscreen: true }), null, key);
  }
});

test("inside the viewer the keys move the slides", () => {
  assert.equal(slideKeyAction("ArrowRight", {}, inViewer), "next");
  assert.equal(slideKeyAction("ArrowDown", {}, inViewer), "next");
  assert.equal(slideKeyAction("PageUp", {}, inViewer), "previous");
  assert.equal(slideKeyAction("Home", {}, inViewer), "first");
  assert.equal(slideKeyAction("End", {}, inViewer), "last");
  assert.equal(slideKeyAction("f", {}, inViewer), "fullscreen");
  assert.equal(slideKeyAction("Enter", {}, inViewer), null);
});

test("f is not taken when this browser cannot go full screen", () => {
  assert.equal(slideKeyAction("f", {}, { ...inViewer, canFullscreen: false }), null);
  assert.equal(slideKeyAction("F", {}, { ...inViewer, canFullscreen: false }), null);
  assert.equal(slideKeyAction("ArrowRight", {}, { ...inViewer, canFullscreen: false }), "next");
});

test("modifier keys and typing in a field inside the viewer are left alone", () => {
  for (const modifier of ["ctrlKey", "metaKey", "altKey", "shiftKey"])
    for (const key of ALL_KEYS) assert.equal(slideKeyAction(key, { [modifier]: true }, inViewer), null, `${modifier} ${key}`);
  for (const key of ALL_KEYS) assert.equal(slideKeyAction(key, {}, { ...inViewer, editable: true }), null, key);
});
