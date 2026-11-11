/**
 * Pure rules for the Connections tab: which cards a role and the company's packages allow, the
 * one-line results after Google or TikTok sends the browser back, and where those returns land.
 * The words come from the helpers the old pages already use; nothing here invents a new one.
 */
import type { Me } from "@/lib/identity-contracts";
import type { PackageState } from "@/lib/services-api";
import type { ConnectionView } from "@/components/console/connections/ConnectionCard";
import type { StoreRecord } from "@/lib/business";
import type { CalendarStatus } from "@/lib/bookings-api";
import type { EmailContext } from "@/lib/email-api";
import { CALENDAR_EXPLAINER, CALENDAR_RECONNECT_BANNER, calendarResult, calendarStateWord } from "@/lib/bookings-view";
import { storeStatusTone, storeSummary } from "@/lib/business-view";
import { emailErrorSentence, formatWhen } from "@/lib/email-recipients";
import { connectErrorText, connectResult, syncLine } from "@/lib/tiktok-connect";

export type ConnectionKind = "tiktok" | "calendar" | "gmail";

export const ROLE_CANNOT = "Your role can't connect this.";
export const MANAGE_LINK = "Manage in Connections";
export const LOCKED: Record<ConnectionKind, string> = {
  tiktok: "Included in the Marketplaces add-on.",
  calendar: "Included in the Bookings package.",
  gmail: "Included in the Staff email package.",
};

/** The package that unlocks each card. */
const PACKAGE: Record<ConnectionKind, string> = {
  tiktok: "marketplaces",
  calendar: "bookings",
  gmail: "staff_email",
};

/** What a card draws for this role and these packages. */
export type CardGate = {
  /** False when the card is not on the tab for this role (Google cards for a platform admin). */
  show: boolean;
  /** The "Included in ..." sentence when the package is off; the card then reads and does nothing. */
  locked: string | null;
  /** Connect, Reconnect and Disconnect are enabled. */
  canAct: boolean;
  /** The tab may read this card's status (a read-only member's tab reads nothing). */
  readStatus: boolean;
  /** A line under the disabled button, or null. */
  note: string | null;
};

/**
 * Spec 9. TikTok needs `marketplaces`; acting needs a tenant or platform Admin, and staff see the
 * status with a disabled Connect. Calendar needs `bookings` and an Admin or staff role. Gmail
 * needs `staff_email` and any role but read-only. A platform admin has no Google connections.
 * `packages` null (the read failed) draws the cards unlocked: the server's own gates still refuse.
 * A card whose package is off shows only its sentence, whatever the role.
 */
export function cardGate(kind: ConnectionKind, role: Me["role"], packages: PackageState[] | null): CardGate {
  if (role === "platform_admin" && kind !== "tiktok") {
    return { show: false, locked: null, canAct: false, readStatus: false, note: null };
  }
  const on = packages === null || packages.some((p) => p.key === PACKAGE[kind] && p.status === "active");
  if (!on) return { show: true, locked: LOCKED[kind], canAct: false, readStatus: false, note: null };
  const admin = role === "platform_admin" || role === "tenant_admin";
  const member = admin || role === "tenant_staff";
  if (!member) return { show: true, locked: null, canAct: false, readStatus: false, note: ROLE_CANNOT };
  if (kind === "tiktok" && !admin) {
    return {
      show: true, locked: null, canAct: false, readStatus: true,
      note: connectErrorText("store_admin_required", ""),
    };
  }
  return { show: true, locked: null, canAct: true, readStatus: true, note: null };
}

/** The TikTok row of the tab for one store, or for no shop yet. A pending store has no last activity. */
export function tiktokView(store: StoreRecord | null, now: Date): ConnectionView {
  if (store === null) {
    return {
      tone: "neutral", word: "Not connected",
      sentence: "Connect your TikTok Shop to sync its orders and products.", lastActivity: null,
    };
  }
  const summary = storeSummary(store);
  return {
    tone: storeStatusTone(store.connection.status),
    word: summary.status,
    sentence: summary.subtitle,
    lastActivity: store.connection.status === "missing_shop_id"
      ? null
      : syncLine(null, store.sync?.orders_synced_at ?? null, now),
  };
}

/**
 * The word on the old Store connections page's "TikTok Shop: {word}." line: each TikTok shop's own
 * state word (the distinct ones, in list order), or "Not connected" when there is no TikTok shop.
 */
export function tiktokLineWord(stores: { platform: string; connection: { status: string } }[]): string {
  const words: string[] = [];
  for (const store of stores) {
    if (store.platform !== "tiktok_shop") continue;
    const word = storeSummary({ ...store, shop_id: null }).status;
    if (!words.includes(word)) words.push(word);
  }
  return words.length === 0 ? "Not connected" : words.join(", ");
}

