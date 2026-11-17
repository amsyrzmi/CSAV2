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
const read = (name) => readFile(new URL(name, import.meta.url), "utf8");
const peopleUrl = moduleUrl(await read("../lib/hr-people.ts"));
const {
  anchorsFor, emptyDraft, emptyStep, draftFromTemplate, copyDraft, rulesBody, stepBody, createBody, updateBody,
  stepProblems, draftProblems, hasProblems, rulesText, templateTies, previewText, warningText, moveItem, sameDraft,
  textLength, stepName, legalEntities, withKind, TEMPLATE_LIMITS, CHECK_CHOICES, cleanText, unsavable, FORMER_OWNER,
} = await import(
  moduleUrl((await read("../lib/hr-template-form.ts")).replace('"./hr-people"', JSON.stringify(peopleUrl))));

const DEPARTMENT = "11111111-1111-4111-8111-000000000001";
const PERSON = "55555555-5555-4555-8555-000000000002";
const step = (overrides = {}) => ({ ...emptyStep("onboarding"), title_en: "Sign the contract", ...overrides });

test("anchorsFor offers the dates each kind counts from", () => {
  assert.deepEqual(anchorsFor("onboarding"), ["start_date", "probation_end"]);
  assert.deepEqual(anchorsFor("offboarding"), ["last_day"]);
  assert.deepEqual(anchorsFor("custom"), ["start_date", "last_day", "probation_end"]);
});

test("a new step counts from the kind's first date and is required", () => {
  const onboarding = emptyStep("onboarding");
  assert.equal(onboarding.anchor, "start_date");
  assert.equal(onboarding.required, true);
  assert.equal(onboarding.owner_role, "hr");
  assert.equal(onboarding.due_offset_days, "0");
  assert.equal(emptyStep("offboarding").anchor, "last_day");
  assert.notEqual(emptyStep("custom").key, emptyStep("custom").key);
  const draft = emptyDraft();
  assert.equal(draft.kind, "onboarding");
  assert.equal(draft.steps.length, 1);
});

test("withKind moves every anchor the new kind does not allow to its first date", () => {
  const draft = { ...emptyDraft(), steps: [step({ anchor: "probation_end" }), step({ anchor: "start_date" })] };
  const off = withKind(draft, "offboarding");
  assert.equal(off.kind, "offboarding");
  assert.deepEqual(off.steps.map((s) => s.anchor), ["last_day", "last_day"]);
  const custom = withKind(off, "custom");
  assert.deepEqual(custom.steps.map((s) => s.anchor), ["last_day", "last_day"]);
  const back = withKind({ ...draft }, "onboarding");
  assert.deepEqual(back.steps.map((s) => s.anchor), ["probation_end", "start_date"]);
});

test("rulesBody sends every rule, trimmed, or null when none is set", () => {
  const none = { department_id: "", employment_type: "", legal_entity_reference: "  ", region: "" };
  assert.equal(rulesBody(none), null);
  assert.deepEqual(rulesBody({ ...none, region: "sabah", legal_entity_reference: " Syarikat Cekap " }), {
    department_id: null, employment_type: null, legal_entity_reference: "Syarikat Cekap", region: "sabah",
  });
});

test("stepBody trims text, keeps line breaks and sends empty optional text as null", () => {
  const body = stepBody(step({
    title_en: "  Sign the contract ", title_ms: " ", description_en: "Line one\r\nLine two\n",
    description_ms: "Baris satu\nBaris dua", reference_note: "", due_offset_days: "-3", required: false,
    approval_by: "manager", auto_check: "document_uploaded:contract",
  }));
  assert.deepEqual(body, {
    title_en: "Sign the contract", title_ms: null, description_en: "Line one\nLine two",
    description_ms: "Baris satu\nBaris dua", reference_note: null, owner_role: "hr", owner_employee_id: null,
    anchor: "start_date", due_offset_days: -3, required: false, approval_by: "manager",
    auto_check: "document_uploaded:contract",
  });
});

test("only a named-employee step sends its person", () => {
  assert.equal(stepBody(step({ owner_role: "manager", owner_employee_id: PERSON })).owner_employee_id, null);
  assert.equal(stepBody(step({ owner_role: "person", owner_employee_id: PERSON })).owner_employee_id, PERSON);
});

