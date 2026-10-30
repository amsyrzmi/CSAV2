import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/auth-copy.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
} }).outputText;
const { AUTH_TAGLINE } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("the sign-in panel carries one exact tagline", () => {
  assert.equal(AUTH_TAGLINE, "Your customer team, always on.");
});

test("the tagline avoids the claims the owner ruled out", () => {
  assert.doesNotMatch(AUTH_TAGLINE, /never sleeps/i);
  assert.doesNotMatch(AUTH_TAGLINE, /every customer/i);
});
