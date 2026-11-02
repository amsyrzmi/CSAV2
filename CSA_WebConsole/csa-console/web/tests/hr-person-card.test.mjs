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
const read = (path) => readFile(new URL(path, import.meta.url), "utf8");
const peopleUrl = moduleUrl(await read("../lib/hr-people.ts"));
const recordsUrl = moduleUrl((await read("../lib/hr-records.ts")).replace('"./hr-people"', JSON.stringify(peopleUrl)));
const card = await import(moduleUrl((await read("../lib/hr-person-card.ts"))
  .replace('"./hr-records"', JSON.stringify(recordsUrl))
  .replace('"./hr-people"', JSON.stringify(peopleUrl))));
const { RECORDS_COPY } = await import(recordsUrl);
const { PEOPLE_COPY } = await import(peopleUrl);

test("a person card's Confirm refusal reads as the profile's sentence, in English and Malay", () => {
  const owner = { status: 409, code: "last_owner_leaving", message: "last_owner_leaving" };
  assert.equal(card.hrPersonRefusal(owner, "en"), "Make someone else the HR owner first. The company always needs one.");
  assert.equal(card.hrPersonRefusal(owner, "ms"), "Jadikan orang lain pemilik HR dahulu. Syarikat sentiasa memerlukan seorang.");
  assert.equal(card.hrPersonRefusal({ code: "manager_loop" }, "en"), PEOPLE_COPY.refusals.manager_loop);
  // Codes the card does not own fall through to the dock's usual error line.
  assert.equal(card.hrPersonRefusal({ code: "proposal_stale" }, "en"), null);
  assert.equal(card.hrPersonRefusal({ code: "proposal_stale" }, "ms"), null);
  assert.equal(card.hrPersonRefusal(new Error("x"), "en"), null);
  assert.equal(card.hrPersonRefusal(null, "ms"), null);
});

test("every person-card refusal has the profile's English and a Malay sentence", () => {
  const codes = Object.keys(card.HR_PERSON_REFUSALS_MS);
  for (const code of [
    "last_owner_leaving", "leaver_has_access", "manager_loop", "own_manager_owner_only",
    "manager_unavailable", "department_name_taken",
  ]) assert.ok(codes.includes(code), code);
  for (const code of codes) {
    const english = RECORDS_COPY.refusals[code] ?? PEOPLE_COPY.refusals[code];
    assert.ok(english, code);
    assert.equal(card.hrPersonRefusal({ code }, "en"), english);
    assert.ok(card.HR_PERSON_REFUSALS_MS[code].trim(), code);
    assert.notEqual(card.HR_PERSON_REFUSALS_MS[code], english, code);
  }
});
