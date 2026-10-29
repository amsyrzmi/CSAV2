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
const copyUrl = moduleUrl(await readFile(new URL("../lib/hr-access-copy.ts", import.meta.url), "utf8"));
const source = await readFile(new URL("../lib/invite-access.ts", import.meta.url), "utf8");
const invite = await import(moduleUrl(source.replace('"./hr-access-copy"', JSON.stringify(copyUrl))));

const values = (options) => options.map((option) => option.value);

test("hrAccessOptions hides presets without the hr package", () => {
  assert.deepEqual(invite.hrAccessOptions({ hasHrPackage: false, isHrOwner: false, role: "tenant_staff" }), []);
  assert.deepEqual(invite.hrAccessOptions({ hasHrPackage: false, isHrOwner: true, role: "tenant_admin" }), []);
});

test("an admin who is not the HR owner may choose only none or Staff", () => {
  for (const role of ["tenant_admin", "tenant_staff", "read_only"])
    assert.deepEqual(values(invite.hrAccessOptions({ hasHrPackage: true, isHrOwner: false, role })), ["none", "staff"]);
});

test("the HR owner may choose every preset", () => {
  assert.deepEqual(values(invite.hrAccessOptions({ hasHrPackage: true, isHrOwner: true, role: "tenant_staff" })),
    ["none", "staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"]);
});

test("options carry the agreed labels and a plain explanation", () => {
  const options = invite.hrAccessOptions({ hasHrPackage: true, isHrOwner: true, role: "tenant_staff" });
  assert.deepEqual(options.map((option) => option.label),
    ["No HR access", "Staff", "Manager", "HR team", "Payroll preparer", "Payroll reviewer"]);
  for (const option of options) {
    assert.ok(option.explanation.length > 10);
    assert.doesNotMatch(option.explanation, /_|null|undefined/);
  }
  assert.equal(options[2].explanation, "Staff, plus approves for their team.");
});

test("the default is Staff, and none for a read-only invitee", () => {
  assert.equal(invite.defaultHrAccess("read_only"), "none");
  assert.equal(invite.defaultHrAccess("tenant_staff"), "staff");
  assert.equal(invite.defaultHrAccess("tenant_admin"), "staff");
});

test("deliveryMessage says the invitation was emailed, or how to share the link", () => {
  assert.equal(invite.deliveryMessage({ token: null, delivery: "email" }, "aina@example.my"),
    "Invitation emailed to aina@example.my");
  assert.equal(invite.deliveryMessage({ token: "synthetic", delivery: "manual" }, "aina@example.my"),
    "We couldn't send the email. Copy this link and send it yourself.");
  assert.equal(invite.deliveryMessage({ token: null, delivery: "email" }, "aina@example.my", "reset"),
    "Reset link emailed to aina@example.my");
});

test("a link is built only when the server returned a token", () => {
  assert.equal(invite.inviteLink({ token: null, delivery: "email" }, "https://console.example"), "");
  assert.equal(invite.inviteLink({ token: "a b/1", delivery: "manual" }, "https://console.example"),
    "https://console.example/invite#token=a%20b%2F1");
});
