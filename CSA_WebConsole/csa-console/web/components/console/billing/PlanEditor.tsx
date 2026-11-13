"use client";

import { useId, useRef, useState } from "react";
import { postPlan, type Bundle, type Stage } from "@/lib/billing-admin-api";
import {
  bundleLabel, fullDay, planBody, planErrorMessage, stageLabel, suggestedDates,
} from "@/lib/billing-admin-view";
import { action, field, Notice, secondary } from "../ui";

const STAGES: Stage[] = ["pilot", "collaborator", "terminated"];
const BUNDLES: Bundle[] = ["individual", "curated", "complete"];

/**
 * One company's "Billing plan" form: stage, bundle (not asked for a termination, which keeps the
 * bundle in force), effective date with suggested 5ths, and a required reason. A termination asks
 * for a second confirmation. Platform admins only: the page and the API both check.
 *
 * `terminatedOn` is the company's termination date when the caller knows it (the Billing page
 * does); termination is final, so no form is offered then. Without it, the server refuses.
 */
export function PlanEditor({
  clientId, name, stage: currentStage = null, bundle: currentBundle = null, terminatedOn = null, onSaved,
}: {
  clientId: string;
  name: string;
  stage?: Stage | null;
  bundle?: Bundle | null;
  terminatedOn?: string | null;
  onSaved?: () => void;
}) {
  const id = useId();
  const [stage, setStage] = useState<Stage>(currentStage && currentStage !== "terminated" ? currentStage : "pilot");
  const [bundle, setBundle] = useState<Bundle>(currentBundle ?? "individual");
  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const working = useRef(false);

  if (terminatedOn !== null) {
    return (
      <Notice testId="billing-plan-final">
        {name} was terminated on {fullDay(terminatedOn)}. Termination is final, so no further plan changes can be saved.
      </Notice>
    );
  }

  const suggestions = suggestedDates(new Date(), stage);
  const terminating = stage === "terminated";

  async function save() {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    try {
      const row = await postPlan(clientId, planBody({ stage, bundle, effective_from: date, reason }));
      setSaved(
        row.stage === "terminated"
          ? `Saved: ${name} is terminated from ${fullDay(row.effective_from)}.`
          : `Saved: ${name} is on ${stageLabel(row.stage)}, ${bundleLabel(row.bundle)} from ${fullDay(row.effective_from)}.`,
      );
      setReason("");
      setDate("");
      setConfirming(false);
      onSaved?.();
    } catch (cause) {
      setError(planErrorMessage(cause));
      setConfirming(false);
    } finally {
      working.current = false;
      setBusy(false);
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaved("");
    setError("");
    if (!reason.trim()) {
      setError("Write a reason for this change. It is kept in the audit log.");
      return;
    }
    if (terminating && !confirming) {
      setConfirming(true);
      return;
    }
    void save();
  }

  function change(next: () => void) {
    next();
    setConfirming(false);
    setSaved("");
  }

  return (
    <form onSubmit={submit} className="space-y-4" data-testid="billing-plan-form" aria-label={`Billing plan for ${name}`}>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor={`${id}-stage`} className="mb-2 block text-sm">Stage</label>
          <select id={`${id}-stage`} className={field} value={stage} disabled={busy}
            onChange={(e) => change(() => setStage(e.target.value as Stage))}>
            {STAGES.map((value) => <option key={value} value={value}>{stageLabel(value)}</option>)}
          </select>
        </div>
        {!terminating && (
          <div>
            <label htmlFor={`${id}-bundle`} className="mb-2 block text-sm">Bundle</label>
            <select id={`${id}-bundle`} className={field} value={bundle} disabled={busy}
              onChange={(e) => change(() => setBundle(e.target.value as Bundle))}>
              {BUNDLES.map((value) => <option key={value} value={value}>{bundleLabel(value)}</option>)}
            </select>
          </div>
        )}
      </div>
      {terminating && (
        <p className="text-sm text-ink-600">
          A termination keeps the bundle in force and bills the final period up to the termination date.
        </p>
      )}
      <div>
        <label htmlFor={`${id}-date`} className="mb-2 block text-sm">Takes effect on</label>
        <input id={`${id}-date`} type="date" className={`${field} max-w-xs`} required value={date} disabled={busy}
          aria-describedby={`${id}-date-hint`} onChange={(e) => change(() => setDate(e.target.value))} />
        <p id={`${id}-date-hint`} className="mt-2 text-sm text-ink-600">
          {terminating
            ? "A termination can be any day from the current cycle's start, and it is final. "
            : "Plan changes take effect on a 5th, the start of a billing cycle. "}
          A company&apos;s first plan can start on any day of the current cycle: that day is its join date, and no later
          change can be dated before it. Dates are Kuala Lumpur days.
        </p>
        <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Suggested dates">
          {suggestions.map((suggestion) => (
            <button key={suggestion.date} type="button" disabled={busy} aria-pressed={date === suggestion.date}
              className="min-h-10 rounded-[var(--fs-radius-control)] border border-ink-300 px-3 text-sm hover:bg-brand-50 aria-pressed:border-brand-600 aria-pressed:font-medium pointer-coarse:min-h-11"
              onClick={() => change(() => setDate(suggestion.date))}>
              {suggestion.label}
            </button>
          ))}
        </div>
      </div>
      <div>
        <label htmlFor={`${id}-reason`} className="mb-2 block text-sm">Reason</label>
        <textarea id={`${id}-reason`} className={`${field} h-auto py-2`} rows={2} required maxLength={500}
          value={reason} disabled={busy} aria-describedby={`${id}-reason-hint`}
          onChange={(e) => change(() => setReason(e.target.value))} />
        <p id={`${id}-reason-hint`} className="mt-2 text-sm text-ink-600">Kept in the audit log with your name.</p>
      </div>
      {error && <Notice error testId="billing-plan-error">{error}</Notice>}
      {saved && <Notice testId="billing-plan-saved">{saved}</Notice>}
      {confirming ? (
        <div className="space-y-3" data-testid="billing-plan-confirm">
          <p className="text-sm font-medium">
            Terminate {name} from {fullDay(date)}? This is final: no plan change can be saved after it.
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="submit" className={action} disabled={busy}>{busy ? "Saving…" : "Yes, terminate"}</button>
            <button type="button" className={secondary} disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <button type="submit" className={action} disabled={busy}>
          {busy ? "Saving…" : terminating ? "Terminate…" : "Save plan"}
        </button>
      )}
    </form>
  );
}
