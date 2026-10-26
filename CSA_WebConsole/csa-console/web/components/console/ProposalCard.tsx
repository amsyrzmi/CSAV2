"use client";

import { useState } from "react";
import { resultSummary } from "@/lib/assistant-view";
import {
  cancelProposal,
  confirmProposal,
  type ProposalOut,
} from "@/lib/workspace-api";
import { action, Notice, secondary } from "./ui";

/** One shared-registry proposal: nothing changes until the user confirms it here. */
export function ProposalCard({
  proposal,
  tenantId,
  onSettled,
}: {
  proposal: ProposalOut;
  /** Required for platform admins, who must always name the tenant. */
  tenantId?: string | null;
  onSettled?: (proposal: ProposalOut) => void;
}) {
  const [settled, setSettled] = useState<ProposalOut | null>(null);
  const [busy, setBusy] = useState<"confirm" | "cancel" | null>(null);
  const [error, setError] = useState("");
  const current = settled ?? proposal;
  const { card } = current;

  async function decide(kind: "confirm" | "cancel") {
    setBusy(kind);
    setError("");
    try {
      const call = kind === "confirm" ? confirmProposal : cancelProposal;
      const next = await call(current.id, undefined, tenantId);
      setSettled(next);
      onSettled?.(next);
    } catch (cause) {
      // Server details are written as user-facing copy; show them verbatim.
      setError(cause instanceof Error ? cause.message : "The request failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <article
      data-testid="proposal-card"
      aria-label={card.title}
      className="mt-3 rounded-md border border-brand-200 bg-white p-4 text-sm"
    >
      <h3 className="font-semibold">{card.title}</h3>
      <p className="mt-1">{card.summary}</p>
      {card.fields.length > 0 && (
        <dl className="mt-3 space-y-1">
          {card.fields.map((item, index) => (
            <div key={`${item.label}-${index}`} className="grid gap-1 sm:grid-cols-[8rem_1fr]">
              <dt className="text-ink-500">{item.label}</dt>
              <dd className="whitespace-pre-wrap break-words">{item.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-3 text-xs text-ink-500">
        Expires {new Date(card.expires_at).toLocaleString()}
      </p>
      {error && (
        <div className="mt-3">
          <Notice error>{error}</Notice>
        </div>
      )}
      {current.status === "requested" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="proposal-confirm"
            className={action}
            disabled={busy !== null}
            onClick={() => decide("confirm")}
          >
            {busy === "confirm" ? "Confirming…" : "Confirm"}
          </button>
          <button
            type="button"
            data-testid="proposal-cancel"
            className={secondary}
            disabled={busy !== null}
            onClick={() => decide("cancel")}
          >
            {busy === "cancel" ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      ) : (
        <p role="status" data-testid="proposal-outcome" className="mt-3 font-medium">
          {resultSummary(current)}
        </p>
      )}
    </article>
  );
}
