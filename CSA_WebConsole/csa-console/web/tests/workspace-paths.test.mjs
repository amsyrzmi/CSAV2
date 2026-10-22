import assert from "node:assert/strict";
import test from "node:test";
import {
  guidanceText,
  hrGuidancePath,
  hrGuidanceText,
  workspaceEndpoint,
} from "../lib/workspace-paths.mjs";

test("workspace endpoints require an explicit selected tenant for platform use", () => {
  assert.equal(
    workspaceEndpoint("tenant /one"),
    "/console/v1/workspace/conversations?client_id=tenant+%2Fone",
  );
  assert.equal(
    workspaceEndpoint(null),
    "/console/v1/workspace/conversations",
  );
});

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

test("workspace thread IDs remain path-encoded and tenant-scoped", () => {
  assert.equal(
    workspaceEndpoint("tenant-id", "thread/one", true),
    "/console/v1/workspace/conversations/thread%2Fone/turns?client_id=tenant-id",
  );
});

test("guidance is selected from the fixed application guide", () => {
  assert.match(guidanceText("business_order_help"), /local record/);
  assert.match(guidanceText("arbitrary instructions"), /No guidance/);
});
