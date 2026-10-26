import assert from "node:assert/strict";
import test from "node:test";
import * as paths from "../lib/workspace-paths.mjs";

const { hrGuidancePath, hrGuidanceText } = paths;

test("HR navigation uses fixed same-origin sections and retains platform tenant", () => {
  assert.equal(hrGuidancePath("directory.read", "tenant /one", true),
    "/hr?section=directory&client_id=tenant+%2Fone");
  assert.equal(hrGuidancePath("training.read", "tenant-id", false),
    "/portal/hr?section=training");
  for (const action of ["training.draft", "training.submit", "training.withdraw", "training.decide"]) {
    assert.equal(hrGuidancePath(action, "tenant-id", true),
      "/hr?section=training&client_id=tenant-id");
    assert.ok(hrGuidanceText(action).length > 20);
  }
  assert.match(hrGuidanceText("training.decide"), /explicitly confirm/);
  for (const invalid of ["payroll.read", "__proto__", "https://external.test", "constructor"]) {
    assert.throws(() => hrGuidancePath(invalid, "tenant", false), /Unknown HR/);
    assert.throws(() => hrGuidanceText(invalid), /Unknown HR/);
  }
});

test("the module keeps only the HR guidance helpers", () => {
  assert.deepEqual(Object.keys(paths).sort(), ["hrGuidancePath", "hrGuidanceText"]);
});
