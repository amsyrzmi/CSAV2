import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/password-rules.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { MIN_LENGTH, checkRules, rulesPass, announcementFor, passwordStrength } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

const TWELVE = "abcdefghijkl";

test("the minimum is the server's twelve characters", () => {
  assert.equal(MIN_LENGTH, 12);
});

test("nothing typed meets no rule", () => {
  assert.deepEqual(checkRules("", "", ""), { length: false, different: false, match: false });
  assert.equal(rulesPass(checkRules("", "", "")), false);
});

test("eleven characters fail the length rule and twelve pass it", () => {
  assert.equal(checkRules("old", TWELVE.slice(0, 11), "").length, false);
  assert.equal(checkRules("old", TWELVE, "").length, true);
});

test("the new password must differ from the current one", () => {
  assert.equal(checkRules(TWELVE, TWELVE, TWELVE).different, false);
  assert.equal(checkRules("another-old-one", TWELVE, TWELVE).different, true);
});

test("a blank confirmation never matches, an equal one does", () => {
  assert.equal(checkRules("old", TWELVE, "").match, false);
  assert.equal(checkRules("old", TWELVE, TWELVE + "x").match, false);
  assert.equal(checkRules("old", TWELVE, TWELVE).match, true);
});

test("all three rules must pass together", () => {
  assert.equal(rulesPass(checkRules("old-password-1", TWELVE, TWELVE)), true);
  assert.equal(rulesPass(checkRules("old-password-1", TWELVE, "nope")), false);
  assert.equal(rulesPass(checkRules(TWELVE, TWELVE, TWELVE)), false);
});

test("strength by length: empty, short, twelve, sixteen and twenty or more", () => {
  assert.deepEqual(passwordStrength(""), { level: 0, label: "" });
  assert.deepEqual(passwordStrength("short"), { level: 1, label: "Too short" });
  assert.deepEqual(passwordStrength("a".repeat(11)), { level: 1, label: "Too short" });
  assert.deepEqual(passwordStrength("a".repeat(12)), { level: 2, label: "Okay" });
  assert.deepEqual(passwordStrength("a".repeat(15)), { level: 2, label: "Okay" });
  assert.deepEqual(passwordStrength("a".repeat(16)), { level: 3, label: "Good" });
  assert.deepEqual(passwordStrength("a".repeat(19)), { level: 3, label: "Good" });
  assert.deepEqual(passwordStrength("a".repeat(20)), { level: 4, label: "Strong" });
  assert.deepEqual(passwordStrength("a".repeat(64)), { level: 4, label: "Strong" });
});

test("strength looks at length only, so a repeated character is not penalised or praised more", () => {
  assert.equal(passwordStrength("Aa1!Aa1!Aa1!").level, passwordStrength("a".repeat(12)).level);
});

test("the announcement names what is left, and says when all is done", () => {
  assert.equal(
    announcementFor(checkRules("o", "", "")),
    "0 of 3 checks pass. Still needed: At least 12 characters, Different from your current password, Matches the confirmation.",
  );
  assert.equal(
    announcementFor(checkRules("old-password-1", TWELVE, "")),
    "2 of 3 checks pass. Still needed: Matches the confirmation.",
  );
  assert.equal(
    announcementFor(checkRules("old-password-1", TWELVE, TWELVE)),
    "All 3 checks pass. You can save.",
  );
});
