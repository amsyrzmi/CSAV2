import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(name) {
  const source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const { initials, memberStatus, invitationChip, signOutSentence, revokedMessage } = await import(
  await load("team-view.ts")
);

test("initials: first letters of the first two words of the name, upper case", () => {
  assert.equal(initials("Aiman Yusof", "aiman@example.test"), "AY");
  assert.equal(initials("siti aminah binti omar", "s@example.test"), "SA");
  assert.equal(initials("  Lina   Wong ", "l@example.test"), "LW");
  assert.equal(initials("Madonna", "m@example.test"), "M");
});

test("initials: a blank name falls back to the email, then to a question mark", () => {
  assert.equal(initials("", "omar.lee@example.test"), "O");
  assert.equal(initials("   ", "zara@example.test"), "Z");
  assert.equal(initials("", ""), "?");
  assert.equal(initials("", "   "), "?");
});

test("initials: takes whole characters, not half a surrogate pair", () => {
  assert.equal(initials("Émile Zola", "e@example.test"), "ÉZ");
  assert.equal(initials("𝒜da Lovelace", "a@example.test"), "𝒜L");
});

test("memberStatus: an enabled member is active and a disabled one is disabled", () => {
  assert.equal(memberStatus({ is_enabled: true }), "active");
  assert.equal(memberStatus({ is_enabled: false }), "disabled");
});

test("memberStatus: a person who has not accepted yet is invited, whatever the flag says", () => {
  assert.equal(memberStatus({ is_enabled: true, accepted: false }), "invited");
  assert.equal(memberStatus({ is_enabled: false, accepted: false }), "invited");
  assert.equal(memberStatus({ is_enabled: true, accepted: true }), "active");
});

test("invitationChip: one plain word and tone per invitation status", () => {
  assert.deepEqual(invitationChip("Pending"), { label: "Waiting", tone: "info" });
  assert.deepEqual(invitationChip("Accepted"), { label: "Joined", tone: "success" });
  assert.deepEqual(invitationChip("Expired"), { label: "Expired", tone: "attention" });
  assert.deepEqual(invitationChip("Revoked"), { label: "Cancelled", tone: "neutral" });
});

test("signOutSentence: says what the server does, no more", () => {
  const role = signOutSentence("Aiman Yusof", "role");
  assert.match(role, /^Aiman Yusof will be signed out of this company straight away/);
  assert.match(role, /sign in again/);
  const disable = signOutSentence("Aiman Yusof", "disable");
  assert.match(disable, /^Aiman Yusof will be signed out of this company straight away/);
  assert.match(disable, /until you enable access again/);
  // A name is never blank in the sentence.
  assert.match(signOutSentence("  ", "role", "a@example.test"), /^a@example\.test will be signed out/);
});

test("signOutSentence speaks to the person themselves when they change their own access", () => {
  const role = signOutSentence("Aiman Yusof", "role", "a@example.test", true);
  assert.match(role, /^You will be signed out of this company straight away and will need to sign in again\.$/);
  const disable = signOutSentence("Aiman Yusof", "disable", "a@example.test", true);
  assert.match(disable, /^You will be signed out of this company straight away/);
  // They cannot enable themselves again, so the sentence does not tell them to.
  assert.match(disable, /another administrator enables your access again/);
  assert.doesNotMatch(disable, /until you enable/);
  // Someone else still gets their own name.
  assert.match(signOutSentence("Aiman Yusof", "role", "a@example.test", false), /^Aiman Yusof will be/);
});

test("revokedMessage names the person and says where they were signed out", () => {
  assert.equal(
    revokedMessage("Aiman Yusof", "a@example.test"),
    "Aiman Yusof has been signed out everywhere and will need to sign in again.",
  );
  assert.match(revokedMessage("", "a@example.test"), /^a@example\.test has been signed out everywhere/);
});
