import type { HrRoute } from "./hr-paths";
import type {
  EmploymentType, FieldChange, HrHome, Interview, OrgChart, OrgNode, Person, PersonStatus, Region,
} from "./hr-people-api";

/** Pure helpers for the People pages: form bodies, the org tree, Home items and plain words. */

export const EMPLOYMENT_TYPES: [EmploymentType, string][] = [
  ["full_time", "Full-time"], ["part_time", "Part-time"], ["contract", "Contract"], ["intern", "Intern"],
];
export const STATUS_LABELS: Record<PersonStatus, string> = { active: "Active", starting: "Starting", left: "Left" };
/** The manager select's value for "no manager". */
export const NO_MANAGER = "";
/** The department select's value for a department typed into the form. */
export const NEW_DEPARTMENT = "__new__";

const REGION_LABELS: Record<Region, string> = {
  peninsular_labuan: "Peninsular Malaysia / Labuan", sabah: "Sabah", sarawak: "Sarawak",
};

export const PEOPLE_COPY = {
  stale: "Someone changed this person since you opened it. Reload to see their change.",
  noManager: "No manager (top of company)",
  newDepartment: "New department…",
  /** Plain sentences for the 409 detail codes of the people operations. */
  refusals: {
    employee_exists: "This person was already added. Reload to see them.",
    person_changed: "Someone changed this person since you opened it. Reload to see their change.",
    manager_unavailable: "That manager is no longer available. Choose another manager.",
    manager_loop: "That manager already reports to this person. Choose a different manager.",
    manager_is_you: "You can't make yourself someone's manager. Ask the HR owner.",
    own_manager_owner_only: "Only the HR owner can change your own manager.",
    department_unavailable: "That department is no longer available. Choose another department.",
    department_archived: "That department is archived. Choose another department.",
    department_changed: "Someone changed this department since you opened it. Reload to see their change.",
    department_name_taken: "A department with that name already exists. Choose a different name.",
    account_taken: "That console account is already linked to another person. Unlink it there first.",
    member_unavailable: "That member is no longer on your team. Choose another member.",
    employment_profile_must_be_complete: "Fill in all three employment details, or leave all three empty.",
    // A 422 from the route's input check: the server names no field, so the sentence points at the likely ones.
    invalid_hr_input: "Check the details you entered. Fill in all three employment details, or leave all three empty.",
  } as Record<string, string>,
  refusalFallback: "We could not save that. Reload the page and try again.",
  empty: {
    people: {
      edit: "No one has been added yet. Choose Add person to add the first one.",
      view: "No one has been added yet. Ask the HR owner to add people.",
    },
    departments: {
      edit: "No departments yet. Choose Add department to create the first one.",
      view: "No departments yet. Ask the HR owner to add them.",
    },
    org: {
      edit: "The org chart shows up once people are added. Choose Add person to start.",
      view: "The org chart shows up once the HR owner adds people.",
    },
    home: "Nothing needs you today.",
    unlinked: "Your login isn't linked to an employee record yet. Ask your HR owner to link it from your profile's Access section.",
  },
};

/** The sentence for a 409 detail code from a people operation. Unknown codes get the fallback. */
export function peopleRefusalText(code: string): string {
  return Object.hasOwn(PEOPLE_COPY.refusals, code) ? PEOPLE_COPY.refusals[code] : PEOPLE_COPY.refusalFallback;
}

export function emptyText(kind: "people" | "departments" | "org" | "home" | "unlinked", canEdit = false): string {
  if (kind === "home" || kind === "unlinked") return PEOPLE_COPY.empty[kind];
  return PEOPLE_COPY.empty[kind][canEdit ? "edit" : "view"];
}

/** The raw form fields, all strings. `manager_id` is `NO_MANAGER` or an id; `department_id` is "", an id or `NEW_DEPARTMENT`. */
export interface PersonFormValues {
  display_name: string;
  job_title: string;
  start_date: string;
  manager_id: string;
  department_id: string;
  new_department: string;
  work_email: string;
  work_phone: string;
  employment_type: string;
  region: string;
  legal_entity_reference: string;
  employment_category: string;
}

export interface PersonOperationBody {
  operation: "person.add" | "person.update";
  employee_id: string;
  expected_version?: number;
  values: Record<string, string | null>;
  employment_profile?: { region: string; legal_entity_reference: string; employment_category: string };
}

const orNull = (value: string) => value.trim() || null;

const PROFILE_FIELDS = ["region", "legal_entity_reference", "employment_category"] as const;

