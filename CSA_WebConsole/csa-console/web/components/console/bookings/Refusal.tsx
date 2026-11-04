"use client";

import { ApiError } from "@/lib/api";
import type { BookingFinding } from "@/lib/bookings-api";
import { findingCopy } from "@/lib/bookings-view";
import { action, Notice, secondary } from "../ui";
import { errorText } from "./parts";

const KNOWN: readonly string[] = ["outside_hours", "closed_day", "short_notice", "unavailable"];

/** How the server said no to a booking or a move, ready to show. */
export type Refusal =
  | { kind: "warn"; findings: BookingFinding[] }
  | { kind: "refused"; findings: BookingFinding[]; text: string }
  | { kind: "taken" }
  | { kind: "stale"; text: string }
  | { kind: "error"; text: string };

function findingsOf(cause: ApiError): BookingFinding[] {
  const found = cause.body?.findings;
  return Array.isArray(found)
    ? found.filter((item): item is BookingFinding => typeof item === "string" && KNOWN.includes(item))
    : [];
}

export function readRefusal(cause: unknown): Refusal {
  if (cause instanceof ApiError && cause.status === 409) {
    const findings = findingsOf(cause);
    if (cause.code === "needs_acknowledging" && findings.length) return { kind: "warn", findings };
    if (cause.code === "refused") return { kind: "refused", findings, text: cause.message };
    if (cause.code === "slot_taken") return { kind: "taken" };
    if (cause.code === "stale") return { kind: "stale", text: cause.message };
  }
  return { kind: "error", text: errorText(cause) };
}

/**
 * Says what happened and what can be done. A warning can be overridden by pressing the button,
 * which sends the findings back as acknowledged; a refusal cannot. A taken time keeps the form
 * as it was and offers to pick another; a stale booking offers to reload.
 */
export function RefusalNotice({
  refusal,
  proceedLabel,
  busy,
  onProceed,
  onPickAnother,
  onReload,
}: {
  refusal: Refusal;
  /** "Book anyway" or "Move anyway". */
  proceedLabel: string;
  busy: boolean;
  onProceed: () => void;
  onPickAnother: () => void;
  onReload: () => void;
}) {
  if (refusal.kind === "error") return <Notice error testId="booking-error">{refusal.text}</Notice>;
  if (refusal.kind === "taken") {
    return (
      <div className="space-y-2" data-testid="booking-taken">
        <Notice error>That time was just taken.</Notice>
        <button type="button" className={secondary} onClick={onPickAnother}>Pick another time</button>
      </div>
    );
  }
  if (refusal.kind === "stale") {
    return (
      <div className="space-y-2" data-testid="booking-stale">
        <Notice error>{refusal.text}</Notice>
        <button type="button" className={secondary} onClick={onReload}>Reload the calendar</button>
      </div>
    );
  }
  const sentences = refusal.findings.length
    ? refusal.findings.map(findingCopy).join(" ")
    : refusal.kind === "refused" ? refusal.text : "";
  if (refusal.kind === "refused") {
    return <Notice error testId="booking-refused">{sentences}</Notice>;
  }
  return (
    <div className="space-y-2" data-testid="booking-warning">
      <Notice warning>{sentences}</Notice>
      <button type="button" className={action} disabled={busy} data-testid="booking-anyway" onClick={onProceed}>
        {proceedLabel}
      </button>
    </div>
  );
}
