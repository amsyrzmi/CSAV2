import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/password-messages.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { passwordErrorText } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

test("the known password messages read as sentences", () => {
  assert.equal(passwordErrorText("invalid current password", 401), "That is not your current password.");
  assert.equal(
    passwordErrorText("new password must differ from the current password", 422),
    "Choose a new password that is different from your current one.",
  );
  assert.equal(passwordErrorText("authentication required", 401), "Your session has ended. Sign in again.");
});

test("throttling is friendly whatever the server called it", () => {
  assert.equal(
    passwordErrorText("too many attempts", 429),
    "Too many attempts. Wait a few minutes, then try again.",
  );
});

test("an unknown message passes through unchanged", () => {
  assert.equal(passwordErrorText("something new", 500), "something new");
  assert.equal(passwordErrorText("something new"), "something new");
});
