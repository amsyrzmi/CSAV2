import { api } from "./api";
import { hrWriter } from "./hr-api";
import type { EmploymentType, Region } from "./hr-people-api";

/**
 * Mirrors of csa/hr/process_models.py (migrations 0630-0636). The process routes answer with
 * `response_model_exclude_unset`, so a field another viewer may not see is absent: those fields are
 * optional here, and a visible empty one is `null`. Dates are `YYYY-MM-DD`, instants ISO strings.
 */
export type ProcessKind = "onboarding" | "offboarding" | "custom";
export type OwnerRole = "hr" | "manager" | "employee" | "person";
export type Anchor = "start_date" | "last_day" | "probation_end";
export type ApprovalBy = "hr" | "manager";
export type AutoCheck =
  | "account_linked" | "personal_details_complete" | "access_removed"
  | "document_uploaded:contract" | "document_uploaded:identity"
  | "document_uploaded:certificate" | "document_uploaded:other";
export type TaskStatus = "todo" | "awaiting_approval" | "done" | "rejected" | "skipped";
export type ProcessStatus = "active" | "completed" | "cancelled";
export type ProcessView = "hr" | "manager" | "self" | "assignee";
export type TaskAction = "complete" | "approve" | "send-back" | "skip" | "reschedule" | "reassign" | "note";

// ---- Templates ----

/** Who a template applies to; a rule left `null` (or out) matches everyone. */
export interface TemplateRules {
  department_id?: string | null;
  employment_type?: EmploymentType | null;
  legal_entity_reference?: string | null;
  region?: Region | null;
}
/** One step to save; its ordinal is its position in `steps`. */
export interface TemplateStep {
  title_en: string;
  title_ms?: string | null;
  description_en?: string | null;
  description_ms?: string | null;
  owner_role: OwnerRole;
  /** Only for a `person` step. */
  owner_employee_id?: string | null;
  anchor: Anchor;
  /** -365 to 365. */
  due_offset_days: number;
  required?: boolean;
  approval_by?: ApprovalBy | null;
  auto_check?: AutoCheck | null;
  reference_note?: string | null;
}
export interface TemplateCreate {
  kind: ProcessKind;
  name: string;
  rules?: TemplateRules | null;
  steps: TemplateStep[];
}
/** `rules` has no default on purpose: send `null` to make the template the company default. */
export interface TemplateUpdate {
  expected_version: number;
  name: string;
  rules: TemplateRules | null;
  steps: TemplateStep[];
  is_active?: boolean | null;
}
export interface TemplateWarning { code: string; ordinal: number }
export interface TemplateReceipt {
  operation: "template.create" | "template.update" | "template.deactivate";
  template_id: string;
  version: number;
  conflicts: string[];
  warnings: TemplateWarning[];
  status: "saved";
  duplicate: boolean;
}
export interface TemplateSummary {
  template_id: string;
  kind: ProcessKind;
  name: string;
  version: number;
  is_active: boolean;
  rules: TemplateRules;
  step_count: number;
  created_at: string;
  updated_at: string;
}
export interface TemplateList { items: TemplateSummary[] }
export interface TemplateStepOut {
  ordinal: number;
  title_en: string;
  title_ms: string | null;
  description_en: string | null;
  description_ms: string | null;
  owner_role: OwnerRole;
  owner_employee_id: string | null;
  anchor: Anchor;
  due_offset_days: number;
  required: boolean;
  approval_by: ApprovalBy | null;
  auto_check: AutoCheck | null;
  reference_note: string | null;
}
export interface TemplateOut {
  template_id: string;
  kind: ProcessKind;
  name: string;
  version: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  rules: TemplateRules;
  steps: TemplateStepOut[];
  conflicts: string[];
  warnings: TemplateWarning[];
}
export interface TemplatePreviewTie { template_id: string; name: string }
export interface TemplatePreview { matching: number; ties: TemplatePreviewTie[]; total_active: number }

// ---- Processes ----

