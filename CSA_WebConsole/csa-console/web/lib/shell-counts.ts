/**
 * Pure rules behind the shell's one poll for the notification count and the Inbox queue count
 * (no React, no I/O, no runtime imports of the app).
 *
 * `null` always means "unknown": a count that has not been read or whose last read failed is
 * never shown as zero.
 */
import type { Me } from "./identity-contracts";
import type { NavBadges } from "./navigation";

export interface ShellCounts {
  /** Unread notifications for the signed-in person, or null while unknown. */
  unread: number | null;
  /** Conversations in Needs us, or null while unknown. */
  needsUs: number | null;
}

/**
 * Which reads the shell makes. A platform admin (or a person with no role) is not a company
 * member and makes none. Every company member, read-only included, has notifications. The
 * queue counts are asked for only when the navigation holds an Inbox link, because the inbox
 * routes refuse everyone else.
 */
export function wantsShellCounts(
  role: Me["role"] | null,
  hasInboxLink: boolean,
): { notifications: boolean; queue: boolean } {
  const member = role === "tenant_admin" || role === "tenant_staff" || role === "read_only";
  return { notifications: member, queue: member && hasInboxLink };
}

function wholeCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** `unread` from the notification count answer; null for anything that is not a whole number. */
export function parseUnread(body: unknown): number | null {
  if (typeof body !== "object" || body === null) return null;
  return wholeCount((body as Record<string, unknown>).unread);
}

/** `needs_us` from the queue counts answer; null for anything that is not a whole number. */
export function parseNeedsUs(body: unknown): number | null {
  if (typeof body !== "object" || body === null) return null;
  return wholeCount((body as Record<string, unknown>).needs_us);
}

/** A failed or malformed read (parsed null) keeps the previous answer. */
export function nextCount(previous: number | null, parsed: number | null): number | null {
  return parsed === null ? previous : parsed;
}

function positiveCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

/** The count badges of the navigation: only Inbox, from Needs us, and only above 0. */
export function navBadges(counts: ShellCounts): NavBadges {
  return positiveCount(counts.needsUs) ? { inbox: counts.needsUs } : {};
}

/**
 * The accessible name of a navigation link that carries a count badge. The badge itself is
 * hidden from assistive technology; the true number is in the name only when it is above 0, so
 * a link with no count keeps exactly its label.
 */
export function badgeLinkName(label: string, count: number | null | undefined): string {
  return positiveCount(count) ? `${label}, ${count} need your team` : label;
}
