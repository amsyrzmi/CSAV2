"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/identity-contracts";
import type { ConversationPage } from "@/lib/inbox-api";
import {
  REVIEW_LABEL,
  activeServiceCount,
  canSeeInboxCount,
  stripParts,
  type StripInput,
} from "@/lib/overview-strip";
import type { PackageState } from "@/lib/services-api";

const REFRESH_MS = 60_000;
const UNKNOWN: StripInput = { review: null, services: null };

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

/**
 * Two real numbers above the Overview chat: replies needing review (only where the role and the
 * Customer Support package allow the Inbox) and active services. Each read stands alone: a failed
 * or disallowed read hides only its number, never shows zero, and with neither there is no strip.
 * Plain text, not a live region, so the slow refresh does not announce itself.
 */
export function StatusStrip({ me, clientId }: { me: Me; clientId: string | null }) {
  const [counts, setCounts] = useState<StripInput>(UNKNOWN);
  const role = me.role;

  useEffect(() => {
    const tenant = clientId ? `client_id=${encodeURIComponent(clientId)}` : "";
    let current: AbortController | null = null;

    async function load() {
      current?.abort();
      const controller = new AbortController();
      current = controller;
      const { signal } = controller;
      let packages: PackageState[] | null = null;
      try {
        const answer = await api<{ packages: PackageState[] }>(
          `/console/v1/services/active${tenant ? `?${tenant}` : ""}`,
          { signal },
        );
        packages = Array.isArray(answer?.packages) ? answer.packages : null;
      } catch {
        if (signal.aborted) return;
      }
      let review: number | null = null;
      if (canSeeInboxCount(role, packages)) {
        try {
          const page = await api<ConversationPage>(
            `/console/v1/inbox/conversations?limit=1${tenant ? `&${tenant}` : ""}`,
            { signal },
          );
          review = typeof page?.needs_review_count === "number" ? page.needs_review_count : null;
        } catch {
          if (signal.aborted) return;
        }
      }
      if (signal.aborted) return;
      setCounts({ review, services: activeServiceCount(packages) });
    }

    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void load();
    }, REFRESH_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      current?.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [clientId, role]);

  const parts = stripParts(counts);
  if (!parts.length) return null;
  return (
    <ul data-testid="overview-strip" aria-label="Workspace status" className="flex flex-wrap items-center gap-2">
      {parts.map((part) => {
        const review = part.label === REVIEW_LABEL;
        const tone = review ? (part.value > 0 ? tones.warning : tones.neutral) : tones.services;
        return (
          <li
            key={part.label}
            data-testid={review ? "overview-strip-review" : "overview-strip-services"}
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
