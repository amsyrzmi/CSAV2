import { EMPLOYMENT_TYPES, REGION_LABELS } from "./hr-people";
import type { EmploymentType, Person, Region } from "./hr-people-api";
import type {
  Anchor, ApprovalBy, AutoCheck, OwnerRole, ProcessKind, TemplateCreate, TemplateOut, TemplatePreview, TemplateRules,
  TemplateStep, TemplateSummary, TemplateUpdate, TemplateWarning,
} from "./hr-process-api";

/**
 * The template editor's draft and its pure rules: the limits the backend checks (csa/hr/process_models.py),
 * the dates each kind counts from, the request bodies, who a template applies to in words, and the tie rule
 * of 0636 `hr_template_ties` (the template list has no conflicts field, so the list works them out).
 */

export const TEMPLATE_LIMITS = {
  name: 120, title: 160, description: 2000, reference: 500, offset: 365, steps: 40, entity: 200,
} as const;

/** Form text as typed: selects hold "" for "Any" or "None", the offset is the number box's text. */
export interface RuleDraft {
  department_id: string;
  employment_type: EmploymentType | "";
  legal_entity_reference: string;
  region: Region | "";
}
export interface StepDraft {
  /** Stable across moves: the row's React key and the base of its element ids. */
  key: number;
  title_en: string;
  title_ms: string;
  description_en: string;
  description_ms: string;
  reference_note: string;
  owner_role: OwnerRole;
  owner_employee_id: string;
  anchor: Anchor;
  due_offset_days: string;
  required: boolean;
  approval_by: ApprovalBy | "";
  auto_check: AutoCheck | "";
}
export interface TemplateDraft { kind: ProcessKind; name: string; rules: RuleDraft; steps: StepDraft[] }
export type StepField =
  | "title_en" | "title_ms" | "description_en" | "description_ms" | "reference_note" | "owner_employee_id"
  | "due_offset_days" | "anchor";
export type StepProblems = Partial<Record<StepField, string>>;
export interface DraftProblems { name?: string; legal_entity?: string; count?: string; steps: StepProblems[] }

export const KIND_CHOICES: [ProcessKind, string][] = [
  ["onboarding", "Onboarding"], ["offboarding", "Offboarding"], ["custom", "Custom process"],
];
export const OWNER_CHOICES: [OwnerRole, string][] = [
  ["hr", "HR team"], ["manager", "The person's manager"], ["employee", "The person"], ["person", "A named employee"],
];
export const ANCHOR_LABELS: Record<Anchor, string> = {
  start_date: "Start date", last_day: "Last day", probation_end: "Probation end",
};
export const APPROVAL_CHOICES: [ApprovalBy | "", string][] = [
  ["", "No approval"], ["hr", "HR team"], ["manager", "The person's manager"],
];
/** `access_removed` is done by the system alone: never offered, but kept on a step that has it. */
export const CHECK_CHOICES: [AutoCheck | "", string][] = [
  ["", "None"],
  ["account_linked", "Console account linked"],
  ["personal_details_complete", "Personal details filled in"],
  ["document_uploaded:contract", "Contract uploaded"],
  ["document_uploaded:identity", "Identity document uploaded"],
  ["document_uploaded:certificate", "Certificate uploaded"],
  ["document_uploaded:other", "Other document uploaded"],
];
export const SYSTEM_CHECK_LABEL = "Access removed (done by the system)";
export { EMPLOYMENT_TYPES, REGION_LABELS };

const SENTENCE = {
  name: "Give the template a name of at most 120 characters.",
  title_en: "Each step needs an English title of at most 160 characters.",
  title_ms: "A Malay title is at most 160 characters.",
  description: "A description is at most 2000 characters.",
  reference: "A reference note is at most 500 characters.",
  offset: "Give a whole number of days from -365 to 365.",
  anchor: "Onboarding steps count from the start date or the probation end, offboarding steps from the last day.",
  person: "Choose an active person of this company for the step.",
  entity: "A legal entity is at most 200 characters.",
  count: "A template has at most 40 steps.",
} as const;
export const TOO_MANY_STEPS = SENTENCE.count;

