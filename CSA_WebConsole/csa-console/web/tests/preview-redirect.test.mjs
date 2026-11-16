import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/preview-redirect.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
} }).outputText;
const { previewHref } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("the old chat links land on the Services preview section", () => {
  assert.equal(previewHref("/services", {}), "/services?section=preview");
  assert.equal(previewHref("/portal/services", {}), "/portal/services?section=preview");
});

test("every incoming query parameter survives, the company first among them", () => {
  assert.equal(previewHref("/services", { client_id: "t-1" }), "/services?client_id=t-1&section=preview");
  assert.equal(previewHref("/services", { client_id: "t-1", x: ["a", "b"], gone: undefined }),
    "/services?client_id=t-1&x=a&x=b&section=preview");
  assert.equal(previewHref("/portal/services", { client_id: "a b&c" }),
    "/portal/services?client_id=a+b%26c&section=preview");
});

test("section is always preview, whatever the old link said", () => {
  assert.equal(previewHref("/services", { section: "other", client_id: "t" }),
    "/services?client_id=t&section=preview");
  assert.equal(previewHref("/services", { section: ["a", "b"] }), "/services?section=preview");
});
