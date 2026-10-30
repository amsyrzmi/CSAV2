import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/overview-strip.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { stripParts, canSeeInboxCount, activeServiceCount } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

const REVIEW = "Replies needing review";
const SERVICES = "Services active";
const pkg = (key, status) => ({ key, status });

test("the strip lists review then services, and omits a part without a value", () => {
  assert.deepEqual(stripParts({ review: 3, services: 2 }), [
    { label: REVIEW, value: 3 },
    { label: SERVICES, value: 2 },
  ]);
  assert.deepEqual(stripParts({ review: null, services: 4 }), [{ label: SERVICES, value: 4 }]);
  assert.deepEqual(stripParts({ review: 1, services: null }), [{ label: REVIEW, value: 1 }]);
  assert.deepEqual(stripParts({ review: null, services: null }), [], "no parts means no strip");
});

test("a real zero shows, but a missing value never becomes zero", () => {
  assert.deepEqual(stripParts({ review: 0, services: 0 }), [
    { label: REVIEW, value: 0 },
    { label: SERVICES, value: 0 },
  ]);
  for (const part of stripParts({ review: null, services: null })) assert.notEqual(part.value, 0);
  assert.equal(stripParts({ review: null, services: 5 }).some((p) => p.label === REVIEW), false);
});

test("negative, NaN and non-finite inputs are treated as missing", () => {
  assert.deepEqual(stripParts({ review: -1, services: Number.NaN }), []);
  assert.deepEqual(stripParts({ review: Number.POSITIVE_INFINITY, services: 2 }), [
    { label: SERVICES, value: 2 },
  ]);
  assert.deepEqual(stripParts({ review: undefined, services: "3" }), []);
});

test("the review count needs a role that can see the Inbox and Customer Support active or suspended", () => {
  const active = [pkg("customer_support", "active")];
  assert.equal(canSeeInboxCount("read_only", active), false);
  assert.equal(canSeeInboxCount(null, active), false);
  assert.equal(canSeeInboxCount("tenant_staff", active), true);
  assert.equal(canSeeInboxCount("tenant_admin", active), true);
  assert.equal(canSeeInboxCount("tenant_staff", [pkg("customer_support", "suspended")]), true);
  assert.equal(canSeeInboxCount("tenant_staff", [pkg("customer_support", "available")]), false);
  assert.equal(canSeeInboxCount("tenant_staff", [pkg("customer_support", "requested")]), false);
  assert.equal(canSeeInboxCount("tenant_staff", [pkg("hr", "active")]), false);
  assert.equal(canSeeInboxCount("tenant_staff", []), false);
  assert.equal(canSeeInboxCount("tenant_staff", null), false, "unknown packages: no read");
  assert.equal(canSeeInboxCount("platform_admin", active), true);
  assert.equal(canSeeInboxCount("platform_admin", null), false);
});

test("active services count only active packages; unknown stays unknown", () => {
  assert.equal(activeServiceCount(null), null);
  assert.equal(activeServiceCount([]), 0);
  assert.equal(
    activeServiceCount([
      pkg("customer_support", "active"),
      pkg("hr", "suspended"),
      pkg("staff_email", "requested"),
      pkg("orders_products", "available"),
      pkg("subscriptions", "active"),
    ]),
    2,
  );
});
