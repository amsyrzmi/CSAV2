import assert from "node:assert/strict";
import test from "node:test";
import {
  guidanceText,
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
