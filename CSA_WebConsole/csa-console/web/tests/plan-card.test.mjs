import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(name, replacements = {}) {
  let source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(replacements)) source = source.replaceAll(from, to);
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const emailUrl = await load("email-recipients.ts");
const copyUrl = await load("assistant-copy.ts", { '"@/lib/email-recipients"': JSON.stringify(emailUrl) });
const apiUrl = await load("api.ts");
const plan = await import(await load("plan-card.ts", {
  '"@/lib/api"': JSON.stringify(apiUrl),
  '"@/lib/assistant-copy"': JSON.stringify(copyUrl),
  '"@/lib/email-recipients"': JSON.stringify(emailUrl),
}));
const copy = await import(copyUrl);

const step = (status, code, index = 0, kind = "email_send") => ({ index, kind, status, ...(code ? { code } : {}) });
const GENERIC = { en: "This step could not be completed.", ms: "Langkah ini tidak dapat diselesaikan." };

test("a finished step has no reason; a changed or timed-out one says why, in both languages", () => {
  assert.equal(plan.stepReason(step("done"), "en"), null);
  assert.equal(plan.stepReason(step("skipped", "changed"), "en"), "It changed since you reviewed it.");
  assert.equal(plan.stepReason(step("skipped", "changed"), "ms"), "Ia berubah sejak anda menyemaknya.");
  assert.equal(plan.stepReason(step("failed", "plan_deadline"), "en"), "It ran out of time and may have finished. Check before doing it again.");
  assert.equal(plan.stepReason(step("failed", "plan_deadline"), "ms"), "Ia kehabisan masa dan mungkin sudah selesai. Semak dahulu sebelum mengulanginya.");
  assert.equal(plan.stepReason(step("not_run"), "en"), "It was never started.");
  assert.equal(plan.stepReason(step("not_run"), "ms"), "Ia tidak pernah dimulakan.");
});

test("a refusal code with a sentence elsewhere reuses that sentence", () => {
  assert.equal(plan.stepReason(step("failed", "mailbox_not_connected"), "en"), "Connect your Gmail on the Email assistant tab first.");
  assert.equal(plan.stepReason(step("failed", "mailbox_not_connected"), "ms"), "Sambungkan Gmail anda di tab Email assistant dahulu.");
  assert.equal(plan.stepReason(step("failed", "draft_changed"), "en"), "This email changed after you opened it. Review it and send again.");
  assert.equal(plan.stepReason(step("failed", "draft_changed"), "ms"), "E-mel ini telah berubah selepas anda membukanya. Semak dan hantar semula.");
  assert.match(plan.stepReason(step("failed", "package_inactive"), "en"), /not part of your organisation's active service packages/);
  assert.match(plan.stepReason(step("failed", "package_inactive"), "ms"), /bukan sebahagian daripada pakej perkhidmatan aktif/);
});

test("the send limit has its own line: the receipt keeps no wait time", () => {
  assert.equal(plan.stepReason(step("failed", "send_limit_reached"), "en"), "The hourly limit of 20 emails was reached.");
  assert.equal(plan.stepReason(step("failed", "send_limit_reached"), "ms"), "Had 20 e-mel sejam telah dicapai.");
});

test("an unknown code reads as the generic sentence and never as the code", () => {
  for (const code of ["totally_new_refusal", "constructor", "__proto__", "toString", "", undefined]) {
    for (const language of ["en", "ms"]) {
      const text = plan.stepReason(step("failed", code), language);
      assert.equal(text, GENERIC[language], `${code} ${language}`);
    }
  }
  assert.equal(plan.stepReason(step("failed", "a raw code"), "en").includes("raw code"), false);
});

test("every badge has copy in both languages", () => {
  assert.deepEqual(Object.keys(plan.STEP_BADGE).sort(), ["done", "failed", "not_run", "skipped"]);
  const en = { done: "Done", failed: "Failed", skipped: "Skipped", not_run: "Not run" };
  const ms = { done: "Selesai", failed: "Gagal", skipped: "Dilangkau", not_run: "Tidak dijalankan" };
  for (const status of Object.keys(en)) {
    assert.equal(copy.panelText("en", plan.STEP_BADGE[status]), en[status]);
    assert.equal(copy.panelText("ms", plan.STEP_BADGE[status]), ms[status]);
  }
});

test("a deadline-cut step is marked uncertain, other failures are not", () => {
  assert.equal(plan.isUncertain(step("failed", "plan_deadline")), true);
  assert.equal(plan.isUncertain(step("failed", "send_refused")), false);
  assert.equal(plan.isUncertain(step("skipped", "plan_deadline")), false);
  assert.equal(plan.isUncertain(step("done")), false);
});

test("retry is offered only when a step failed or never ran", () => {
  assert.equal(plan.canRetry([]), false);
  assert.equal(plan.canRetry([step("done"), step("skipped", "changed", 1)]), false);
  assert.equal(plan.canRetry([step("done"), step("failed", "send_refused", 1)]), true);
  assert.equal(plan.canRetry([step("done"), step("not_run", undefined, 1)]), true);
});
