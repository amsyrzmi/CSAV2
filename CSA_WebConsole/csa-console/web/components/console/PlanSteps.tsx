"use client";

import { useState } from "react";
import { languageOf, panelText, type Language } from "@/lib/assistant-copy";
import { emailClient, type Draft } from "@/lib/email-api";
import { emailErrorSentence } from "@/lib/email-recipients";
import { STEP_BADGE, isUncertain, stepReason } from "@/lib/plan-card";
import type { PlanStepResult, PlanStepStatus, ProposalCard } from "@/lib/workspace-api";
import { EmailPreview } from "./EmailPreview";
import { Notice, secondary } from "./ui";

const BADGE_TONE: Record<PlanStepStatus, string> = {
  done: "bg-[var(--fs-success-bg)] text-[color:var(--fs-success)]",
  failed: "bg-[var(--fs-danger-bg)] text-[color:var(--fs-danger)]",
  skipped: "bg-[var(--fs-warning-bg)] text-[color:var(--fs-warning)]",
  not_run: "bg-[var(--fs-warning-bg)] text-[color:var(--fs-warning)]",
};

/**
 * One row per step of a plan card: the fixed step title and, once the plan ran, how the step
 * ended. An email step's row carries its draft id; the draft opens through the same
 * `EmailPreview` as a single email card, read from the person's own draft on request (a plan
 * can hold 20 drafts, so none is fetched until asked). The card holds no address, name,
 * subject or body, and neither does any other text here.
 */
export function PlanSteps({
  card,
  steps,
  tenantId,
}: {
  card: Pick<ProposalCard, "fields" | "language">;
  steps: PlanStepResult[];
  tenantId?: string | null;
}) {
  const language = languageOf(card);
  const byRow = new Map(steps.map((step) => [step.index, step]));
  return (
    <ol
      data-testid="plan-steps"
      aria-label={panelText(language, "plan_steps_label")}
      className="mt-3 space-y-2"
    >
      {card.fields.map((row, index) => (
        <PlanStepRow
          key={index}
          label={row.label}
          draftId={row.value.trim() || null}
          step={byRow.get(index)}
          language={language}
          tenantId={tenantId}
        />
      ))}
    </ol>
  );
}

function PlanStepRow({
  label,
  draftId,
  step,
  language,
  tenantId,
}: {
  label: string;
  draftId: string | null;
  step: PlanStepResult | undefined;
  language: Language;
  tenantId?: string | null;
}) {
  const [shown, setShown] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [problem, setProblem] = useState("");
  const reason = step ? stepReason(step, language) : null;

  function toggle() {
    const next = !shown;
    setShown(next);
    if (!next || draft || !draftId) return;
    setProblem("");
    emailClient(tenantId ?? "")
      .getDraft(draftId)
      .then(setDraft, (cause: unknown) =>
        setProblem(emailErrorSentence(cause, language) ?? panelText(language, "email_open_failed")),
      );
  }

  return (
    <li
      data-testid="plan-step"
      data-status={step?.status ?? "pending"}
      className="rounded-md border border-[color:var(--fs-border)] p-2"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 break-words">{label}</span>
        <span className="flex items-center gap-2">
          {step && (
            <span
              data-testid="plan-step-status"
              className={`rounded px-1.5 py-0.5 text-xs font-medium ${isUncertain(step) ? BADGE_TONE.skipped : BADGE_TONE[step.status]}`}
            >
              {panelText(language, STEP_BADGE[step.status])}
            </span>
          )}
          {draftId && (
            <button
              type="button"
              data-testid="plan-step-email"
              aria-expanded={shown}
              className={`${secondary} !min-h-8 !px-2 !py-1 text-xs`}
              onClick={toggle}
            >
              {panelText(language, shown ? "plan_hide_email" : "plan_show_email")}
            </button>
          )}
        </span>
      </div>
      {reason && (
        <p data-testid="plan-step-reason" className="mt-1 text-xs text-[color:var(--fs-text-secondary)]">
          {reason}
        </p>
      )}
      {shown && problem && (
        <div className="mt-2">
          <Notice error>{problem}</Notice>
        </div>
      )}
      {shown && draft && (
        <div className="mt-2">
          <EmailPreview
            draft={draft}
            recipientsTestId="plan-step-email-recipients"
            bodyTestId="plan-step-email-body"
            language={language}
          />
        </div>
      )}
    </li>
  );
}