const ANCHORS: Record<ProcessKind, Anchor[]> = {
  onboarding: ["start_date", "probation_end"],
  offboarding: ["last_day"],
  custom: ["start_date", "last_day", "probation_end"],
};
/** The dates a step of `kind` may count from (0631's anchor rule); the first is a new step's. */
export function anchorsFor(kind: ProcessKind): Anchor[] {
  return ANCHORS[kind];
}

let lastKey = 0;
const nextKey = () => { lastKey += 1; return lastKey; };

export function emptyStep(kind: ProcessKind): StepDraft {
  return {
    key: nextKey(), title_en: "", title_ms: "", description_en: "", description_ms: "", reference_note: "",
    owner_role: "hr", owner_employee_id: "", anchor: anchorsFor(kind)[0], due_offset_days: "0", required: true,
    approval_by: "", auto_check: "",
  };
}
const NO_RULES: RuleDraft = { department_id: "", employment_type: "", legal_entity_reference: "", region: "" };
export function emptyDraft(): TemplateDraft {
  return { kind: "onboarding", name: "", rules: { ...NO_RULES }, steps: [emptyStep("onboarding")] };
}

/** A new template's kind: a step counting from a date the kind does not have moves to the kind's first date. */
export function withKind(draft: TemplateDraft, kind: ProcessKind): TemplateDraft {
  const allowed = anchorsFor(kind);
  return {
    ...draft, kind,
    steps: draft.steps.map(step => allowed.includes(step.anchor) ? step : { ...step, anchor: allowed[0] }),
  };
}

export function draftFromTemplate(template: TemplateOut): TemplateDraft {
  const { rules } = template;
  return {
    kind: template.kind,
    name: template.name,
    rules: {
      department_id: rules.department_id ?? "",
      employment_type: rules.employment_type ?? "",
      legal_entity_reference: rules.legal_entity_reference ?? "",
      region: rules.region ?? "",
    },
    steps: [...template.steps].sort((a, b) => a.ordinal - b.ordinal).map(step => ({
      key: nextKey(),
      title_en: step.title_en,
      title_ms: step.title_ms ?? "",
      description_en: step.description_en ?? "",
      description_ms: step.description_ms ?? "",
      reference_note: step.reference_note ?? "",
      owner_role: step.owner_role,
      owner_employee_id: step.owner_employee_id ?? "",
      anchor: step.anchor,
      due_offset_days: String(step.due_offset_days),
      required: step.required,
      approval_by: step.approval_by ?? "",
      auto_check: step.auto_check ?? "",
    })),
  };
}

/** "Copy as new": the same rules and steps (new row keys) under "Copy of <name>". */
export function copyDraft(draft: TemplateDraft): TemplateDraft {
  return { ...draft, name: `Copy of ${draft.name.trim()}`, rules: { ...draft.rules },
    steps: draft.steps.map(step => ({ ...step, key: nextKey() })) };
}

/** Characters as the server counts them (code points, not UTF-16 units). */
export function textLength(text: string): number {
  return [...text].length;
}
const optional = (text: string) => {
  const trimmed = text.trim();
  return trimmed === "" ? null : trimmed;
};
/** A description keeps its line breaks (CRLF becomes LF, as the server stores it). */
const multiline = (text: string) => optional(text.replaceAll("\r\n", "\n"));

/** Every rule, trimmed (null for "Any"), or null for a template with no rule: the company default. */
export function rulesBody(rules: RuleDraft): TemplateRules | null {
  const body: TemplateRules = {
    department_id: optional(rules.department_id),
    employment_type: rules.employment_type || null,
    legal_entity_reference: optional(rules.legal_entity_reference),
    region: rules.region || null,
  };
  return Object.values(body).every(value => value === null) ? null : body;
}

