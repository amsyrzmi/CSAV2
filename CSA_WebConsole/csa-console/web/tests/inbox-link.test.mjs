import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/inbox-link.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { linkedConversation, pathWithoutConversation } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

const ID = "a0000000-0000-4000-8000-000000000001";

test("the production source holds no runtime alias imports", () => {
  assert.equal(/\bfrom\s+["']@\//.test(source), false);
});

test("a canonical id is accepted and lower-cased", () => {
  assert.equal(linkedConversation(ID), ID);
  assert.equal(linkedConversation(ID.toUpperCase()), ID);
});

test("anything else is ignored", () => {
  for (const value of [
    "", undefined, null, 5, {}, [ID], [ID, ID], ["x"], "not-a-uuid", ` ${ID}`, `${ID} `, `${ID}\n`,
    ID.slice(1), `${ID}0`, ID.replaceAll("-", ""), "'; DROP TABLE conversations; --",
    `${ID}'; DROP TABLE conversations; --`, "../../admin", `${ID}&client_id=x`,
  ]) {
    assert.equal(linkedConversation(value), "", JSON.stringify(value));
  }
});

test("the path loses only the conversation parameter", () => {
  const params = new URLSearchParams(`conversation=${ID}&client_id=abc&tab=x`);
  assert.equal(pathWithoutConversation("/inbox", params), "/inbox?client_id=abc&tab=x");
  assert.equal(pathWithoutConversation("/portal/inbox", new URLSearchParams(`a=1&conversation=${ID}`)), "/portal/inbox?a=1");
});

test("no parameters left means no trailing question mark", () => {
  assert.equal(pathWithoutConversation("/portal/inbox", new URLSearchParams(`conversation=${ID}`)), "/portal/inbox");
  assert.equal(pathWithoutConversation("/portal/inbox", new URLSearchParams()), "/portal/inbox");
});

test("every copy of the parameter goes and the input is left alone", () => {
  const params = new URLSearchParams(`conversation=a&b=2&conversation=c`);
  assert.equal(pathWithoutConversation("/inbox", params), "/inbox?b=2");
  assert.equal(params.toString(), "conversation=a&b=2&conversation=c");
});
