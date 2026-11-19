"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/identity-contracts";
import type { ConversationPage } from "@/lib/inbox-api";
import {
  MINE_LABEL,
  NEEDS_US_LABEL,
  OVERDUE_LABEL,
  REVIEW_LABEL,
  SERVICES_LABEL,
  activeServiceCount,
  canSeeInboxCount,
  stripParts,
} from "@/lib/overview-strip";
import type { PackageState } from "@/lib/services-api";

const pill =
  "inline-flex min-h-8 items-center gap-1.5 rounded-[var(--fs-radius-round)] border px-3 py-1 text-sm";
const tones = {
  services:
    "border-transparent bg-[var(--fs-surface-blue)] text-[color:var(--fs-text)]",
  warning:
    "border-[color:var(--fs-warning)]/30 bg-[var(--fs-warning-bg)] text-[color:var(--fs-warning)]",
  neutral:
    "border-[color:var(--fs-border)] bg-[var(--fs-surface)] text-[color:var(--fs-text)]",
};

/** What the strip keeps from the Inbox read; the queue numbers are null when `counts` is missing. */
type InboxRead = { review: number | null; needsUs: number | null; overdue: number | null; mine: number | null };

/** The strip's test id for each part. */
const TEST_IDS: Record<string, string> = {
  [REVIEW_LABEL]: "overview-strip-review",
  [SERVICES_LABEL]: "overview-strip-services",
  [NEEDS_US_LABEL]: "overview-strip-needs-us",
  [OVERDUE_LABEL]: "overview-strip-overdue",
  [MINE_LABEL]: "overview-strip-mine",
};

function queueNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/**
 * Real numbers above the Overview body: replies needing review (only where the role and the
 * Customer Support package allow the Inbox), the queue counts that ride on the same read (Needs us,
 * Overdue and, for a company member, Mine) and active services. The packages come from the page,
 * which reads them once for everything that needs them; `undefined` means still loading, `null`
 * that the read failed. Each number stands alone: a failed or disallowed read hides only its
 * number, never shows zero, and with neither there is no strip. Plain text, not a live region, so
 * the slow refresh does not announce itself.
 */
export function StatusStrip({ me, clientId, packages }: {
  me: Me;
  clientId: string | null;
  packages: PackageState[] | null | undefined;
}) {
  // undefined until the Inbox read answers, null when it failed.
  const [fetched, setFetched] = useState<InboxRead | null | undefined>(undefined);
  // The Inbox count is only asked for when the packages say Customer Support is on, so a company
  // without it (or a failed services read) makes no Inbox request at all.
  const wantsReview = packages !== undefined && canSeeInboxCount(me.role, packages);

  useEffect(() => {
    if (!wantsReview) return;
    const tenant = clientId ? `&client_id=${encodeURIComponent(clientId)}` : "";
    const controller = new AbortController();
    api<ConversationPage>(`/console/v1/inbox/conversations?limit=1${tenant}`, { signal: controller.signal })
      .then((page) => {
        const counts = page?.counts ?? null;
        setFetched({
          review: typeof page?.needs_review_count === "number" ? page.needs_review_count : null,
          needsUs: queueNumber(counts?.needs_us),
          overdue: queueNumber(counts?.overdue),
          mine: queueNumber(counts?.mine),
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) setFetched(null);
      });
    // `packages` is a dependency on purpose: each fresh read of the packages refreshes the count.
    return () => controller.abort();
  }, [wantsReview, packages, clientId]);

  // Nothing until both answers are in, so the strip appears whole rather than growing a second pill.
  const waiting = packages === undefined || (wantsReview && fetched === undefined);
  const parts = waiting
    ? []
    : stripParts({
        review: wantsReview ? (fetched?.review ?? null) : null,
        services: activeServiceCount(packages ?? null),
        needsUs: wantsReview ? (fetched?.needsUs ?? null) : null,
        overdue: wantsReview ? (fetched?.overdue ?? null) : null,
        // The platform admin is not a member of the company, so "Mine" would only ever be zero.
        mine: wantsReview && me.role !== "platform_admin" ? (fetched?.mine ?? null) : null,
      });
  if (!parts.length) return null;
  return (
    <ul data-testid="overview-strip" aria-label="Workspace status" className="flex flex-wrap items-center gap-2">
      {parts.map((part) => {
        const warns = part.label === REVIEW_LABEL || part.label === OVERDUE_LABEL;
        const queue = part.label !== SERVICES_LABEL;
        const tone = warns ? (part.value > 0 ? tones.warning : tones.neutral) : queue ? tones.neutral : tones.services;
        return (
          <li
            key={part.label}
            data-testid={TEST_IDS[part.label]}
            className={`${pill} ${tone}`}
          >
            <strong className="font-semibold tabular-nums">{part.value}</strong>
            <span>{part.label}</span>
          </li>
        );
      })}
    </ul>
  );
}
