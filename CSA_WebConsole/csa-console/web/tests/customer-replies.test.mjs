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
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const source = await readFile(new URL("../lib/customer-replies.ts", import.meta.url), "utf8");
const replies = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const api = await import(apiUrl);

test("only a mode that replies needs a brand name", () => {
  assert.equal(replies.saveBlockedReason("off", ""), null);
  assert.equal(replies.saveBlockedReason("off", "   "), null);
  assert.equal(replies.saveBlockedReason("auto", "Team Jo"), null);
  for (const mode of ["approve", "auto"]) {
    for (const name of ["", "   "]) {
      assert.equal(replies.saveBlockedReason(mode, name), "Add your brand name first.");
    }
  }
});

test("the panel says what each warning means and leaves replies_off to its own choice", () => {
  assert.deepEqual(replies.panelWarnings(["replies_off", "no_knowledge", "base_only"]), [
    "Your bot has nothing to answer from yet. Add files in Business knowledge.",
    "With Customer support alone, most customer questions go to your team.",
  ]);
  assert.deepEqual(replies.panelWarnings(["replies_off"]), []);
  assert.deepEqual(replies.panelWarnings([]), []);
});

test("the banner shows the most urgent line and never base_only", () => {
  assert.deepEqual(replies.bannerLines(["replies_off"]), ["Your customers aren't getting replies yet."]);
  assert.deepEqual(replies.bannerLines(["replies_off", "no_knowledge"]), ["Your customers aren't getting replies yet."]);
  assert.deepEqual(replies.bannerLines(["no_knowledge"]), ["Your bot has nothing to answer from yet."]);
  assert.deepEqual(replies.bannerLines(["base_only"]), []);
  assert.deepEqual(replies.bannerLines([]), []);
});

test("the choices keep their exact wording", () => {
  assert.deepEqual(replies.MODE_CHOICES.map((choice) => choice.label), [
    "Off: customers get no reply",
    "Staff approve each reply: a draft waits up to 24 hours, then your team gets a ticket and the customer is told you'll follow up",
    "Bot replies by itself: when it can't answer, it hands over to your team",
  ]);
  assert.deepEqual(replies.MODE_CHOICES.map((choice) => choice.mode), ["off", "approve", "auto"]);
});

test("the refusals have sentences", () => {
  assert.equal(new api.ApiError(422, "brand_name_required").code, "brand_name_required");
  assert.ok(replies.refusalText("brand_name_required").includes("brand name"));
  assert.ok(replies.refusalText("package_suspended").includes("suspended"));
  assert.equal(replies.refusalText("something_else"), null);
  assert.equal(replies.saveErrorText(new api.ApiError(422, "Request failed (422)")),
    "Use one line of at most 120 characters for the brand name.");
  assert.equal(replies.saveErrorText(new api.ApiError(422, "brand_name_required")),
    "Add your brand name before turning replies on.");
});
