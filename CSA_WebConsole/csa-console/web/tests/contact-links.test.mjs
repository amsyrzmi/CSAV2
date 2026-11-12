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
const subsSource = await readFile(new URL("../lib/subscriptions-api.ts", import.meta.url), "utf8");
const subsUrl = moduleUrl(subsSource.replace('"./api"', JSON.stringify(apiUrl)));
const source = await readFile(new URL("../lib/contact-links.ts", import.meta.url), "utf8");
const links = await import(moduleUrl(source.replace('"@/lib/subscriptions-api"', JSON.stringify(subsUrl))));

const NOW = new Date("2026-11-12T02:00:00Z");
const DAY = 86_400_000;
const ago = (days) => new Date(NOW.getTime() - days * DAY).toISOString();
const person = (id, name, channels = []) => ({ id, full_name: name, phone: null, channels });
const AISYAH = person("a1", "Aisyah Rahman", ["telegram"]);
const BEN = person("b2", "Ben Tan", ["whatsapp"]);
const link = (changes = {}) => ({
  id: "l1", survivor: AISYAH, loser: BEN, reason: "staff", created_at: ago(2), unlinked_at: null,
  can_unlink: true, ...changes,
});

test("channels read as plain names, and an unknown code keeps its words", () => {
  assert.equal(links.channelsText(["telegram"]), "Telegram");
  assert.equal(links.channelsText(["telegram", "whatsapp"]), "Telegram and WhatsApp");
  assert.equal(links.channelsText(["email", "telegram", "whatsapp"]), "Email, Telegram and WhatsApp");
  assert.equal(links.channelsText(["tiktok_shop"]), "TikTok Shop");
  assert.equal(links.channelsText(["line_chat"]), "Line chat");
  assert.equal(links.channelsText([]), "No chat channel yet");
});

test("a contact is named by name, else phone, else plainly", () => {
  assert.equal(links.contactLabel(AISYAH), "Aisyah Rahman");
  assert.equal(links.contactLabel({ ...AISYAH, full_name: null, phone: "+60123456789" }), "+60123456789");
  assert.equal(links.contactLabel({ ...AISYAH, full_name: "  ", phone: null }), "Unnamed customer");
});

test("the link body names both contacts", () => {
  assert.deepEqual(links.linkBody("a1", "b2"), { contact_a: "a1", contact_b: "b2" });
});

test("how a link was made reads in a few words", () => {
  assert.equal(links.linkReasonText("verified_phone"), "Matched by a verified phone number");
  assert.equal(links.linkReasonText("staff"), "Linked by staff");
});

test("a link that cannot be undone says why, in the right words", () => {
  // Undoable: no note at all (the Unlink button shows instead).
  assert.equal(links.unlinkNote(link(), NOW), null);
  assert.equal(
    links.unlinkNote(link({ can_unlink: false, created_at: ago(31) }), NOW),
    "This link is older than 30 days and can no longer be undone here.",
  );
  assert.equal(
    links.unlinkNote(link({ can_unlink: false, created_at: ago(30.5) }), NOW),
    "This link is older than 30 days and can no longer be undone here.",
  );
  // Already undone: not the 30-day sentence, even when it is old.
  assert.equal(
    links.unlinkNote(link({ can_unlink: false, created_at: ago(45), unlinked_at: "2026-10-30T02:00:00Z" }), NOW, "Asia/Kuala_Lumpur"),
    "Undone on 30 Oct 2026.",
  );
  // Recent and not undone, yet not undoable: a newer link has to go first.
  assert.equal(
    links.unlinkNote(link({ can_unlink: false, created_at: ago(5) }), NOW),
    "A newer link on one of these contacts has to be undone first.",
  );
});

test("after an unlink, what could not be put back is named plainly", () => {
  const pair = link();
  assert.equal(links.skippedSentence([], pair), null);
  assert.equal(
    links.skippedSentence(["loser.telegram_id"], pair),
    "Unlinked. Not everything could be put back, because it changed after the link or another contact now has it: Ben Tan's Telegram account.",
  );
  assert.equal(
    links.skippedSentence(["survivor.phone", "survivor.phone_verified_at", "loser.telegram_id"], pair),
    "Unlinked. Not everything could be put back, because it changed after the link or another contact now has it: Aisyah Rahman's phone number and phone verification, Ben Tan's Telegram account.",
  );
  // A column this page does not know keeps its words, so nothing is hidden.
  assert.equal(
    links.skippedSentence(["loser.nick_name"], pair),
    "Unlinked. Not everything could be put back, because it changed after the link or another contact now has it: Ben Tan's nick name.",
  );
});

test("each review reason has its own plain sentence", () => {
  const reasons = ["several_matches", "telegram_conflict", "same_channel_identity", "table_conflict", "previously_unlinked"];
  const said = reasons.map((reason) => links.reviewReasonSentence(reason));
  assert.equal(new Set(said).size, reasons.length);
  for (const sentence of said) {
    assert.doesNotMatch(sentence, /_/);
    assert.match(sentence, /\.$/);
  }
  assert.equal(links.reviewReasonSentence("previously_unlinked"),
    "Staff unlinked these two contacts before, so they were not linked again automatically.");
  // A reason from a newer server still reads, and does not show a code.
  assert.equal(links.reviewReasonSentence("something_new"), "These two contacts could not be linked automatically.");
});

test("a chosen contact cannot be linked to itself or to one already linked", () => {
  const rows = [link()];
  assert.equal(links.linkChoiceProblem("b2", { id: "b2" }, rows), "Choose a different contact. This is the same customer.");
  assert.equal(links.linkChoiceProblem("b2", { id: "a1" }, rows), "These two are already linked.");
  assert.equal(links.linkChoiceProblem("b2", { id: "c3" }, rows), null);
  // An undone link does not count.
  assert.equal(links.linkChoiceProblem("b2", { id: "a1" }, [link({ unlinked_at: ago(1) })]), null);
});
