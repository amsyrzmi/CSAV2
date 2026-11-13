/**
 * Pure wording and rules for linking contacts: the "Linked contacts" panel in a subscription and
 * the "Needs review" list on the Sign-ups tab. One person can reach the business on several
 * channels, and staff decide which contacts are the same person. Nothing here shows an email, a
 * Telegram account or a value a link moved: the server never sends them.
 */
import {
  formatDay,
  type LinkedContact,
  type LinkIn,
  type LinkOut,
  type LinkReason,
  type ReviewReason,
} from "@/lib/subscriptions-api";

const CHANNEL_NAMES: Record<string, string> = {
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  email: "Email",
  tiktok_shop: "TikTok Shop",
  web: "Web",
};

/** "Telegram", "Telegram and WhatsApp", "Email, Telegram and WhatsApp"; none gives a plain note. */
export function channelsText(channels: string[]): string {
  if (channels.length === 0) return "No chat channel yet";
  const names = channels.map((code) => {
    const known = CHANNEL_NAMES[code];
    if (known) return known;
    const words = code.replaceAll("_", " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  });
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export function contactLabel(contact: Pick<LinkedContact, "full_name" | "phone">): string {
  return contact.full_name?.trim() || contact.phone?.trim() || "Unnamed customer";
}

/** The two contacts as picked; the server decides which one survives. */
export function linkBody(contactA: string, contactB: string): LinkIn {
  return { contact_a: contactA, contact_b: contactB };
}

export function linkReasonText(reason: LinkReason): string {
  return reason === "verified_phone" ? "Matched by a verified phone number" : "Linked by staff";
}

const WINDOW_DAYS = 30;

/**
 * Why a link has no Unlink button, or null while it can be undone. The server's `can_unlink` is
 * the rule; this says which of its three reasons applies: already undone, older than 30 days, or
 * (the only one left) a newer link on either contact that has to be undone first.
 */
export function unlinkNote(
  link: Pick<LinkOut, "can_unlink" | "created_at" | "unlinked_at">,
  now: Date,
  timeZone?: string,
): string | null {
  if (link.can_unlink) return null;
  if (link.unlinked_at) return `Undone on ${formatDay(link.unlinked_at, timeZone)}.`;
  const age = now.getTime() - new Date(link.created_at).getTime();
  if (age > WINDOW_DAYS * 86_400_000) return "This link is older than 30 days and can no longer be undone here.";
  return "A newer link on one of these contacts has to be undone first.";
}

const COLUMN_NAMES: Record<string, string> = {
  phone: "phone number",
  phone_verified_at: "phone verification",
  telegram_id: "Telegram account",
};

/**
 * What an unlink left alone, for the staff member who just undid it, or null when everything
 * went back. `skipped` entries are "role.column" with role survivor or loser.
 */
export function skippedSentence(
  skipped: string[],
  link: Pick<LinkOut, "survivor" | "loser">,
): string | null {
  if (skipped.length === 0) return null;
  const groups = new Map<string, string[]>();
  for (const entry of skipped) {
    const [role, ...rest] = entry.split(".");
    const column = rest.join(".");
    const who = contactLabel(role === "survivor" ? link.survivor : link.loser);
    groups.set(who, [...(groups.get(who) ?? []), COLUMN_NAMES[column] ?? column.replaceAll("_", " ")]);
  }
  const text = [...groups].map(([who, things]) =>
    `${who}'s ${things.length === 1 ? things[0] : `${things.slice(0, -1).join(", ")} and ${things[things.length - 1]}`}`);
  return `Unlinked. Not everything could be put back, because it changed after the link or another contact now has it: ${text.join(", ")}.`;
}

const REVIEW_SENTENCES: Record<ReviewReason, string> = {
  several_matches: "Several contacts have this phone number, so it was not linked automatically.",
  telegram_conflict: "Both contacts have their own Telegram account, so they were not linked automatically.",
  same_channel_identity:
    "Both contacts already chat on the same channel with different accounts, so they were not linked automatically.",
  table_conflict:
    "Both contacts have a record that would clash if they were linked, such as a waiting sign-up for the same plan, so they were not linked automatically.",
  previously_unlinked: "Staff unlinked these two contacts before, so they were not linked again automatically.",
};

/** One plain sentence for why the system asked staff, whatever code a newer server may send. */
export function reviewReasonSentence(reason: string): string {
  return Object.hasOwn(REVIEW_SENTENCES, reason)
    ? REVIEW_SENTENCES[reason as ReviewReason]
    : "These two contacts could not be linked automatically.";
}

/** Why a chosen contact cannot be linked to this customer, or null when it can. */
export function linkChoiceProblem(
  contactId: string,
  chosen: { id: string },
  links: Pick<LinkOut, "survivor" | "loser" | "unlinked_at">[],
): string | null {
  if (chosen.id === contactId) return "Choose a different contact. This is the same customer.";
  const already = links.some(
    (link) => link.unlinked_at === null && (link.survivor.id === chosen.id || link.loser.id === chosen.id),
  );
  return already ? "These two are already linked." : null;
}
