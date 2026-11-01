import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(name, replacements = {}) {
  let source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(replacements)) source = source.replaceAll(from, to);
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const emailUrl = await load("email-recipients.ts");
const copy = await import(await load("assistant-copy.ts", {
  '"@/lib/email-recipients"': JSON.stringify(emailUrl),
}));
const email = await import(emailUrl);

const FALLBACK = "Sesuatu tidak kena. Cuba lagi.";
// What ApiError carries: `message` is the explained sentence, `code` the server's detail.
const apiError = (code, status = 409, message = code) => Object.assign(new Error(message), { status, code });

test("English and Malay panel text have the same keys and no Malay value is empty", () => {
  assert.deepEqual(Object.keys(copy.PANEL.ms).sort(), Object.keys(copy.PANEL.en).sort());
  for (const [key, value] of Object.entries(copy.PANEL.ms)) assert.ok(value.trim(), key);
  for (const [key, value] of Object.entries(copy.PANEL.en)) assert.ok(value.trim(), key);
});

test("the language of a turn or card defaults to English", () => {
  assert.equal(copy.languageOf({ language: "ms" }), "ms");
  assert.equal(copy.languageOf({ language: "en" }), "en");
  assert.equal(copy.languageOf({}), "en");
  assert.equal(copy.languageOf(null), "en");
  assert.equal(copy.languageOf(undefined), "en");
  assert.equal(copy.languageOf({ language: "fr" }), "en");
});

test("panel text fills its placeholders", () => {
  assert.equal(copy.panelText("en", "on_tab", { tab: "People & HR" }), "On People & HR");
  assert.equal(copy.panelText("ms", "on_tab", { tab: "People & HR" }), "Pada tab People & HR");
  assert.equal(copy.panelText("en", "choose_colleague"), "Choose a colleague:");
});

test("the Malay buttons and labels match the server's Malay card text", () => {
  assert.equal(copy.PANEL.ms.button_send, "Hantar");
  assert.equal(copy.PANEL.ms.button_sending, "Menghantar…");
  assert.equal(copy.PANEL.ms.no_colleague, "Tiada rakan sekerja yang sepadan ditemui.");
  assert.equal(copy.PANEL.ms.email_to, "Kepada");
  assert.equal(copy.PANEL.ms.email_cc, "CC");
  assert.equal(copy.PANEL.ms.email_bcc, "BCC");
  assert.equal(copy.PANEL.en.button_send, "Send");
  assert.equal(copy.PANEL.en.button_confirm, "Confirm");
  assert.equal(copy.PANEL.en.button_confirming, "Confirming…");
  assert.equal(copy.PANEL.en.button_cancelling, "Cancelling…");
});

const LISTED = [
  "package_inactive", "proposal_stale", "proposal_consumed", "proposal_expired",
  "proposal_session_mismatch", "agent_disabled", "planner_unavailable", "mailbox_not_connected",
  "planner_invalid_output", "email_model_unavailable", "hr_model_unavailable",
  "email_timeout_check_saved_state", "hr_agent_timeout_check_saved_state", "training_not_draft",
  "stale_version", "invalid_transition", "csrf_required", "no_employee_link",
  "hr_training_not_permitted", "reply_target_unavailable", "email_recipient_invalid",
  "recipients_invalid", "draft_rejected", "thread_body_unavailable", "resource_unavailable",
  "reply_approval_not_found",
  // Subscription card refusals at Confirm: retrying cannot help, so they must not fall back.
  "action_not_allowed", "resume_at_not_after_today", "plan_unavailable", "plan_unchanged",
  "price_override_requires_immediate_change", "renews_at_not_after_current", "request_decided",
  "request_subscription_mismatch", "request_action_mismatch", "subscription_not_found",
];
// One home per sentence: these three live in the email table, so MS_ERRORS must not copy them.
const EMAIL_OWNED = ["mailbox_not_connected", "email_model_unavailable", "recipients_invalid"];
// What a listed code reads as in Malay: its MS_ERRORS line, else the email table's.
const malayLine = (code) => copy.MS_ERRORS[code] ?? email.emailErrorSentence({ code }, "ms");

test("every listed code gets a Malay line through errorLine, from one home", () => {
  for (const code of LISTED) {
    const line = copy.errorLine(apiError(code, 409, "English sentence"), "ms");
    assert.ok(line.trim(), code);
    assert.notEqual(line, FALLBACK, code);
    assert.equal(line, malayLine(code), code);
  }
  for (const code of EMAIL_OWNED) {
    assert.equal(copy.MS_ERRORS[code], undefined, `${code} is duplicated in MS_ERRORS`);
    assert.equal(copy.errorLine(apiError(code), "ms"), email.emailErrorSentence({ code }, "ms"), code);
  }
  // draft_rejected and thread_body_unavailable are different on purpose.
  for (const code of ["draft_rejected", "thread_body_unavailable"])
    assert.notEqual(copy.MS_ERRORS[code], email.emailErrorSentence({ code }, "ms"), code);
});

