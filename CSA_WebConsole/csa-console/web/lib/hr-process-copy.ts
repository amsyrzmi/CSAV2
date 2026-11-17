import { explainedMessage } from "./api";

/**
 * Plain sentences for the refusal codes of the HR process routes (templates, processes, tasks).
 * The first block is `PROBLEMS` of csa/hr/processes.py, word for word (tests/hr-process-api.test.mjs
 * checks every one). Each says what is wrong or what to do, never the code. `process_already_active`
 * keeps this sentence: the start dialog adds "Replace it?" itself.
 */
const SENTENCES: Record<string, string> = {
  permission_denied: "Only the HR owner or someone with Manage people can do this.",
  resource_unavailable: "That item does not exist, or you cannot see it.",
  idempotency_conflict: "This request key was already used for a different change.",
  unsupported_operation: "That change is not supported.",
  human_operation_required: "Only a signed-in person can make this change.",
  template_unavailable: "That template is not available for this kind of process.",
  template_changed: "Someone changed this template since you opened it. Reload to see it.",
  template_kind_invalid: "Choose onboarding, offboarding or custom.",
  template_name_invalid: "Give the template a name of at most 120 characters.",
  template_invalid: "Active must be yes or no.",
  template_rules_invalid: "Check who the template applies to.",
  department_unavailable: "That department is not in this company.",
  template_steps_invalid: "A template needs 1 to 40 steps.",
  template_step_title_invalid: "Each step needs an English title of at most 160 characters.",
  template_step_text_invalid: "A description is at most 2000 characters, a reference 500.",
  template_step_owner_invalid: "Only a step owned by a named person names that person.",
  template_step_person_unavailable: "Choose an active person of this company for the step.",
  template_step_anchor_invalid: "Onboarding steps count from the start date or the probation end, offboarding steps from the last day.",
  template_step_offset_invalid: "A step is due at most 365 days before or after its date.",
  template_step_approval_invalid: "A step is approved by HR or the manager.",
  template_step_check_invalid: "That automatic check is not available.",
  process_kind_invalid: "Choose onboarding, offboarding or custom.",
  person_not_active: "This person is not active.",
  person_not_leaving: "Mark this person as leaving before starting their offboarding.",
  process_template_required: "Choose a template for a custom process.",
  process_no_template: "No active template applies to this person.",
  process_already_active: "This person already has an active process of this kind.",
  process_closed: "This process is already completed or cancelled.",
  person_changed: "Someone changed this person since you opened it. Reload to see it.",
  probation_end_invalid: "Give the probation end as a date, or clear it.",
  task_changed: "Someone changed this step since you opened it. Reload to see it.",
  task_closed: "This step is already done or skipped, or its process is closed.",
  task_not_assignee: "Only the person this step is assigned to, or HR, can do this.",
  task_not_approver: "Only the approver of this step can approve it or send it back.",
  task_awaiting_approval: "This step is waiting for approval.",
  task_not_awaiting_approval: "This step is not waiting for approval.",
  task_self_approval: "Someone other than the person who completed this step must approve it.",
  task_own_process: "Someone else in HR must approve a step of a process about you.",
  check_not_met: "The automatic check for this step is not met yet.",
  task_system_only: "The system completes this step when the person's access is removed.",
  task_reason_invalid: "Give a reason of 1 to 500 characters.",
  task_note_invalid: "A note is at most 2000 characters.",
  task_due_invalid: "Give the new due date as a date.",
  task_assignee_unavailable: "Choose an active person of this company, or the HR pool.",
  // Route-level answers the process routes share with the rest of People & HR.
  read_only_session: "Your account can only view People & HR. Ask an admin if you need to make changes.",
  invalid_hr_input: "Check the details you entered. A title, note or reason may be too long or empty.",
  invalid_hr_query: "That filter is not available. Clear the filters and try again.",
  hr_request_too_large: "That is too much text to save in one go. Shorten it and try again.",
  hr_database_unavailable: "We could not reach the HR records. Wait a minute and try again.",
  // Nothing was written: two changes met, and one was turned back.
  hr_retry_required: "Another change was in progress. Try again.",
};

/** A refusal with no sentence of its own: the server answered, so nothing was saved. */
export const PROCESS_ERROR_FALLBACK = "We could not save that. Check the details and try again.";
/** A read that failed for any other reason. */
export const PROCESS_LOAD_ERROR = "We couldn't load this. Try again.";

/**
 * The sentence for a refusal `code` (`ApiError.code`): this table, then the console-wide sentences
 * (`csrf_required`, `package_inactive`, ...), then the generic one. Never the code itself.
 */
export function processErrorText(code: string): string {
  if (Object.hasOwn(SENTENCES, code)) return SENTENCES[code];
  return explainedMessage(code) ?? PROCESS_ERROR_FALLBACK;
}

/** What the writes name in "Choose <button> again". */
export type ProcessAction =
  | "start" | "cancel" | "close" | "save" | "deactivate"
  | "complete" | "check" | "complete-for" | "approve" | "send-back" | "skip" | "reschedule" | "reassign" | "note";
const BUTTON: Record<ProcessAction, string> = {
  start: "Start process",
  cancel: "Cancel process",
  close: "Close anyway",
  save: "Save",
  deactivate: "Deactivate",
  complete: "Mark done",
  // A `complete` from an automatic-check step, and from HR on someone's behalf.
  check: "Check now",
  "complete-for": "Mark done for them",
  approve: "Approve",
  "send-back": "Send back",
  skip: "Skip",
  reschedule: "Reschedule",
  reassign: "Reassign",
  note: "Save note",
};

/** An `ApiError`, or anything: a definite answer is a 4xx with a code. */
function refusal(failure: unknown): { status: number; code: string } | null {
  if (typeof failure !== "object" || failure === null) return null;
  const { status, code } = failure as { status?: unknown; code?: unknown };
  return typeof status === "number" && status >= 400 && status < 500 && typeof code === "string" ? { status, code } : null;
}

/**
 * The sentence for a failed write. A 4xx is a definite answer, so its code picks the sentence. A
 * network error or a 5xx may have landed: the sentence says choosing the same button again is safe
 * (the client keeps the Idempotency-Key for the same change), and never says to reload, which would
 * drop the key and let a second process or note through.
 */
export function processSaveError(failure: unknown, action: ProcessAction): string {
  const definite = refusal(failure);
  if (definite) return processErrorText(definite.code);
  return `We couldn't confirm that. Choose ${BUTTON[action]} again; it won't be done twice.`;
}

/** The sentence for a failed read: a 4xx with a sentence of its own says that, anything else is a plain retry. */
export function processLoadError(failure: unknown): string {
  const definite = refusal(failure);
  if (definite && (Object.hasOwn(SENTENCES, definite.code) || explainedMessage(definite.code) !== undefined))
    return processErrorText(definite.code);
  return PROCESS_LOAD_ERROR;
}