/**
 * The Google Calendar row: the existing state words, the banner sentence for a connection Google
 * stopped accepting, and the two ways it can be unavailable (not switched on, not linked to a
 * person). The status carries no timestamp, so there is no last activity. `null` reads as off.
 */
export function calendarView(status: CalendarStatus | null): ConnectionView {
  if (!status || !status.available) {
    return {
      tone: "neutral", word: "Not switched on", lastActivity: null,
      sentence: "Google Calendar isn't switched on for this company yet. Ask your Admin.",
    };
  }
  if (!status.can_connect || status.status === null) {
    return {
      tone: "neutral", word: "Not available", lastActivity: null,
      sentence: "Your account isn't linked to a person in Bookings yet. Ask your Admin to add you on People & rooms.",
    };
  }
  const word = calendarStateWord(status.status);
  if (status.status === "needs_reconnect") {
    return { tone: "attention", word, sentence: CALENDAR_RECONNECT_BANNER, lastActivity: null };
  }
  return {
    tone: status.status === "connected" ? "success" : "neutral", word, sentence: CALENDAR_EXPLAINER, lastActivity: null,
  };
}

export const GMAIL_UNAVAILABLE = "Connecting Gmail isn't available yet. Contact fourstacks support.";

/** The Gmail row, with the Email page's state words and tones; `null` (nothing read) reads as not connected. */
export function gmailView(context: EmailContext | null): ConnectionView {
  const none: ConnectionView = {
    tone: "neutral", word: "Not connected", sentence: "No mailbox connected for your account.", lastActivity: null,
  };
  if (!context) return none;
  if (!context.enabled) {
    return {
      tone: "neutral", word: "Switched off", lastActivity: null,
      sentence: "The email assistant is switched off for your company.",
    };
  }
  const mailbox = context.mailbox;
  if (!mailbox) return none;
  const synced = mailbox.last_sync_at ?? context.last_synced_at;
  const lastActivity = synced ? `Last checked ${formatWhen(synced)}.` : "Not checked yet.";
  if (mailbox.status === "connected") {
    return {
      tone: "success", word: "Connected", lastActivity,
      sentence: `Your Gmail ${mailbox.address ?? "(pending)"} is connected to the Email assistant.`,
    };
  }
  if (mailbox.status === "pending") {
    return { tone: "attention", word: "Not finished", lastActivity, sentence: "Gmail has not finished connecting." };
  }
  if (mailbox.status === "revoked") {
    return {
      tone: "error", word: "Access removed", lastActivity,
      sentence: "Gmail access was removed, so new email will not arrive.",
    };
  }
  // A status this page has no word for reads as no mailbox, as the Email page's own status does.
  return none;
}

/** The line under a disabled Connect Gmail when this server has no Google client set up. */
export function gmailNote(context: EmailContext | null): string | null {
  return context && !context.provider_configured ? GMAIL_UNAVAILABLE : null;
}

/** Where the tab lives: a platform admin's address keeps the chosen company. */
export function connectionsHref(platform: boolean, tenant: string | null): string {
  if (!platform) return "/portal/connections";
  return tenant ? `/connections?client_id=${encodeURIComponent(tenant)}` : "/connections";
}

/** The query parameters the three callbacks send back with; `client_id` is not one of them. */
export const ARRIVAL_KEYS = ["tiktok", "reason", "calendar", "calendar_error", "connected", "error"] as const;

export type Arrival = { kind: ConnectionKind; tone: "good" | "bad"; text: string };

const GMAIL_FAILED = "Gmail could not be connected. Try again.";

/** Every outcome the address carries, in card order (TikTok, Calendar, Gmail), with the existing sentences. */
export function arrivals(search: { get(name: string): string | null }): Arrival[] {
  const found: Arrival[] = [];
  const params = new URLSearchParams();
  for (const key of ["tiktok", "reason"]) {
    const value = search.get(key);
    if (value !== null) params.set(key, value);
  }
  const tiktok = connectResult(params);
  if (tiktok) found.push({ kind: "tiktok", ...tiktok });
  const calendar = calendarResult(search);
  if (calendar) found.push({ kind: "calendar", tone: calendar.tone === "success" ? "good" : "bad", text: calendar.text });
  const code = search.get("error");
  if (search.get("connected") !== null) {
    found.push({ kind: "gmail", tone: "good", text: "Mailbox connected." });
  } else if (code) {
    found.push({ kind: "gmail", tone: "bad", text: emailErrorSentence({ code }) ?? GMAIL_FAILED });
  }
  return found;
}
