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

// ---- Task 9: claim, resolve, snooze, search, help and the selection key ----

const { SHORTCUT_HELP } = await import(moduleUrl(
  await readFile(new URL("../lib/inbox-shortcuts.ts", import.meta.url), "utf8"),
));

/** A target inside an element matching `inside` (a dialog, a menu), as `closest` reports it. */
function within(inside, tagName = "BUTTON") {
  return { tagName, closest: (selector) => (inside && selector.split(",").some(
    (part) => part.trim() === inside) ? {} : null) };
}

test("c, e and s act on the open conversation, upper case too", () => {
  for (const [key, shortcut] of [["c", "claim"], ["C", "claim"], ["e", "resolve"], ["E", "resolve"],
    ["s", "snooze"], ["S", "snooze"]])
    assert.equal(shortcutFor(press(key), closed), shortcut, key);
});

test("slash goes to search, a question mark (Shift and slash) opens the help, x toggles a row", () => {
  assert.equal(shortcutFor(press("/"), closed), "search");
  assert.equal(shortcutFor(press("?", { shiftKey: true }), closed), "help");
  // Some layouts send "?" without Shift: still the help.
  assert.equal(shortcutFor(press("?"), closed), "help");
  assert.equal(shortcutFor(press("x"), closed), "toggle");
  assert.equal(shortcutFor(press("X"), closed), "toggle");
});

const NEW_KEYS = ["c", "C", "e", "E", "s", "S", "/", "?", "x", "X"];

test("the new keys never fire while typing in a field or an editable area", () => {
  for (const key of NEW_KEYS) {
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "input", "textarea", "select"])
      assert.equal(shortcutFor(press(key, { target: { tagName } }), closed), null, `${key} ${tagName}`);
    assert.equal(shortcutFor(press(key, { target: { tagName: "DIV", isContentEditable: true } }), closed),
      null, `${key} editable`);
  }
});

test("the new keys never fire with Ctrl, Cmd or Alt held", () => {
  for (const key of NEW_KEYS)
    for (const modifier of ["ctrlKey", "metaKey", "altKey"])
      assert.equal(shortcutFor(press(key, { [modifier]: true }), closed), null, `${key} ${modifier}`);
});

test("the new keys never fire while a dialog, slide-over or popover is open", () => {
  for (const key of NEW_KEYS) assert.equal(shortcutFor(press(key), open), null, key);
});

test("nothing fires from inside a dialog, popover or menu, even one the screen did not count", () => {
  for (const inside of ['[role="dialog"]', '[role="alertdialog"]', '[role="menu"]', "dialog"]) {
    for (const key of [...NEW_KEYS, "j", "k", "Escape"])
      assert.equal(shortcutFor(press(key, { target: within(inside) }), closed), null, `${key} in ${inside}`);
  }
  // Outside every one of them, the key works.
  assert.equal(shortcutFor(press("j", { target: within(null) }), closed), "next");
  assert.equal(shortcutFor(press("c", { target: within(null) }), closed), "claim");
});

test("a held key repeats only J and K: an action key acts once", () => {
  assert.equal(shortcutFor(press("j", { repeat: true }), closed), "next");
  assert.equal(shortcutFor(press("k", { repeat: true }), closed), "previous");
  for (const key of [...NEW_KEYS, "Escape"])
    assert.equal(shortcutFor(press(key, { repeat: true }), closed), null, key);
});

test("the help lists every shortcut in words", () => {
  assert.deepEqual(SHORTCUT_HELP.map((entry) => entry.key),
    ["J", "K", "X", "C", "E", "S", "/", "?", "Escape"]);
  for (const entry of SHORTCUT_HELP) {
    assert.ok(entry.text.length > 10 && /^[A-Z]/.test(entry.text) && entry.text.endsWith("."), entry.key);
    assert.ok(entry.name.length > 0, entry.key);
  }
});
