/**
 * Display helpers for email drafts: recipients, card staleness, times, and the one map of
 * email error codes to sentences. No imports, so the node tests load it directly.
 */

export const OUTSIDE_LABEL = "Outside your company";
/** The Email tab syncs on open when the last sync is older than this. */
export const SYNC_AFTER_MS = 5 * 60 * 1000;
const MAX_RECIPIENTS = 20;

export interface RecipientFlag { address: string; outside: boolean }
export interface DraftContent {
  thread_id: string | null;
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
  body: string;
}
export interface DraftRecipients extends DraftContent {
  recipients: RecipientFlag[];
  reply_to_message_id: string | null;
}
export interface RecipientGroup { label: "To" | "CC" | "BCC"; people: RecipientFlag[] }

/** To, CC and BCC with the server's outside flag; an address the server did not mark is outside. */
export function recipientGroups(draft: DraftRecipients): RecipientGroup[] {
  const all = [...draft.to, ...draft.cc, ...draft.bcc];
  // recipients[] follows [...to, ...cc, ...bcc]; fall back to a lookup if it ever does not.
  const positional = draft.recipients.length === all.length &&
    draft.recipients.every((r, i) => r.address.toLowerCase() === all[i].toLowerCase());
  const byAddress = new Map(draft.recipients.map(r => [r.address.trim().toLowerCase(), r.outside]));
  let index = 0;
  const flag = (address: string): RecipientFlag => {
    const outside = positional ? draft.recipients[index].outside : byAddress.get(address.trim().toLowerCase()) ?? true;
    index += 1;
    return { address, outside };
  };
  const groups: RecipientGroup[] = [
    { label: "To", people: draft.to.map(flag) },
    { label: "CC", people: draft.cc.map(flag) },
    { label: "BCC", people: draft.bcc.map(flag) },
  ];
  return groups.filter(group => group.people.length > 0);
}

export function outsideCount(draft: DraftRecipients): number {
  return new Set(recipientGroups(draft).flatMap(g => g.people).filter(p => p.outside)
    .map(p => p.address.toLowerCase())).size;
}

/** Same rule as the server's card title: a reply needs a thread and a message to answer. */
export function isReply(draft: { thread_id: string | null; reply_to_message_id: string | null }): boolean {
  return draft.thread_id !== null && Boolean(draft.reply_to_message_id);
}

export function parseAddresses(text: string): string[] {
  return text.split(/[\s,;]+/).map(part => part.trim()).filter(Boolean);
}
export function formatAddresses(addresses: string[]): string {
  return addresses.join(", ");
}
/** A quick shape check before saving; the server applies the full recipient rules. */
export function addressesLookValid(addresses: string[]): boolean {
  return addresses.length >= 1 && addresses.length <= MAX_RECIPIENTS &&
    addresses.every(address => /^[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+$/.test(address));
}

export function needsSync(lastSyncedAt: string | null, now: number): boolean {
  return lastSyncedAt === null || now - Date.parse(lastSyncedAt) > SYNC_AFTER_MS;
}

/** "27 Oct 2026, 09:15" (docs/WRITING_GUIDE.md). */
export function formatWhen(iso: string, timeZone?: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    hourCycle: "h23", ...(timeZone ? { timeZone } : {}),
  }).formatToParts(new Date(iso));
  const part = (type: string) => parts.find(p => p.type === type)?.value ?? "";
  return `${part("day")} ${part("month")} ${part("year")}, ${part("hour")}:${part("minute")}`;
}

/** Cards store counts only; the draft id is in the `Draft` field, the content is fetched on open. */
export function draftIdFromCard(card: { fields: { label: string; value: string }[] }): string | null {
  const value = card.fields.find(field => field.label === "Draft")?.value.trim();
  return value ? value : null;
}
/** The opened draft replaces the `Subject` and `Draft` placeholders; other fields stay. */
export function cardFieldsShown(card: { fields: { label: string; value: string }[] }) {
  return card.fields.filter(field => field.label !== "Subject" && field.label !== "Draft");
}

/**
 * A send card is bound to the draft's version when it was made. If the draft read now has a
 * different version it was edited since: say so and do not send (the server re-checks anyway).
 */
export function sendCardStale(
  card: { kind: string; expected_version: string | null },
  draft: { version: string },
): boolean {
  return card.kind === "email_send" && card.expected_version !== null && card.expected_version !== draft.version;
}

const sentences: Record<string, string> = {
  draft_needs_staff_input: "Fill in the [STAFF: …] parts before sending.",
  send_outcome_unknown: "We couldn't confirm the email was sent. Check your Gmail Sent folder before trying again.",
  draft_changed: "This email changed after you opened it. Review it and send again.",
  draft_already_sent: "This email was already sent.",
  draft_already_sending: "This email is already being sent.",
  send_refused: "Gmail refused this email. Check the addresses and try again.",
  mailbox_reauthorization_required: "Reconnect your Gmail on the Email assistant tab, then try again.",
  provider_rate_limited: "Gmail is busy. Try again in a minute.",
  recipients_invalid: "Check the email addresses.",
  recipient_unresolved: "We have no work email for this colleague. Add one to their profile, then try again.",
  draft_not_releasable: "Wait a couple of minutes, then try again.",
  // Email tab codes from before sending existed.
  oauth_state_invalid: "The sign-in link expired or was already used. Start the connection again.",
  provider_denied: "Google reported that access was not granted.",
  scopes_not_granted: "Google did not grant read and compose access. Tick both permissions and try again.",
  assistant_disabled: "The email assistant is switched off for this tenant.",
  mailbox_not_connected: "Connect your Gmail on the Email assistant tab first.",
  model_not_configured: "No model is configured for analysis.",
  provider_not_configured: "Google OAuth is not configured on this deployment.",
  draft_rejected: "The draft was rejected: check the recipient and remove line breaks from the subject.",
  draft_already_exported: "This draft was already saved to Gmail and can no longer be edited here.",
  thread_body_unavailable: "This thread has no readable text to analyse.",
  idempotency_conflict: "A different request already used this key. Reload and try again.",
};

function codeOf(error: unknown): string | null {
  return typeof error === "object" && error !== null && "code" in error && typeof error.code === "string"
    ? error.code : null;
}

/** The sentence for an email refusal (an `ApiError`, or anything with `code`), or null if unmapped. */
export function emailErrorSentence(error: unknown): string | null {
  const code = codeOf(error);
  if (code === null) return null;
  if (code === "send_limit_reached") {
    const body = (error as { body?: { retry_after?: unknown } }).body;
    const seconds = typeof body?.retry_after === "number" ? body.retry_after : 60;
    const minutes = Math.max(1, Math.ceil(seconds / 60));
    return `You've sent 20 emails in the last hour. Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.`;
  }
  return sentences[code] ?? null;
}

/** "hide" for no access, a sentence for a summary that cannot run, or null for the generic error. */
export function attentionProblem(error: unknown): "hide" | string | null {
  const code = codeOf(error);
  if (code === "permission_denied") return "hide";
  if (code === "model_not_configured" || code === "email_model_unavailable") return "The summary isn't available right now.";
  return code === "mailbox_not_connected" ? sentences.mailbox_not_connected : null;
}
export function attentionNote(page: { items: unknown[]; skipped?: number }): string | null {
  return (page.skipped ?? 0) > 0 && page.items.length > 0 ? "Some emails couldn't be summarised." : null;
}