test("createBody and updateBody carry the whole template, rules always present on update", () => {
  const draft = { ...emptyDraft(), name: " Warehouse onboarding ", steps: [step(), step({ title_en: "Collect the badge" })] };
  const create = createBody(draft);
  assert.deepEqual(Object.keys(create), ["kind", "name", "rules", "steps"]);
  assert.equal(create.name, "Warehouse onboarding");
  assert.equal(create.rules, null);
  assert.deepEqual(create.steps.map((s) => s.title_en), ["Sign the contract", "Collect the badge"]);
  const update = updateBody(draft, 4);
  assert.deepEqual(Object.keys(update), ["expected_version", "name", "rules", "steps"]);
  assert.equal(update.expected_version, 4);
  assert.equal(update.rules, null);
  assert.equal(updateBody(draft, 4, true).is_active, true);
});

test("stepProblems checks the backend limits with sentences", () => {
  assert.deepEqual(stepProblems(step(), "onboarding"), {});
  const long = (n) => "x".repeat(n);
  const problems = stepProblems(step({
    title_en: " ", title_ms: long(161), description_en: long(2001), description_ms: long(2001),
    reference_note: long(501), due_offset_days: "366",
  }), "onboarding");
  assert.equal(problems.title_en, "Each step needs an English title of at most 160 characters.");
  assert.equal(problems.title_ms, "A Malay title is at most 160 characters.");
  assert.equal(problems.description_en, "A description is at most 2000 characters.");
  assert.equal(problems.description_ms, "A description is at most 2000 characters.");
  assert.equal(problems.reference_note, "A reference note is at most 500 characters.");
  assert.equal(problems.due_offset_days, "Give a whole number of days from -365 to 365.");
  assert.equal(stepProblems(step({ due_offset_days: "1.5" }), "onboarding").due_offset_days,
    "Give a whole number of days from -365 to 365.");
  assert.equal(stepProblems(step({ due_offset_days: "" }), "onboarding").due_offset_days,
    "Give a whole number of days from -365 to 365.");
  assert.deepEqual(stepProblems(step({ due_offset_days: "-365", title_en: long(160) }), "onboarding"), {});
  // Characters, not UTF-16 units: an emoji counts once, as the server counts it.
  assert.deepEqual(stepProblems(step({ title_en: "😀".repeat(160) }), "onboarding"), {});
  assert.equal(textLength("😀a"), 2);
});

test("stepProblems refuses an anchor the kind does not allow and a named step without a person", () => {
  assert.equal(stepProblems(step({ anchor: "last_day" }), "onboarding").anchor,
    "Onboarding steps count from the start date or the probation end, offboarding steps from the last day.");
  assert.equal(stepProblems(step({ anchor: "start_date" }), "offboarding").anchor !== undefined, true);
  assert.deepEqual(stepProblems(step({ anchor: "last_day" }), "custom"), {});
  assert.equal(stepProblems(step({ owner_role: "person", owner_employee_id: "" }), "onboarding").owner_employee_id,
    "Choose an active person of this company for the step.");
});

test("draftProblems checks the name, the legal entity and the step count", () => {
  const ok = { ...emptyDraft(), name: "Warehouse onboarding", steps: [step()] };
  assert.equal(hasProblems(draftProblems(ok)), false);
  const bad = draftProblems({ ...ok, name: "", rules: { ...ok.rules, legal_entity_reference: "x".repeat(201) },
    steps: [step(), step({ title_en: "" })] });
  assert.equal(bad.name, "Give the template a name of at most 120 characters.");
  assert.equal(bad.legal_entity, "A legal entity is at most 200 characters.");
  assert.deepEqual(Object.keys(bad.steps[0]), []);
  assert.ok(bad.steps[1].title_en);
  assert.equal(hasProblems(bad), true);
  assert.equal(draftProblems({ ...ok, name: "x".repeat(121) }).name !== undefined, true);
  const many = draftProblems({ ...ok, steps: Array.from({ length: 41 }, () => step()) });
  assert.equal(many.count, "A template has at most 40 steps.");
  assert.equal(TEMPLATE_LIMITS.steps, 40);
});

test("rulesText says who a template applies to in words, never an id or a code", () => {
  const names = (id) => (id === DEPARTMENT ? "Warehouse" : null);
  assert.equal(rulesText({}, names), "Everyone");
  assert.equal(rulesText({ department_id: null, region: null }, names), "Everyone");
  assert.equal(rulesText({
    department_id: DEPARTMENT, employment_type: "full_time", legal_entity_reference: "Syarikat Cekap", region: "peninsular_labuan",
  }, names), "Department: Warehouse · Type: Full-time · Legal entity: Syarikat Cekap · Region: Peninsular Malaysia / Labuan");
  assert.equal(rulesText({ department_id: "11111111-1111-4111-8111-000000000009" }, names),
    "Department: one no longer listed");
  assert.equal(rulesText({ region: "sabah", employment_type: "intern" }, names), "Type: Intern · Region: Sabah");
});

