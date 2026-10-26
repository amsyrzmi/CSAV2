import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const code = ts.transpileModule(
  await readFile(new URL("../lib/page-context-state.ts", import.meta.url), "utf8"),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } },
).outputText;
const { initialPageContextState, pageContextReducer } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

const inbox = { tab: "inbox", section: "open", record: { kind: "conversation", id: "c1", version: 3 } };

test("a tab publishes its context and clears it on leave", () => {
  let state = pageContextReducer(initialPageContextState, { type: "publish", owner: "a", context: inbox });
  assert.deepEqual(state.context, inbox);
  state = pageContextReducer(state, { type: "clear", owner: "a" });
  assert.equal(state.context, null);
});

test("a late cleanup from the previous tab does not erase the new tab's context", () => {
  let state = pageContextReducer(initialPageContextState, { type: "publish", owner: "old", context: inbox });
  state = pageContextReducer(state, { type: "publish", owner: "new", context: { tab: "hr" } });
  state = pageContextReducer(state, { type: "clear", owner: "old" });
  assert.deepEqual(state.context, { tab: "hr" });
});

test("publishing null clears only for the owner", () => {
  let state = pageContextReducer(initialPageContextState, { type: "publish", owner: "a", context: inbox });
  state = pageContextReducer(state, { type: "publish", owner: "b", context: null });
  assert.deepEqual(state.context, inbox);
  state = pageContextReducer(state, { type: "publish", owner: "a", context: null });
  assert.equal(state.context, null);
});