/** One step to save; its ordinal is its position in `steps`. Check `stepProblems` first. */
export function stepBody(step: StepDraft): TemplateStep {
  return {
    title_en: step.title_en.trim(),
    title_ms: optional(step.title_ms),
    description_en: multiline(step.description_en),
    description_ms: multiline(step.description_ms),
    reference_note: optional(step.reference_note),
    owner_role: step.owner_role,
    owner_employee_id: step.owner_role === "person" ? optional(step.owner_employee_id) : null,
    anchor: step.anchor,
    due_offset_days: Number(step.due_offset_days.trim()),
    required: step.required,
    approval_by: step.approval_by || null,
    auto_check: step.auto_check || null,
  };
}

export function createBody(draft: TemplateDraft): TemplateCreate {
  return { kind: draft.kind, name: draft.name.trim(), rules: rulesBody(draft.rules), steps: draft.steps.map(stepBody) };
}
/** `rules` is always present (null clears them); `is_active` only to turn the template on again. */
export function updateBody(draft: TemplateDraft, expectedVersion: number, activate = false): TemplateUpdate {
  const body: TemplateUpdate = {
    expected_version: expectedVersion, name: draft.name.trim(), rules: rulesBody(draft.rules),
    steps: draft.steps.map(stepBody),
  };
  return activate ? { ...body, is_active: true } : body;
}

/** Same name, rules and steps once trimmed, as the server would store them. */
export function sameDraft(a: TemplateDraft, b: TemplateDraft): boolean {
  return JSON.stringify(createBody(a)) === JSON.stringify(createBody(b));
}

const OFFSET = /^-?\d{1,3}$/;
export function stepProblems(step: StepDraft, kind: ProcessKind): StepProblems {
  const problems: StepProblems = {};
  const title = step.title_en.trim();
  if (title === "" || textLength(title) > TEMPLATE_LIMITS.title) problems.title_en = SENTENCE.title_en;
  if (textLength(step.title_ms.trim()) > TEMPLATE_LIMITS.title) problems.title_ms = SENTENCE.title_ms;
  if (textLength(multiline(step.description_en) ?? "") > TEMPLATE_LIMITS.description) problems.description_en = SENTENCE.description;
  if (textLength(multiline(step.description_ms) ?? "") > TEMPLATE_LIMITS.description) problems.description_ms = SENTENCE.description;
  if (textLength(step.reference_note.trim()) > TEMPLATE_LIMITS.reference) problems.reference_note = SENTENCE.reference;
  const offset = step.due_offset_days.trim();
  if (!OFFSET.test(offset) || Math.abs(Number(offset)) > TEMPLATE_LIMITS.offset) problems.due_offset_days = SENTENCE.offset;
  if (!anchorsFor(kind).includes(step.anchor)) problems.anchor = SENTENCE.anchor;
  if (step.owner_role === "person" && step.owner_employee_id.trim() === "") problems.owner_employee_id = SENTENCE.person;
  return problems;
}

export function draftProblems(draft: TemplateDraft): DraftProblems {
  const problems: DraftProblems = { steps: draft.steps.map(step => stepProblems(step, draft.kind)) };
  const name = draft.name.trim();
  if (name === "" || textLength(name) > TEMPLATE_LIMITS.name) problems.name = SENTENCE.name;
  if (textLength(draft.rules.legal_entity_reference.trim()) > TEMPLATE_LIMITS.entity) problems.legal_entity = SENTENCE.entity;
  if (draft.steps.length > TEMPLATE_LIMITS.steps) problems.count = SENTENCE.count;
  return problems;
}
export function hasProblems(problems: DraftProblems): boolean {
  return Boolean(problems.name || problems.legal_entity || problems.count)
    || problems.steps.some(step => Object.keys(step).length > 0);
}

