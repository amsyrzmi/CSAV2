/** How the inbox names and colours a customer. No imports, so node --test can load it. */

export type CustomerLike = {
  display_name: string | null;
  telegram_username: string | null;
  phone_masked: string | null;
};

/** Four blues; each text colour is at least 4.5:1 on its background (checked in the tests). */
export const CUSTOMER_TINTS = [
  { background: "#D9E5FF", color: "#183DA5" },
  { background: "#CFEBFF", color: "#0B5A8C" },
  { background: "#E0E6FB", color: "#2C3E9E" },
  { background: "#D2EEF6", color: "#164B8D" },
] as const;

function present(value: string | null | undefined): string | null {
  const text = value?.trim();
  return text ? text : null;
}

/** First code point of each of the first two words, upper-cased; null for a blank name. */
export function initialsFor(name: string | null): string | null {
  const words = (name ?? "").split(/\s+/).filter(Boolean).slice(0, 2);
  if (words.length === 0) return null;
  return words.map((word) => String.fromCodePoint(word.codePointAt(0) ?? 63).toUpperCase()).join("");
}

/** Stable FNV-1a (32 bit) over the id, so a person keeps their colour. */
export function tintIndex(id: string): 0 | 1 | 2 | 3 {
  let hash = 0x811c9dc5;
  for (let i = 0; i < id.length; i += 1) {
    hash ^= id.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return ((hash >>> 0) % 4) as 0 | 1 | 2 | 3;
}

/** Name, else @username, else the masked number, else "Unknown sender". */
export function rowTitle(contact: CustomerLike): string {
  const name = present(contact.display_name);
  if (name) return name;
  const username = present(contact.telegram_username);
  if (username) return `@${username}`;
  return present(contact.phone_masked) ?? "Unknown sender";
}

/** Handle or masked number plus the channel, for example "@aisyah_r · Telegram". */
export function secondaryLine(
  contact: CustomerLike,
  channel: "telegram" | "whatsapp",
): string {
  const channelName = channel === "telegram" ? "Telegram" : "WhatsApp";
  const username = present(contact.telegram_username);
  const handle =
    channel === "telegram"
      ? (username ? `@${username}` : present(contact.phone_masked))
      : (present(contact.phone_masked) ?? (username ? `@${username}` : null));
  return handle ? `${handle} · ${channelName}` : channelName;
}

export function senderLabel(known: boolean): "Customer" | "New sender" {
  return known ? "Customer" : "New sender";
}
