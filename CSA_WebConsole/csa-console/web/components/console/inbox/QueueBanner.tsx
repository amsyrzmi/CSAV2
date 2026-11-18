"use client";

import type { Conversation } from "@/lib/inbox-api";
import { assignedText, dueState, waitBannerText } from "@/lib/inbox-queue";

/**
 * The line under the thread header (design 14.4), in the `Notice` look: how long a needs-us item has
 * waited and when it is due, from the server's times, and who holds it when that is someone else.
 * Not a live region: the wait changes every minute and must not be read out each time. Refusals
 * (a held reply first, the customer wrote) are the feedback area's, so they are never repeated here.
 */
export function QueueBanner({ item, now, me }: {
  item: Conversation; now: number; me: { userId: string | null };
}) {
  const wait = now ? waitBannerText(item, now) : "";
  const assigned = assignedText(item, me);
  if (!wait && !assigned) return null;
  const late = wait !== "" && dueState(item, now) === "overdue";
  const tone = late
    ? "border-[var(--fs-warning)]/30 bg-[var(--fs-warning-bg)] text-[var(--fs-warning)]"
    : "border-brand-200 bg-brand-50 text-ink-900";
  return <div className="border-b border-[color:var(--fs-border)] px-3 py-2">
    <div data-testid="inbox-queue-banner"
      className={`rounded-[var(--fs-radius-control)] border px-3 py-2 text-sm [overflow-wrap:anywhere] ${tone}`}>
      {wait && <p>{wait}</p>}
      {assigned && <p>{assigned}</p>}
    </div>
  </div>;
}
