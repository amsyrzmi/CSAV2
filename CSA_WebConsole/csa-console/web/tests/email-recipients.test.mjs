import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/email-recipients.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
} }).outputText;
const email = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const draft = (extra = {}) => ({
  id: "d1", thread_id: null, reply_to_message_id: null, recipient: "siti@company.test",
  to: ["siti@company.test"], cc: ["vendor@outside.test"], bcc: ["Boss@Company.test"],
  recipients: [
    { address: "siti@company.test", outside: false },
    { address: "vendor@outside.test", outside: true },
    { address: "Boss@Company.test", outside: false },
  ],
  subject: "Roster", body: "Hello", status: "draft", needs_staff_input: false, sent_at: null,
  ...extra,
});

test("recipients are grouped To, CC, BCC and outside addresses carry the flag", () => {
  const groups = email.recipientGroups(draft());
  assert.deepEqual(groups.map((g) => g.label), ["To", "CC", "BCC"]);
  assert.deepEqual(groups[0].people, [{ address: "siti@company.test", outside: false }]);
  assert.deepEqual(groups[1].people, [{ address: "vendor@outside.test", outside: true }]);
  assert.deepEqual(groups[2].people, [{ address: "Boss@Company.test", outside: false }]);
  assert.equal(email.OUTSIDE_LABEL, "Outside your company");
  assert.equal(email.outsideCount(draft()), 1);
  // Empty groups are left out; an address missing from recipients[] is treated as outside.
  const lonely = email.recipientGroups(draft({ cc: [], bcc: [], to: ["new@x.test"], recipients: [] }));
  assert.deepEqual(lonely, [{ label: "To", people: [{ address: "new@x.test", outside: true }] }]);
});

test("a draft with no thread is a new email, never a reply", () => {
  assert.equal(email.isReply(draft()), false);
  assert.equal(email.isReply(draft({ thread_id: "t1" })), false, "no message to reply to");
  assert.equal(email.isReply(draft({ thread_id: "t1", reply_to_message_id: "<m@x>" })), true);
  assert.equal(email.isReply(draft({ thread_id: null, reply_to_message_id: "<m@x>" })), false);
});

test("address fields split on commas, semicolons and spaces", () => {
  assert.deepEqual(email.parseAddresses(" a@x.test, b@y.test;c@z.test\nd@w.test  "), ["a@x.test", "b@y.test", "c@z.test", "d@w.test"]);
  assert.deepEqual(email.parseAddresses("  "), []);
  assert.equal(email.formatAddresses(["a@x.test", "b@y.test"]), "a@x.test, b@y.test");
  assert.equal(email.addressesLookValid(["a@x.test"]), true);
  assert.equal(email.addressesLookValid([]), false, "at least one recipient");
  assert.equal(email.addressesLookValid(["not-an-address"]), false);
  assert.equal(email.addressesLookValid(Array.from({ length: 21 }, (_, i) => `p${i}@x.test`)), false, "at most 20");
});

test("a send card whose draft was edited since is stale; the browser never hashes the draft", () => {
  const card = { kind: "email_send", expected_version: "a".repeat(64) };
  assert.equal(email.sendCardStale(card, { version: "a".repeat(64) }), false);
  assert.equal(email.sendCardStale(card, { version: "b".repeat(64) }), true);
  assert.equal(email.sendCardStale({ ...card, expected_version: null }, { version: "b".repeat(64) }), false);
  // Only send cards compare here; the server re-checks an export card's version at confirm.
  assert.equal(email.sendCardStale({ ...card, kind: "email_export" }, { version: "b".repeat(64) }), false);
  assert.equal(email.draftVersion, undefined);
  assert.doesNotMatch(source, /crypto\.subtle|sha-?256/i);
});

test("send errors are plain sentences; unknown codes are left to the caller", () => {
  const sentence = (code, body) => email.emailErrorSentence({ code, body });
  assert.equal(sentence("send_limit_reached", { detail: "send_limit_reached", retry_after: 600 }),
    "You've sent 20 emails in the last hour. Try again in 10 minutes.");
  assert.equal(sentence("send_limit_reached", { retry_after: 61 }), "You've sent 20 emails in the last hour. Try again in 2 minutes.");
  assert.equal(sentence("send_limit_reached", { retry_after: 5 }), "You've sent 20 emails in the last hour. Try again in 1 minute.");
  assert.equal(sentence("send_limit_reached", {}), "You've sent 20 emails in the last hour. Try again in 1 minute.");
  assert.equal(sentence("draft_needs_staff_input"), "Fill in the [STAFF: …] parts before sending.");
  assert.equal(sentence("send_outcome_unknown"), "We couldn't confirm the email was sent. Check your Gmail Sent folder before trying again.");
  assert.equal(sentence("draft_changed"), "This email changed after you opened it. Review it and send again.");
  assert.equal(sentence("draft_already_sent"), "This email was already sent.");
  assert.equal(sentence("draft_already_sending"), "This email is already being sent.");
  assert.equal(sentence("send_refused"), "Gmail refused this email. Check the addresses and try again.");
  assert.equal(sentence("mailbox_reauthorization_required"), "Reconnect your Gmail on the Email assistant tab, then try again.");
  assert.equal(sentence("provider_rate_limited"), "Gmail is busy. Try again in a minute.");
  assert.equal(sentence("recipients_invalid"), "Check the email addresses.");
  assert.equal(sentence("draft_not_releasable"), "Wait a couple of minutes, then try again.");
  assert.equal(sentence("something_new"), null);
  assert.equal(email.emailErrorSentence(new Error("boom")), null);
  assert.equal(email.emailErrorSentence(null), null);
});