test("templateTies mirrors the server's tie rule", () => {
  const t = (template_id, kind, rules, is_active = true) => ({ template_id, kind, rules, is_active, name: template_id });
  const ties = templateTies([
    t("a", "onboarding", { region: "sabah" }),
    t("b", "onboarding", { employment_type: "contract" }),
    t("c", "onboarding", { department_id: DEPARTMENT, employment_type: "full_time" }),
    t("d", "onboarding", { region: "sarawak" }),
    t("e", "offboarding", {}),
    t("f", "offboarding", { region: null }),
    t("g", "custom", {}),
    t("h", "custom", {}),
    t("i", "onboarding", { employment_type: "contract" }, false),
    t("j", "onboarding", { legal_entity_reference: "Kedai Mawar" }),
    t("k", "onboarding", { legal_entity_reference: "kedai mawar", region: "sabah" }),
    t("l", "onboarding", { legal_entity_reference: "KEDAI MAWAR", employment_type: "full_time" }),
  ]);
  assert.deepEqual(ties.get("a"), ["b", "j"]);
  assert.deepEqual(ties.get("b"), ["a", "d", "j"]);
  assert.deepEqual(ties.get("c"), ["k", "l"]);
  assert.deepEqual(ties.get("d"), ["b", "j"]);
  assert.deepEqual(ties.get("e"), ["f"]);
  assert.deepEqual(ties.get("g") ?? [], []);
  assert.deepEqual(ties.get("i") ?? [], []);
  assert.deepEqual(ties.get("k"), ["c", "l"]);
});

test("previewText counts people", () => {
  assert.equal(previewText({ matching: 6, ties: [], total_active: 9 }), "This applies to 6 people now.");
  assert.equal(previewText({ matching: 1, ties: [], total_active: 9 }), "This applies to 1 person now.");
  assert.equal(previewText({ matching: 0, ties: [], total_active: 9 }), "No one matches yet.");
});

test("warningText names the step", () => {
  const title = (ordinal) => (ordinal === 2 ? "Return the laptop" : null);
  assert.equal(warningText({ code: "step_after_last_day", ordinal: 2 }, title),
    "Step 2, Return the laptop, is due after the last day. It will be due on the last day instead.");
  assert.equal(warningText({ code: "something_new", ordinal: 3 }, title), "Check step 3 before you use this template.");
});

