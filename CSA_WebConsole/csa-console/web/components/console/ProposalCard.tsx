"use client";

import { useEffect, useState } from "react";
import { errorLine, languageOf, panelText } from "@/lib/assistant-copy";
import { resultSummary } from "@/lib/assistant-view";
import { emailClient, type Draft } from "@/lib/email-api";
import { cardFieldsFor, draftIdFromCard, emailCardBlock, emailErrorSentence } from "@/lib/email-recipients";
import {
  cancelProposal,
  confirmProposal,
  type ProposalOut,
} from "@/lib/workspace-api";
import { EmailPreview } from "./EmailPreview";
import { action, Notice, secondary } from "./ui";

/** Codes after which confirming this same card again cannot help. */
const FINAL_EMAIL_CODES = new Set([
  "draft_changed",
  "draft_already_sent",
  "draft_already_sending",
  "draft_already_exported",
  "send_outcome_unknown",
  "send_released",
]);

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
  const [blocked, setBlocked] = useState(false);
  const current = settled ?? proposal;
  const { card } = current;
  const language = languageOf(card);
  const isEmail = proposal.kind === "email_send" || proposal.kind === "email_export";
  // Email cards store counts only: the content is read from the person's own draft on open.
  const draftId = isEmail && proposal.status === "requested" ? draftIdFromCard(proposal.card) : null;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftError, setDraftError] = useState("");

  useEffect(() => {
    if (!draftId) return;
    let live = true;
    emailClient(tenantId ?? "").getDraft(draftId).then(
      (value) => { if (live) setDraft(value); },
      (cause: unknown) => {
        if (live)
          setDraftError(emailErrorSentence(cause, language) ?? panelText(language, "email_open_failed"));
      },
    );
    return () => { live = false; };
  }, [draftId, tenantId, language]);

  // Already sent, sending or exported says so on both email cards. A send card also says when
  // [STAFF: …] parts are left to fill in, or when the draft was edited since the card was made.
  const staleCode = isEmail && draft ? emailCardBlock(proposal.card, draft) ?? undefined : undefined;
  const waitingForDraft = draftId !== null && draft === null;

  async function decide(kind: "confirm" | "cancel") {
    setBusy(kind);
    setError("");
    try {
      const call = kind === "confirm" ? confirmProposal : cancelProposal;
      const next = await call(current.id, undefined, tenantId);
      setSettled(next);
      onSettled?.(next);
    } catch (cause) {
      const sentence = isEmail ? emailErrorSentence(cause, language) : null;
      if (sentence && FINAL_EMAIL_CODES.has((cause as { code?: string }).code ?? "")) setBlocked(true);
      // Server details are written as user-facing copy; show them verbatim.
      setError(
        sentence ??
          (language === "ms" ? errorLine(cause, "ms") : cause instanceof Error ? cause.message : "The request failed."),
      );
    } finally {
      setBusy(null);
    }
  }

  const fields = cardFieldsFor(card, isEmail);
  return (
    <article
      data-testid="proposal-card"
      aria-label={card.title}
      className="mt-3 rounded-md border border-brand-200 bg-white p-4 text-sm"
    >
      <h3 className="font-semibold">{card.title}</h3>
      <p className="mt-1">{card.summary}</p>
      {draft && (
        <div className="mt-3">
          <EmailPreview
            draft={draft}
            recipientsTestId="proposal-email-recipients"
            bodyTestId="proposal-email-body"
            language={language}
          />
        </div>
      )}
      {draftError && (
        <div className="mt-3">
          <Notice error>{draftError}</Notice>
        </div>
      )}
      {fields.length > 0 && (
        <dl className="mt-3 space-y-1">
          {fields.map((item, index) => (
            <div key={`${item.label}-${index}`} className="grid gap-1 sm:grid-cols-[8rem_1fr]">
              <dt className="text-ink-500">{item.label}</dt>
              <dd className="whitespace-pre-wrap break-words">{item.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-3 text-xs text-ink-500">
        {panelText(language, "expires", { when: new Date(card.expires_at).toLocaleString() })}
      </p>
      {(error || (staleCode && current.status === "requested")) && (
        <div className="mt-3">
          <Notice error>{error || emailErrorSentence({ code: staleCode }, language)}</Notice>
        </div>
      )}
      {current.status === "requested" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {!blocked && !staleCode && (
            <button
              type="button"
              data-testid="proposal-confirm"
              className={action}
              disabled={busy !== null || waitingForDraft || draftError !== ""}
              onClick={() => decide("confirm")}
            >
              {panelText(
                language,
                proposal.kind === "email_send"
                  ? busy === "confirm" ? "button_sending" : "button_send"
                  : busy === "confirm" ? "button_confirming" : "button_confirm",
              )}
            </button>
          )}
          <button
            type="button"
            data-testid="proposal-cancel"
            className={secondary}
            disabled={busy !== null}
            onClick={() => decide("cancel")}
          >
            {panelText(language, busy === "cancel" ? "button_cancelling" : "button_cancel")}
          </button>
        </div>
      ) : (
        <p role="status" data-testid="proposal-outcome" className="mt-3 font-medium">
          {resultSummary(current, language)}
        </p>
      )}
    </article>
  );
}