/**
 * The sentence to show before saving when only some of the three employment details are filled,
 * else null. The server refuses a partial profile too (a 422 `invalid_hr_input`, no code that names
 * the cause), so the form should check this first.
 */
export function employmentProfileIssue(form: PersonFormValues): string | null {
  const filled = PROFILE_FIELDS.filter((field) => form[field].trim() !== "").length;
  return filled === 0 || filled === PROFILE_FIELDS.length ? null : PEOPLE_COPY.refusals.employment_profile_must_be_complete;
}

/**
 * The `person.add` / `person.update` body for the HR operations route.
 *
 * `person.update` replaces every field in `values` and the employment profile, so callers must
 * prefill the form with the person's current values. The profile is sent whenever any of its three
 * fields has text, so the server validates it and refuses a partial one. It is left out only when
 * all three are blank, and the server then clears the stored profile: that is a deliberate clear.
 */
export function personBody(
  mode: "add" | "update", form: PersonFormValues, ids: { employeeId: string; version?: number },
): PersonOperationBody {
  const values: Record<string, string | null> = {
    display_name: form.display_name.trim(),
    job_title: orNull(form.job_title),
    start_date: orNull(form.start_date),
    manager_id: orNull(form.manager_id),
  };
  if (form.department_id === NEW_DEPARTMENT) values.new_department = orNull(form.new_department);
  else values.department_id = orNull(form.department_id);
  Object.assign(values, {
    work_email: orNull(form.work_email),
    work_phone: orNull(form.work_phone),
    employment_type: orNull(form.employment_type),
  });
  const profile = {
    region: form.region.trim(),
    legal_entity_reference: form.legal_entity_reference.trim(),
    employment_category: form.employment_category.trim(),
  };
  const any = profile.region || profile.legal_entity_reference || profile.employment_category;
  const common = { employee_id: ids.employeeId, values, ...(any ? { employment_profile: profile } : {}) };
  if (mode === "add") return { operation: "person.add", ...common };
  if (ids.version === undefined) throw new Error("A person update needs the record version");
  return { operation: "person.update", expected_version: ids.version, ...common };
}

/** `version` is null for a new department; `archived` is always the state to save. */
export function departmentBody(input: { departmentId: string; name: string; archived: boolean; version: number | null }) {
  return {
    operation: "department.save" as const,
    department_id: input.departmentId,
    expected_version: input.version,
    name: input.name.trim(),
    archived: input.archived,
  };
}

export interface OrgTreeNode extends OrgNode { children: OrgTreeNode[] }

const byName = (a: OrgNode, b: OrgNode) => a.display_name.localeCompare(b.display_name);

/**
 * People nested under their manager, siblings by name. Roots are people with no manager, a manager
 * outside the chart, or a place in a reporting loop; nobody in a loop is nested under the others.
 */
export function orgTree(chart: OrgChart): OrgTreeNode[] {
  const ids = new Set(chart.nodes.map((item) => item.employee_id));
  const looped = new Set(chart.looped);
  const reports = new Map<string, OrgNode[]>();
  const roots: OrgNode[] = [];
  for (const item of chart.nodes) {
    const manager = looped.has(item.employee_id) ? null : item.manager_id;
    if (manager && manager !== item.employee_id && ids.has(manager)) reports.set(manager, [...(reports.get(manager) ?? []), item]);
    else roots.push(item);
  }
  const placed = new Set<string>();
  const build = (item: OrgNode): OrgTreeNode => {
    placed.add(item.employee_id);
    const below = (reports.get(item.employee_id) ?? []).filter((child) => !placed.has(child.employee_id)).sort(byName);
    return { ...item, children: below.map((child) => (placed.has(child.employee_id) ? null : build(child))).filter((child) => child !== null) };
  };
  const tree = roots.sort(byName).map(build);
  // A loop the server did not report must not make people vanish from the chart.
  for (const item of [...chart.nodes].sort(byName)) if (!placed.has(item.employee_id)) tree.push(build(item));
  return tree;
}

/**
 * Whether Home triggers `automation.run` every five minutes. Only a writing HR owner does: Home is
 * everyone's landing page, the run takes a tenant-wide lock, and the background worker
 * (scripts/run_hr_maintenance) does the maintenance anyway.
 */
export function runsHomeMaintenance(context: { client_owner: boolean }, readonly: boolean): boolean {
  return context.client_owner && !readonly;
}

