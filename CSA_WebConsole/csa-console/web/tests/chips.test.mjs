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
const { toggleChip, nextSingle, chipLabel, isPressed } = await import(moduleUrl(
  await readFile(new URL("../lib/chips.ts", import.meta.url), "utf8"),
));

test("toggleChip adds and removes", () => {
  assert.deepEqual(toggleChip(["a"], "b"), ["a", "b"]);
  assert.deepEqual(toggleChip(["a", "b"], "a"), ["b"]);
  assert.deepEqual(toggleChip([], "a"), ["a"]);
  const current = ["a"];
  toggleChip(current, "b");
  assert.deepEqual(current, ["a"], "the input array is never mutated");
});

test("nextSingle returns the pressed value", () => {
  assert.equal(nextSingle("a", "b"), "b");
  assert.equal(nextSingle("a", "a"), "a", "pressing the chosen chip keeps it chosen");
});

test("count shows only for finite non-negative numbers", () => {
  assert.equal(chipLabel({ value: "x", label: "Open", count: 3 }), "Open (3)");
  assert.equal(chipLabel({ value: "x", label: "Open", count: 0 }), "Open (0)");
  for (const count of [null, undefined, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(chipLabel({ value: "x", label: "Open", count }), "Open", String(count));
  }
  assert.equal(chipLabel({ value: "x", label: "Open" }), "Open");
});

test("isPressed reads single and multi values", () => {
  assert.equal(isPressed("a", "a"), true);
  assert.equal(isPressed("a", "b"), false);
  assert.equal(isPressed(["a", "b"], "b"), true);
  assert.equal(isPressed([], "b"), false);
});
