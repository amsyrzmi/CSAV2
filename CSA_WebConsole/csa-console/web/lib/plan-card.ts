/**
 * What a plan card says about its steps (no React, no I/O): the badge and the reason for each
 * step of an executed plan, in the card's language, and whether the plan can be retried.
 *
 * The receipt holds a refusal `code` per step. It is never shown as it is: a code with a
 * sentence elsewhere in the console reuses that sentence, and any other reads as one generic
 * line. No step text names a person, an address, a subject or a body.
 */
import { explainedMessage } from "@/lib/api";
import { MS_ERRORS, panelText, type Language, type PanelKey } from "@/lib/assistant-copy";
import { emailErrorSentence } from "@/lib/email-recipients";
import type { PlanStepResult, PlanStepStatus } from "@/lib/workspace-api";

export const STEP_BADGE: Record<PlanStepStatus, PanelKey> = {
  done: "plan_step_done",
  failed: "plan_step_failed",
  skipped: "plan_step_skipped",
  not_run: "plan_step_not_run",
};

/** Failed and not-run steps are what the server's retry proposes again. */
export function canRetry(steps: readonly PlanStepResult[]): boolean {
  return steps.some((step) => step.status === "failed" || step.status === "not_run");
}

/** The short reason for a step that did not finish, or null for a step that did. */
export function stepReason(step: PlanStepResult, language: Language): string | null {
  const say = (key: PanelKey) => panelText(language, key);
  if (step.status === "done") return null;
  if (step.status === "not_run") return say("plan_reason_not_run");
  const code = step.code;
  if (step.status === "skipped" || code === "changed") return say("plan_reason_changed");
  if (code === "plan_deadline") return say("plan_reason_deadline");
  // The receipt keeps no wait time, so the email table's "try again in N minutes" would mislead.
  if (code === "send_limit_reached") return say("plan_reason_limit");
  if (typeof code === "string" && code) {
    const email = emailErrorSentence({ code }, language);
    if (typeof email === "string") return email;
    const known =
      language === "ms"
        ? Object.hasOwn(MS_ERRORS, code) ? MS_ERRORS[code] : undefined
        : explainedMessage(code);
    if (typeof known === "string") return known;
  }
  return say("plan_reason_generic");
}