test("the attention panel hides for no access and explains a missing summary", () => {
  assert.equal(email.attentionProblem({ code: "permission_denied" }), "hide");
  assert.equal(email.attentionProblem({ code: "model_not_configured" }), "The summary isn't available right now.");
  assert.equal(email.attentionProblem({ code: "email_model_unavailable" }), "The summary isn't available right now.");
  assert.equal(email.attentionProblem({ code: "mailbox_not_connected" }), "Connect your Gmail on the Email assistant tab first.");
  assert.equal(email.attentionProblem({ code: "other" }), null);
  assert.equal(email.attentionNote({ items: [{}], skipped: 2, as_of: null }), "Some emails couldn't be summarised.");
  assert.equal(email.attentionNote({ items: [], skipped: 2, as_of: null }), null);
  assert.equal(email.attentionNote({ items: [{}], skipped: 0, as_of: null }), null);
  // With nothing to show, a skipped email means the panel cannot say "nothing".
  assert.equal(email.attentionEmpty({ items: [], skipped: 2, as_of: null }), "Some emails couldn't be summarised.");
  assert.equal(email.attentionEmpty({ items: [], skipped: 0, as_of: null }), "Nothing needs your attention right now.");
  assert.equal(email.attentionEmpty({ items: [], as_of: null }), "Nothing needs your attention right now.");
});

test("email cards offer no Send or Confirm for a draft that is not ready", () => {
  const send = { kind: "email_send", expected_version: "a".repeat(64) };
  const exportCard = { kind: "email_export", expected_version: "c".repeat(64) };
  const ready = { status: "draft", version: "a".repeat(64), needs_staff_input: false };
  assert.equal(email.emailCardBlock(send, ready), null);
  assert.equal(email.emailCardBlock(exportCard, ready), null);
  for (const [status, code] of [
    ["sent", "draft_already_sent"], ["sending", "draft_already_sending"], ["exported", "draft_already_exported"],
  ]) {
    assert.equal(email.emailCardBlock(send, { ...ready, status }), code);
    assert.equal(email.emailCardBlock(exportCard, { ...ready, status }), code, "export cards too");
  }
  const placeholders = { ...ready, needs_staff_input: true };
  assert.equal(email.emailCardBlock(send, placeholders), "card_needs_staff_input");
  assert.equal(email.emailErrorSentence({ code: "card_needs_staff_input" }),
    "Fill in the [STAFF: …] parts on the Email assistant tab, then send it there.");
  assert.equal(email.emailCardBlock(exportCard, placeholders), null, "saving to Gmail drafts keeps placeholders");
  assert.equal(email.emailCardBlock(send, { ...ready, version: "b".repeat(64) }), "draft_changed");
});

test("a send the owner released says so on the card", () => {
  assert.equal(email.emailErrorSentence({ code: "send_released" }),
    "You marked this email as not sent. Ask the assistant to send it again, or send it from the Email assistant tab.");
});

test("sync on open only when the last sync is older than five minutes", () => {
  const now = Date.parse("2026-10-29T10:00:00Z");
  assert.equal(email.needsSync(null, now), true);
  assert.equal(email.needsSync("2026-10-29T09:54:59Z", now), true);
  assert.equal(email.needsSync("2026-10-29T09:55:00Z", now), false);
  assert.equal(email.needsSync("2026-10-29T09:59:00Z", now), false);
});