test("moveItem moves one row and keeps the others in order", () => {
  assert.deepEqual(moveItem(["a", "b", "c"], 2, 0), ["c", "a", "b"]);
  assert.deepEqual(moveItem(["a", "b", "c"], 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveItem(["a", "b", "c"], 0, -1), ["a", "b", "c"]);
  assert.deepEqual(moveItem(["a", "b", "c"], 2, 3), ["a", "b", "c"]);
});

test("draftFromTemplate, copyDraft and sameDraft", () => {
  const template = {
    template_id: "66666666-6666-4666-8666-000000000101", kind: "offboarding", name: "Standard offboarding", version: 3,
    is_active: true, created_at: "", updated_at: "", conflicts: [], warnings: [],
    rules: { department_id: DEPARTMENT, employment_type: null, legal_entity_reference: null, region: "sabah" },
    steps: [{
      ordinal: 1, title_en: "Return the laptop", title_ms: null, description_en: "Line one\nLine two", description_ms: null,
      owner_role: "person", owner_employee_id: PERSON, anchor: "last_day", due_offset_days: -2, required: true,
      approval_by: null, auto_check: "access_removed", reference_note: null,
    }],
  };
  const draft = draftFromTemplate(template);
  assert.equal(draft.kind, "offboarding");
  assert.deepEqual(draft.rules, { department_id: DEPARTMENT, employment_type: "", legal_entity_reference: "", region: "sabah" });
  assert.equal(draft.steps[0].due_offset_days, "-2");
  assert.equal(draft.steps[0].auto_check, "access_removed");
  assert.deepEqual(createBody(draft).steps[0], {
    title_en: "Return the laptop", title_ms: null, description_en: "Line one\nLine two", description_ms: null,
    reference_note: null, owner_role: "person", owner_employee_id: PERSON, anchor: "last_day", due_offset_days: -2,
    required: true, approval_by: null, auto_check: "access_removed",
  });
  const copy = copyDraft(draft);
  assert.equal(copy.name, "Copy of Standard offboarding");
  assert.notEqual(copy.steps[0].key, draft.steps[0].key);
  assert.equal(sameDraft(draft, draftFromTemplate(template)), true);
  assert.equal(sameDraft(draft, { ...draft, name: "Standard offboarding " }), true);
  assert.equal(sameDraft(draft, { ...draft, name: "Other" }), false);
  assert.equal(sameDraft(draft, { ...draft, steps: [...draft.steps, emptyStep("offboarding")] }), false);
});

test("the automatic checks offered exclude the system-only one", () => {
  const values = CHECK_CHOICES.map(([value]) => value);
  assert.deepEqual(values, ["", "account_linked", "personal_details_complete", "document_uploaded:contract",
    "document_uploaded:identity", "document_uploaded:certificate", "document_uploaded:other"]);
});

test("stepName names a row by its position and title", () => {
  assert.equal(stepName(2, "Collect the badge"), "Step 3: Collect the badge");
  assert.equal(stepName(0, "  "), "Step 1: Untitled step");
});

test("legalEntities lists the distinct legal entities of the people read", () => {
  const person = (legal_entity_reference) => ({
    employment_profile: legal_entity_reference === null ? null
      : { region: "sabah", legal_entity_reference, employment_category: "" },
  });
  assert.deepEqual(legalEntities([
    person("Syarikat Cekap Sdn Bhd"), person(null), person(" "), person("Kedai Mawar Enterprise"),
    person("syarikat cekap sdn bhd"), person(" Kedai Mawar Enterprise "),
  ]), ["Kedai Mawar Enterprise", "Syarikat Cekap Sdn Bhd"]);
});

test("a pasted tab becomes a space and CRLF or CR a line feed, as the server can store them", () => {
  assert.equal(cleanText("•\tItem one"), "• Item one");
  assert.equal(cleanText("a\r\nb\rc", true), "a\nb\nc");
  const body = stepBody(step({
    title_en: "Sign\tthe contract", description_en: "•\tItem one\r\n•\tItem two\rEnd", description_ms: "\tBaris",
    reference_note: "Folder\tA", title_ms: "Tandatangan\tkontrak",
  }));
  assert.equal(body.title_en, "Sign the contract");
  assert.equal(body.title_ms, "Tandatangan kontrak");
  assert.equal(body.description_en, "• Item one\n• Item two\nEnd");
  assert.equal(body.description_ms, "Baris");
  assert.equal(body.reference_note, "Folder A");
  assert.equal(createBody({ ...emptyDraft(), name: "Warehouse\tonboarding" }).name, "Warehouse onboarding");
  assert.deepEqual(stepProblems(step({ title_en: "A\tB", description_en: "x\r\ny\tz" }), "onboarding"), {});
});

test("any other control character is a field problem with a sentence", () => {
  const sentence = "This text has a character we can't save. Remove it and try again.";
  assert.equal(unsavable("a\u0000b"), true);
  assert.equal(unsavable("a\nb"), true);
  assert.equal(unsavable("a\nb", true), false);
  assert.equal(unsavable("bell\u0007", true), true);
  assert.equal(unsavable("del\u007f", true), true);
  const problems = stepProblems(step({
    title_en: "Sign\u0000", title_ms: "x\u0007", description_en: "ok\u0000", description_ms: "\u0007",
    reference_note: "\u001b[1m",
  }), "onboarding");
  assert.deepEqual(problems, {
    title_en: sentence, title_ms: sentence, description_en: sentence, description_ms: sentence, reference_note: sentence,
  });
  const draft = { ...emptyDraft(), name: "Bad\u0007name", rules: { ...emptyDraft().rules, legal_entity_reference: "X\u0000" },
    steps: [step()] };
  const found = draftProblems(draft);
  assert.equal(found.name, sentence);
  assert.equal(found.legal_entity, sentence);
});

test("a named owner who is not among the loaded people is a problem only when they could be loaded", () => {
  const PERSON2 = "55555555-5555-4555-8555-000000000009";
  const named = step({ owner_role: "person", owner_employee_id: PERSON2 });
  assert.deepEqual(stepProblems(named, "onboarding"), {});
  assert.equal(stepProblems(named, "onboarding", (id) => id === PERSON).owner_employee_id, FORMER_OWNER);
  assert.equal(FORMER_OWNER, "This person is no longer in your company list. Choose someone else.");
  assert.deepEqual(stepProblems({ ...named, owner_employee_id: PERSON }, "onboarding", (id) => id === PERSON), {});
  assert.equal(hasProblems(draftProblems({ ...emptyDraft(), name: "X", steps: [named] }, () => false)), true);
});
