/**
 * Pure rules for the Connections tab: which cards a role and the company's packages allow, the
 * one-line results after Google or TikTok sends the browser back, and where those returns land.
 * The words come from the helpers the old pages already use; nothing here invents a new one.
 */
import type { Me } from "@/lib/identity-contracts";
import type { PackageState } from "@/lib/services-api";
import { calendarResult } from "@/lib/bookings-view";
import { emailErrorSentence } from "@/lib/email-recipients";
import { connectErrorText, connectResult } from "@/lib/tiktok-connect";

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
