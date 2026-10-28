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
const { shortcutFor } = await import(moduleUrl(
  await readFile(new URL("../lib/inbox-shortcuts.ts", import.meta.url), "utf8"),
));

const closed = { dialogOpen: false };
const open = { dialogOpen: true };
function press(key, extra = {}) {
  return {
    key, ctrlKey: false, metaKey: false, altKey: false,
    target: { tagName: "DIV" }, ...extra,
  };
}

test("j and k move to the next and previous conversation", () => {
  assert.equal(shortcutFor(press("j"), closed), "next");
  assert.equal(shortcutFor(press("k"), closed), "previous");
});

test("an upper-case J or K works the same", () => {
  assert.equal(shortcutFor(press("J"), closed), "next");
  assert.equal(shortcutFor(press("K"), closed), "previous");
});

test("a missing target counts as a plain page", () => {
  assert.equal(shortcutFor(press("j", { target: null }), closed), "next");
});

test("typing in a field never triggers a shortcut", () => {
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "input", "textarea"]) {
    assert.equal(shortcutFor(press("j", { target: { tagName } }), closed), null, tagName);
    assert.equal(shortcutFor(press("Escape", { target: { tagName } }), closed), null, tagName);
  }
  const editable = { target: { tagName: "DIV", isContentEditable: true } };
  assert.equal(shortcutFor(press("k", editable), closed), null);
  assert.equal(shortcutFor(press("Escape", editable), closed), null);
});

test("a held modifier key leaves the browser shortcut alone", () => {
  for (const modifier of ["ctrlKey", "metaKey", "altKey"]) {
    assert.equal(shortcutFor(press("j", { [modifier]: true }), closed), null, modifier);
    assert.equal(shortcutFor(press("Escape", { [modifier]: true }), closed), null, modifier);
  }
});

test("nothing fires while a dialog or slide-over is open", () => {
  assert.equal(shortcutFor(press("j"), open), null);
  assert.equal(shortcutFor(press("k"), open), null);
  assert.equal(shortcutFor(press("Escape"), open), null);
});

test("Escape closes the customer card only when no dialog is open", () => {
  assert.equal(shortcutFor(press("Escape"), closed), "close");
});

test("any other key does nothing", () => {
  for (const key of ["a", "Enter", " ", "ArrowDown", "jj", "", "Tab"])
    assert.equal(shortcutFor(press(key), closed), null, JSON.stringify(key));
});