const TYPE_LABELS = Object.fromEntries(EMPLOYMENT_TYPES) as Record<EmploymentType, string>;
/**
 * Who a template applies to: "Everyone" with no rule, else each rule in words. A department the
 * page cannot name (archived and gone from the list) is never shown by its id.
 */
export function rulesText(rules: TemplateRules, departmentName: (departmentId: string) => string | null): string {
  const parts: string[] = [];
  if (rules.department_id) parts.push(`Department: ${departmentName(rules.department_id) ?? "one no longer listed"}`);
  if (rules.employment_type && Object.hasOwn(TYPE_LABELS, rules.employment_type))
    parts.push(`Type: ${TYPE_LABELS[rules.employment_type]}`);
  if (rules.legal_entity_reference?.trim()) parts.push(`Legal entity: ${rules.legal_entity_reference.trim()}`);
  if (rules.region && Object.hasOwn(REGION_LABELS, rules.region)) parts.push(`Region: ${REGION_LABELS[rules.region]}`);
  return parts.length === 0 ? "Everyone" : parts.join(" · ");
}

type TieRules = [department: string | null, type: string | null, entity: string | null, region: string | null];
function tieRules(rules: TemplateRules): TieRules {
  const text = (value: string | null | undefined) => value?.trim() || null;
  return [text(rules.department_id), text(rules.employment_type), text(rules.legal_entity_reference)?.toLowerCase() ?? null,
    text(rules.region)];
}
/**
 * The other active templates each active onboarding or offboarding template may tie with (0636
 * `hr_template_ties`): same kind, as many set rules, and no rule set on both with different values
 * (the legal entity ignores case). Custom templates are picked by hand, so they never tie.
 */
export function templateTies(items: readonly Pick<TemplateSummary, "template_id" | "kind" | "is_active" | "rules">[]): Map<string, string[]> {
  const ties = new Map<string, string[]>();
  const candidates = items.filter(item => item.is_active && item.kind !== "custom");
  for (const item of candidates) {
    const mine = tieRules(item.rules);
    const count = mine.filter(value => value !== null).length;
    const found = candidates.filter(other => {
      if (other.template_id === item.template_id || other.kind !== item.kind) return false;
      const theirs = tieRules(other.rules);
      return theirs.filter(value => value !== null).length === count
        && mine.every((value, index) => value === null || theirs[index] === null || value === theirs[index]);
    }).map(other => other.template_id);
    if (found.length > 0) ties.set(item.template_id, found);
  }
  return ties;
}

export function previewText(preview: TemplatePreview): string {
  if (preview.matching === 0) return "No one matches yet.";
  return `This applies to ${preview.matching} ${preview.matching === 1 ? "person" : "people"} now.`;
}

/** A save warning about one step; a code a later release adds still names the step. */
export function warningText(warning: TemplateWarning, titleAt: (ordinal: number) => string | null): string {
  const title = titleAt(warning.ordinal);
  const step = `Step ${warning.ordinal}${title ? `, ${title},` : ""}`;
  if (warning.code === "step_after_last_day")
    return `${step} is due after the last day. It will be due on the last day instead.`;
  return `Check step ${warning.ordinal} before you use this template.`;
}

/** `items` with the one at `from` moved to `to`; a move off either end changes nothing. */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length || from === to) return [...items];
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** A step row's accessible name: "Step 3: Collect the badge". */
export function stepName(index: number, title: string): string {
  return `Step ${index + 1}: ${title.trim() || "Untitled step"}`;
}

/** The legal entities already on people's records, for the rule's suggestions: distinct, ignoring case. */
export function legalEntities(people: readonly Pick<Person, "employment_profile">[]): string[] {
  const seen = new Map<string, string>();
  for (const person of people) {
    const entity = person.employment_profile?.legal_entity_reference?.trim();
    if (entity && !seen.has(entity.toLowerCase())) seen.set(entity.toLowerCase(), entity);
  }
  return [...seen.values()].sort((a, b) => a.localeCompare(b));
}