export interface ProcessStart {
  employee_id: string;
  kind: ProcessKind;
  /** Required for a custom process; else the matching template is used. */
  template_id?: string | null;
  /** Cancel an active onboarding or offboarding of the person first. Without it, that is refused. */
  replace?: boolean;
}
export interface ProcessStartReceipt {
  operation: "process.start";
  process_id: string;
  employee_id: string;
  kind: ProcessKind;
  template_id: string | null;
  status: "saved";
  duplicate: boolean;
}
export interface ProcessCancelReceipt {
  operation: "process.cancel";
  process_id: string;
  status: "saved";
  duplicate: boolean;
}
export interface ProcessCloseReceipt {
  operation: "process.close_anyway";
  process_id: string;
  process_status: ProcessStatus;
  status: "saved";
  duplicate: boolean;
}
export interface ProcessRow {
  process_id: string;
  employee_id: string;
  employee_name: string;
  department_id: string | null;
  kind: ProcessKind;
  /** The template's name; null without one. */
  name: string | null;
  status: ProcessStatus;
  started_at: string;
  closed_at: string | null;
  tasks_total: number;
  /** Done or skipped. */
  tasks_done: number;
  next_due_on: string | null;
  overdue: boolean;
}
export interface ProcessPage { items: ProcessRow[]; next_cursor: string | null }
export interface ProcessFilters {
  kind?: ProcessKind | "";
  status?: ProcessStatus | "";
  /** `true` keeps processes with an open task due before today; `false` is no filter. */
  overdue?: boolean;
  department?: string;
  owner?: string;
  limit?: number;
}
export interface AnchorDates {
  start_date?: string | null;
  last_day?: string | null;
  probation_end?: string | null;
}
export interface ProcessHeader {
  process_id: string;
  kind: ProcessKind;
  name: string | null;
  status: ProcessStatus;
  employee_name: string;
  started_at?: string | null;
  closed_at?: string | null;
  employee_id?: string | null;
  tasks_total?: number | null;
  tasks_done?: number | null;
  required_total?: number | null;
  required_done?: number | null;
  open_tasks?: number | null;
  template_id?: string | null;
  template_version?: number | null;
  anchor_dates?: AnchorDates | null;
  started_by?: string | null;
}
export interface ProcessTask {
  task_id: string;
  ordinal: number;
  title_en: string;
  title_ms: string | null;
  owner_role: OwnerRole;
  assignee_name: string | null;
  due_on: string | null;
  status: TaskStatus;
  required: boolean;
  overdue: boolean;
  mine: boolean;
  // The caller's own tasks, and every task for HR.
  description_en?: string | null;
  description_ms?: string | null;
  anchor?: Anchor | null;
  approval_by?: ApprovalBy | null;
  auto_check?: AutoCheck | null;
  reference_note?: string | null;
  completed_at?: string | null;
  note?: string | null;
  version?: number | null;
  sent_back_reason?: string | null;
  done_by_hr?: boolean | null;
  // HR only.
  assignee_employee_id?: string | null;
  assignee_overridden?: boolean | null;
  due_offset_days?: number | null;
  due_overridden?: boolean | null;
  completed_by?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
}
/** Which keys appear depends on the action; unknown actions and keys pass through. */
export interface ProcessEvent {
  event_id: string;
  at: string;
  /** Null is the system. */
  actor: string | null;
  action: string;
  task_id: string | null;
  detail: Record<string, unknown>;
}
export interface ProcessDetail {
  view: ProcessView;
  process: ProcessHeader;
  tasks: ProcessTask[];
  /** HR only. */
  events?: ProcessEvent[] | null;
}
export interface PersonProcess {
  process_id: string;
  kind: ProcessKind;
  name: string | null;
  status: ProcessStatus;
  started_at: string;
  closed_at: string | null;
  tasks_total: number;
  tasks_done: number;
  next_task: { title_en: string; title_ms: string | null; due_on: string | null } | null;
}
export interface PersonProcesses {
  view: "hr" | "manager" | "self";
  employee_id: string;
  processes: PersonProcess[];
  /** HR and the person. */
  anchor_dates?: AnchorDates | null;
  /** HR only: true when the person has no process of that kind and no template matches them. */
  no_matching_template?: { onboarding: boolean; offboarding: boolean } | null;
}

// ---- Tasks ----

