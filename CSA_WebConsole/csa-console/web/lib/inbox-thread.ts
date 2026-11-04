/**
 * Pure helpers for the inbox thread, list rows and review cards. No imports, so
 * node --test can load this file directly. Dates are local time, written the way the
 * writing guide asks: "27 Oct 2026, 09:15".
 */

export type Sender = "customer" | "assistant" | "staff";

/** Messages name only the direction and `author = 'human_support'` for staff (spec 9). */
export function senderOf(message: { direction: string; author: string }): Sender {
  if (message.direction === "inbound") return "customer";
  return message.author === "human_support" ? "staff" : "assistant";
}

export function authorLabel(sender: Sender): "Customer" | "Staff" | "Assistant" {
  return sender === "customer" ? "Customer" : sender === "staff" ? "Staff" : "Assistant";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parse(at: string): Date | null {
  const time = new Date(at);
  return Number.isNaN(time.getTime()) ? null : time;
}

const two = (value: number) => String(value).padStart(2, "0");

/** "27 Oct 2026", or "" for an unreadable time. */
export function dayLabel(at: string): string {
  const time = parse(at);
  return time ? `${time.getDate()} ${MONTHS[time.getMonth()]} ${time.getFullYear()}` : "";
}

/** "09:15" on the 24-hour clock, or "". */
export function clockTime(at: string): string {
  const time = parse(at);
  return time ? `${two(time.getHours())}:${two(time.getMinutes())}` : "";
}

/** "27 Oct 2026, 09:15", or "". */
export function dateTime(at: string): string {
  const time = parse(at);
  return time ? `${dayLabel(at)}, ${clockTime(at)}` : "";
}

/** Same sender within this window: one group (tighter spacing, one avatar). */
export const GROUP_WINDOW_MS = 5 * 60_000;

type ThreadMessage = { id: string; direction: string; author: string; created_at: string };

export type ThreadItem<M extends ThreadMessage> =
  | { kind: "day"; key: string; label: string }
  | { kind: "message"; key: string; message: M; sender: Sender; first: boolean; last: boolean };

/**
 * Oldest-first messages to render items: a divider whenever the local day changes, and
 * each message marked as the first and/or last of its group.
 */
export function threadItems<M extends ThreadMessage>(messages: M[]): ThreadItem<M>[] {
  type Previous = { sender: Sender; time: Date | null; day: string };
  const items: ThreadItem<M>[] = [];
  let previous = null as Previous | null;
  let lastMessage = null as Extract<ThreadItem<M>, { kind: "message" }> | null;
  for (const message of messages) {
    const sender = senderOf(message);
    const time = parse(message.created_at);
    const day = time ? dayLabel(message.created_at) : "";
    const newDay = day !== "" && day !== previous?.day;
    if (newDay) items.push({ kind: "day", key: `day-${day}-${message.id}`, label: day });
    const grouped =
      !newDay && previous !== null && previous.sender === sender && previous.time !== null &&
      time !== null && time.getTime() - previous.time.getTime() <= GROUP_WINDOW_MS;
    if (!grouped && lastMessage) lastMessage.last = true;
    const item = { kind: "message" as const, key: message.id, message, sender, first: !grouped, last: false };
    items.push(item);
    lastMessage = item;
    previous = { sender, time, day: day || (previous?.day ?? "") };
  }
  if (lastMessage) lastMessage.last = true;
  return items;
}

/** Strip any leading "@" so the UI can add exactly one; blank becomes null. */
export function cleanUsername(username: string | null | undefined): string | null {
  const bare = (username ?? "").trim().replace(/^@+/, "").trim();
  return bare ? bare : null;
}

/** A copy of the contact with a clean username, for the identity helpers. */
export function cleanContact<C extends { telegram_username: string | null }>(contact: C): C {
  return { ...contact, telegram_username: cleanUsername(contact.telegram_username) };
}

/**
 * The row's second line, or null when it would only repeat the title (a nameless sender
 * whose title is already the handle) or only name the channel (the row's badge does).
 */
export function rowSubtitle(title: string, secondary: string, channelName: string): string | null {
  if (secondary === title || secondary === channelName) return null;
  if (secondary === `${title} · ${channelName}`) return null;
  return secondary;
}

/** Still pending on the server but past its expiry: shown read-only. */
export function isExpired(approval: { status: string; expires_at: string }, now: number): boolean {
  if (approval.status !== "pending") return false;
  const time = parse(approval.expires_at);
  return time !== null && time.getTime() <= now;
}

export type ApprovalTone = "attention" | "neutral" | "success";

/** Words for an approval's state; unknown codes never reach the screen. */
export function approvalLabel(status: string, expired: boolean): { text: string; tone: ApprovalTone } {
  if (status === "pending") return expired ? { text: "Expired", tone: "neutral" } : { text: "Needs review", tone: "attention" };
  if (status === "approved") return { text: "Approved", tone: "success" };
  if (status === "edited") return { text: "Sent after edit", tone: "success" };
  if (status === "rejected") return { text: "Rejected", tone: "neutral" };
  if (status === "expired") return { text: "Expired", tone: "neutral" };
  return { text: "Handled", tone: "neutral" };
}

/** Messages newer than the newest one the reader has seen at the bottom. */
export function unseenCount(times: string[], seenAt: number | null): number {
  if (seenAt === null) return 0;
  let count = 0;
  for (const at of times) {
    const time = parse(at);
    if (time && time.getTime() > seenAt) count += 1;
  }
  return count;
}

export function newMessagesText(count: number): string {
  return count === 1 ? "1 new message" : `${count} new messages`;
}

/** The conversation J (next) or K (previous) moves to; stops at the ends. */
export function neighbourId(ids: string[], current: string, direction: "next" | "previous"): string | null {
  if (ids.length === 0) return null;
  const index = ids.indexOf(current);
  if (index === -1) return ids[0];
  const next = direction === "next" ? Math.min(index + 1, ids.length - 1) : Math.max(index - 1, 0);
  return ids[next];
}

/** Within 80 px of the bottom counts as reading the newest message. */
export const BOTTOM_SLACK_PX = 80;

export function nearBottom(box: { scrollTop: number; scrollHeight: number; clientHeight: number }): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= BOTTOM_SLACK_PX;
}