test("a known code in Malay gives its Malay line, whatever the English message", () => {
  const line = copy.errorLine(apiError("planner_unavailable", 503, "The assistant is unavailable right now. Try again in a moment."), "ms");
  assert.equal(line, copy.MS_ERRORS.planner_unavailable);
  assert.doesNotMatch(line, /unavailable/);
  for (const code of LISTED) {
    const text = copy.errorLine(apiError(code, 409, "English sentence"), "ms");
    assert.equal(text, malayLine(code), code);
    assert.notEqual(text, FALLBACK, code);
  }
});

test("every email sentence has a Malay line through errorLine", () => {
  assert.ok(email.EMAIL_ERROR_CODES.length >= 20);
  for (const code of email.EMAIL_ERROR_CODES) {
    const ms = copy.errorLine(apiError(code), "ms");
    assert.ok(ms.trim(), code);
    assert.notEqual(ms, code, code);
    assert.notEqual(ms, FALLBACK, code);
    assert.notEqual(ms, copy.errorLine(apiError(code), "en"), `${code} is still English`);
  }
  const limited = Object.assign(apiError("send_limit_reached", 429), { body: { retry_after: 125 } });
  assert.equal(copy.errorLine(limited, "ms"), "Anda telah menghantar 20 e-mel dalam sejam yang lalu. Cuba lagi dalam 3 minit.");
});

test("an unmapped code, a bare network error or a non-Error gives the Malay fallback", () => {
  assert.equal(copy.errorLine(apiError("some_new_code"), "ms"), FALLBACK);
  assert.equal(copy.errorLine(apiError("Request failed (404)", 404), "ms"), FALLBACK);
  assert.equal(copy.errorLine(new TypeError("Failed to fetch"), "ms"), FALLBACK);
  assert.equal(copy.errorLine("boom", "ms"), FALLBACK);
  assert.equal(copy.errorLine(null, "ms"), FALLBACK);
});

test("a server error with no mapped code gives the Malay 5xx line", () => {
  const line = copy.errorLine(apiError("Request failed (502)", 502, "Something went wrong on our side. Try again."), "ms");
  assert.notEqual(line, FALLBACK);
  assert.equal(line, copy.PANEL.ms.error_server);
  assert.doesNotMatch(line, /Something/);
  // A mapped code still wins over the status.
  assert.equal(copy.errorLine(apiError("planner_unavailable", 503), "ms"), copy.MS_ERRORS.planner_unavailable);
});

test("English error lines are exactly what the panel showed before", () => {
  assert.equal(copy.errorLine(apiError("planner_unavailable", 503, "The assistant is unavailable right now. Try again in a moment."), "en"),
    "The assistant is unavailable right now. Try again in a moment.");
  assert.equal(copy.errorLine(apiError("provider_unreachable", 502), "en"), "We couldn't reach Gmail. Wait a minute and try again.");
  assert.equal(copy.errorLine(apiError("some_new_code"), "en"), "some_new_code");
  assert.equal(copy.errorLine(apiError("Request failed (502)", 502, "Something went wrong on our side. Try again."), "en"),
    "Something went wrong on our side. Try again.");
  assert.equal(copy.errorLine("boom", "en"), "The assistant request failed.");
  const explained = apiError("mailbox_not_connected", 409, "Connect your Gmail in the Email tab first.");
  assert.equal(copy.errorLine(explained, "en"), "Connect your Gmail in the Email tab first.");
});

test("the dock chrome has English as it was and a Malay line", () => {
  const english = {
    dock_title: "Assistant", new_conversation: "New conversation", close_assistant: "Close assistant",
    message_label: "Message", retry_same: "Retry same request", start_new: "Start a new request",
    order_source: "Source", order_tracking: "Tracking", order_carrier: "Carrier", order_not_recorded: "Not recorded",
    empty_hint: "Ask about this tab, find a colleague, or prepare an action. Nothing changes until you confirm it.",
  };
  for (const [key, text] of Object.entries(english)) {
    assert.equal(copy.PANEL.en[key], text, key);
    assert.ok(copy.PANEL.ms[key]?.trim(), key);
    assert.notEqual(copy.PANEL.ms[key], text, key);
  }
});