/** A row of My tasks: every field is present, but those another viewer may not see are `null`. */
export interface MyTask {
  task_id: string;
  process_id: string;
  process_kind: ProcessKind;
  process_name: string | null;
  /** The person the process is about. */
  employee_name: string;
  ordinal: number;
  title_en: string;
  title_ms: string | null;
  description_en: string | null;
  description_ms: string | null;
  owner_role: OwnerRole;
  due_on: string | null;
  required: boolean;
  approval_by: ApprovalBy | null;
  auto_check: AutoCheck | null;
  reference_note: string | null;
  status: TaskStatus;
  note: string | null;
  version: number;
  overdue: boolean;
  pool: boolean;
  needs_my_approval: boolean;
  sent_back_reason: string | null;
  /** The caller is the assignee. */
  mine: boolean;
  /** The HR team only. */
  assignee_name: string | null;
  done_by_hr: boolean | null;
}
export interface MyTaskPage { items: MyTask[]; next_cursor: string | null }
export interface TaskReceipt {
  operation: "task.complete" | "task.skip" | "task.approve" | "task.send_back" | "task.note" | "task.reschedule" | "task.reassign";
  task_id: string;
  process_id: string;
  task_status: TaskStatus;
  version: number;
  process_status: ProcessStatus;
  status: "saved";
  duplicate: boolean;
}
/** The body each task action takes. Every one but `note` needs the step's `version`. */
export interface TaskBodies {
  complete: { expected_version: number; note?: string | null };
  approve: { expected_version: number };
  "send-back": { expected_version: number; reason: string };
  skip: { expected_version: number; reason: string };
  reschedule: { expected_version: number; due_on: string };
  reassign: { expected_version: number; assignee_employee_id: string | null };
  note: { note: string | null };
}
const TASK_ACTIONS: ReadonlySet<string> = new Set<TaskAction>([
  "complete", "approve", "send-back", "skip", "reschedule", "reassign", "note",
]);

/** `client_id` first (as `hrClient` sends it), then any set, non-empty filters in order. */
function path(tenant: string, rest: string, params: Record<string, string | number | undefined> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") query.set(key, String(value));
  const extra = query.size ? `&${query}` : "";
  return `/console/v1/hr${rest}?client_id=${encodeURIComponent(tenant)}${extra}`;
}

/**
 * Templates, processes and tasks. Reads are GETs; every write carries CSRF and an Idempotency-Key
 * that stays with the exact operation until it succeeds. The template preview writes nothing, so
 * it needs CSRF but no key.
 */
export function processClient(tenant: string) {
  const { write } = hrWriter(tenant);
  const id = encodeURIComponent;
  return {
    templates: () => api<TemplateList>(path(tenant, "/templates")),
    template: (templateId: string) => api<TemplateOut>(path(tenant, `/templates/${id(templateId)}`)),
    createTemplate: (body: TemplateCreate) => write<TemplateReceipt>("/templates", body),
    updateTemplate: (templateId: string, body: TemplateUpdate) =>
      write<TemplateReceipt>(`/templates/${id(templateId)}`, body, "PUT"),
    deactivateTemplate: (templateId: string, expected_version: number) =>
      write<TemplateReceipt>(`/templates/${id(templateId)}/deactivate`, { expected_version }),
    previewTemplate: (kind: ProcessKind, rules: TemplateRules | null) =>
      api<TemplatePreview>(path(tenant, "/templates/preview"), { method: "POST", body: JSON.stringify({ kind, rules }) }),
    processes: (filters: ProcessFilters, after?: string) => {
      const { kind, status, overdue, department, owner, limit } = filters;
      return api<ProcessPage>(path(tenant, "/processes", {
        kind, status, overdue: overdue ? "true" : undefined, department, owner, limit, after,
      }));
    },
    process: (processId: string) => api<ProcessDetail>(path(tenant, `/processes/${id(processId)}`)),
    startProcess: (body: ProcessStart) => write<ProcessStartReceipt>("/processes", body),
    cancelProcess: (processId: string) => write<ProcessCancelReceipt>(`/processes/${id(processId)}/cancel`, undefined),
    closeProcess: (processId: string, note: string) =>
      write<ProcessCloseReceipt>(`/processes/${id(processId)}/close`, { note }),
    personProcesses: (employeeId: string) => api<PersonProcesses>(path(tenant, `/people/${id(employeeId)}/processes`)),
    myTasks: (status?: TaskStatus, after?: string) => api<MyTaskPage>(path(tenant, "/tasks/mine", { status, after })),
    taskAction: async <A extends TaskAction>(taskId: string, action: A, body: TaskBodies[A]) => {
      if (!TASK_ACTIONS.has(action)) throw new Error(`Unknown task action: ${action}`);
      return write<TaskReceipt>(`/tasks/${id(taskId)}/${action}`, body);
    },
  };
}