export interface HomeItem {
  kind: "training" | "requests" | "interview" | "starting" | "leaving" | "notifications";
  key: string;
  label: string;
  href: HrRoute;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const KUALA_LUMPUR_HOURS = 8;
const DAY = 86_400_000;

/** `YYYY-MM-DD` of an instant on the Kuala Lumpur calendar. */
function klDate(instant: Date): string {
  return new Date(instant.getTime() + KUALA_LUMPUR_HOURS * 3_600_000).toISOString().slice(0, 10);
}
function addDays(date: string, days: number): string {
  return new Date(Date.parse(`${date}T00:00:00Z`) + days * DAY).toISOString().slice(0, 10);
}
function dateText(date: string): string {
  const [year, month, day] = date.split("-");
  return `${Number(day)} ${MONTHS[Number(month) - 1]} ${year}`;
}
function plural(count: number, one: string): string {
  return `${count} ${one}${count === 1 ? "" : "s"}`;
}

/**
 * What needs this person today: waiting work and unread notices, interviews from today to 7 days
 * ahead, and people starting from today to 14 days ahead (all by Kuala Lumpur date). `leaving` is
 * the seam for project 1b and is empty until then.
 */
export function homeItems(input: {
  home: HrHome; interviews: Interview[]; unread: number; starting: Person[]; leaving?: Person[]; now: Date;
}): HomeItem[] {
  const today = klDate(input.now);
  const items: HomeItem[] = [];
  if (input.home.training_waiting > 0)
    items.push({ kind: "training", key: "training", href: { tab: "training" },
      label: `${plural(input.home.training_waiting, "training request")} waiting for your decision` });
  if (input.home.requests_waiting > 0)
    items.push({ kind: "requests", key: "requests", href: { tab: "requests" },
      label: `${plural(input.home.requests_waiting, "HR request")} waiting for your decision` });
  const interviewEnd = addDays(today, 7);
  for (const item of [...input.interviews].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at))) {
    const day = klDate(new Date(item.starts_at));
    if (item.status === "cancelled" || day < today || day > interviewEnd) continue;
    const clock = new Date(Date.parse(item.starts_at) + KUALA_LUMPUR_HOURS * 3_600_000).toISOString().slice(11, 16);
    items.push({ kind: "interview", key: `interview-${item.id}`, href: { tab: "recruitment" },
      label: `Interview on ${dateText(day)}, ${clock}` });
  }
  const startEnd = addDays(today, 14);
  const starters = input.starting
    .filter((item) => item.status !== "left" && item.start_date !== null && item.start_date >= today && item.start_date <= startEnd)
    .sort((a, b) => (a.start_date ?? "").localeCompare(b.start_date ?? ""));
  for (const item of starters)
    items.push({ kind: "starting", key: `starting-${item.employee_id}`,
      href: { tab: "people", view: "profile", employeeId: item.employee_id },
      label: `${item.display_name} starts on ${dateText(item.start_date ?? today)}` });
  for (const item of input.leaving ?? [])
    items.push({ kind: "leaving", key: `leaving-${item.employee_id}`,
      href: { tab: "people", view: "profile", employeeId: item.employee_id }, label: `${item.display_name} is leaving` });
  if (input.unread > 0)
    items.push({ kind: "notifications", key: "notifications", href: { tab: "home" },
      label: `${plural(input.unread, "unread notification")}` });
  return items;
}

const FIELD_LABELS: Record<FieldChange["field"], string> = {
  display_name: "Name", job_title: "Job title", department: "Department", manager: "Manager",
  work_email: "Work email", work_phone: "Work phone", start_date: "Start date", employment_type: "Employment type",
  active: "Status", region: "Region", legal_entity_reference: "Legal entity", employment_category: "Employment category",
};

function valueText(field: FieldChange["field"], value: string): string {
  if (field === "employment_type") return EMPLOYMENT_TYPES.find(([id]) => id === value)?.[1] ?? value;
  if (field === "region") return Object.hasOwn(REGION_LABELS, value) ? REGION_LABELS[value as Region] : value;
  if (field === "active") return value === "true" ? STATUS_LABELS.active : value === "false" ? STATUS_LABELS.left : value;
  if (field === "start_date" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return dateText(value);
  return value;
}

/** One plain line for one changed field in a person's history. */
export function historyText(change: FieldChange): string {
  const label = FIELD_LABELS[change.field];
  const before = change.old === null ? null : `"${valueText(change.field, change.old)}"`;
  const after = change.new === null ? null : `"${valueText(change.field, change.new)}"`;
  if (before && after) return `${label} changed from ${before} to ${after}`;
  if (after) return `${label} set to ${after}`;
  if (before) return `${label} cleared (was ${before})`;
  return `${label} changed`;
}