test("times read as day, short month, year and 24-hour time; the card names its draft", () => {
  assert.equal(email.formatWhen("2026-10-27T01:15:00Z", "Asia/Kuala_Lumpur"), "27 Oct 2026, 09:15");
  assert.equal(email.formatWhen("2026-10-27T13:05:00Z", "Asia/Kuala_Lumpur"), "27 Oct 2026, 21:05");
  const card = { fields: [{ label: "Subject", value: "(shown on open)" }, { label: "Draft", value: " 9b2c " }] };
  assert.equal(email.draftIdFromCard(card), "9b2c");
  assert.equal(email.draftIdFromCard({ fields: [] }), null);
  // The opened draft replaces the placeholder fields; anything else stays on the card.
  assert.deepEqual(email.cardFieldsShown(card), []);
  assert.deepEqual(email.cardFieldsShown({ fields: [...card.fields, { label: "Not included", value: "Ali (no work email)" }] }),
    [{ label: "Not included", value: "Ali (no work email)" }]);
});

test("after a sync the open thread is read again, and cleared when the sync removed it", async () => {
  const reads = [];
  const fresh = await email.rereadThread("t1", async (id) => { reads.push(id); return { thread_id: id, body: "new reply" }; });
  assert.deepEqual(fresh, { thread_id: "t1", body: "new reply" });
  assert.deepEqual(reads, ["t1"]);
  // Nothing open: nothing is read.
  assert.equal(await email.rereadThread(null, async () => { throw new Error("not called"); }), null);
  // Gone after cleanup: the gateway answers 404 resource_unavailable, so the view clears.
  assert.equal(await email.rereadThread("t1", async () => { throw { status: 404, code: "resource_unavailable" }; }), null);
  // Any other failure is the caller's to show.
  await assert.rejects(email.rereadThread("t1", async () => { throw { status: 503, code: "provider_unreachable" }; }));
});

test("provider_unreachable, permission_denied and email_model_unavailable have sentences", () => {
  for (const code of ["provider_unreachable", "permission_denied", "email_model_unavailable"]) {
    const sentence = email.emailErrorSentence({ code });
    assert.ok(sentence && sentence !== code && /^[A-Z].*[.]$/.test(sentence), code);
  }
});

test("recipient labels and the outside label are Malay in a Malay card", () => {
  const groups = email.recipientGroups(draft(), "ms");
  assert.deepEqual(groups.map((g) => g.label), ["Kepada", "CC", "BCC"]);
  assert.deepEqual(groups[1].people, [{ address: "vendor@outside.test", outside: true }]);
  assert.equal(email.OUTSIDE_LABELS.ms, "Di luar syarikat anda");
  assert.equal(email.OUTSIDE_LABELS.en, email.OUTSIDE_LABEL);
  assert.deepEqual(email.recipientGroups(draft()).map((g) => g.label), ["To", "CC", "BCC"]);
  assert.equal(email.outsideCount(draft()), 1);
});

test("email sentences have a Malay side and send_limit_reached counts minutes in Malay", () => {
  assert.equal(email.emailErrorSentence({ code: "draft_already_sent" }, "ms"), "E-mel ini sudah dihantar.");
  assert.equal(email.emailErrorSentence({ code: "draft_already_sent" }), "This email was already sent.");
  assert.equal(email.emailErrorSentence({ code: "nope" }, "ms"), null);
  assert.equal(email.emailErrorSentence({ code: "send_limit_reached" }, "ms"), "Anda telah menghantar 20 e-mel dalam sejam yang lalu. Cuba lagi dalam 1 minit.");
  assert.equal(email.emailErrorSentence({ code: "send_limit_reached", body: { retry_after: 600 } }, "ms"),
    "Anda telah menghantar 20 e-mel dalam sejam yang lalu. Cuba lagi dalam 10 minit.");
  assert.equal(email.EMAIL_ERROR_CODES.length, new Set(email.EMAIL_ERROR_CODES).size);
  for (const code of email.EMAIL_ERROR_CODES) {
    assert.ok(email.emailErrorSentence({ code }, "ms"), code);
    assert.notEqual(email.emailErrorSentence({ code }, "ms"), email.emailErrorSentence({ code }, "en"), code);
  }
});

test("a Malay card is found and shown exactly like an English one", () => {
  const sentinels = [{ label: "Subject", value: "(shown on open)" }, { label: "Draft", value: "d-42" }];
  const english = { fields: [...sentinels, { label: "To", value: "1 person" }, { label: "CC", value: "1 person" }] };
  const malay = { fields: [...sentinels, { label: "Kepada", value: "1 orang" }, { label: "CC", value: "1 orang" }] };
  assert.equal(email.draftIdFromCard(malay), "d-42");
  assert.equal(email.draftIdFromCard(english), email.draftIdFromCard(malay));
  assert.deepEqual(email.cardFieldsShown(malay), malay.fields.slice(2));
  assert.deepEqual(email.cardFieldsShown(english), english.fields.slice(2));
  // A card's block is a language-free code, whatever language the card is in.
  assert.equal(email.emailCardBlock({ kind: "email_send", expected_version: "v1" }, { status: "sent", version: "v1", needs_staff_input: false }), "draft_already_sent");
});
