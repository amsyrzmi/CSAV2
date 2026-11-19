/** Pure rules behind the Overview status strip (no React, no I/O). */
import type { Me } from "./identity-contracts";
import type { PackageState } from "./services-api";

/** Each number is null when its read failed or the role may not see it. */
export type StripInput = {
  review: number | null;
  services: number | null;
  /** Queue counts from the Inbox read; absent or null means unknown (never shown as zero). */
  needsUs?: number | null;
  overdue?: number | null;
  mine?: number | null;
};
export type StripPart = { label: string; value: number };

export const REVIEW_LABEL = "Replies to check";
export const SERVICES_LABEL = "Services on";
export const NEEDS_US_LABEL = "Needs us";
export const OVERDUE_LABEL = "Overdue";
export const MINE_LABEL = "Mine";

/** A count the server could have sent; anything else is treated as unknown. */
function known(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

/**
 * The parts to show: Replies to check, Needs us, Overdue, Mine, Services on. An unknown number is left out, never shown as zero; an
 * empty list means no strip at all.
 */
export function stripParts(input: StripInput): StripPart[] {
  const parts: StripPart[] = [];
  if (known(input.review)) parts.push({ label: REVIEW_LABEL, value: input.review });
  if (known(input.needsUs)) parts.push({ label: NEEDS_US_LABEL, value: input.needsUs });
  if (known(input.overdue)) parts.push({ label: OVERDUE_LABEL, value: input.overdue });
  if (known(input.mine)) parts.push({ label: MINE_LABEL, value: input.mine });
  if (known(input.services)) parts.push({ label: SERVICES_LABEL, value: input.services });
  return parts;
}

/**
 * The review count comes from the Inbox list read, so it is only asked for when the role can
 * open the Inbox (not read-only) and Customer Support is active or suspended (the Inbox stays
 * reachable while suspended). Unknown packages mean no read.
 */
export function canSeeInboxCount(role: Me["role"], packages: PackageState[] | null): boolean {
  if (role === null || role === "read_only" || packages === null) return false;
  const support = packages.find((item) => item.key === "customer_support")?.status;
  return support === "active" || support === "suspended";
}

/** Active packages, or null when the services read did not answer. */
export function activeServiceCount(packages: PackageState[] | null): number | null {
  if (packages === null) return null;
  return packages.filter((item) => item.status === "active").length;
}
