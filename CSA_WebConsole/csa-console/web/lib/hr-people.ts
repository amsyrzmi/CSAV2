import type { HrContext, Training } from "./hr-api";
import type { HrRoute } from "./hr-paths";
import type {
  Department, EmploymentType, FieldChange, HrHome, Interview, OrgChart, OrgNode, PeopleQuery, Person, PersonStatus,
  ProfileSection, Region,
} from "./hr-people-api";
import type { Option } from "./hr-pickers";

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
  /** What the person form checks before it saves. */
  form: {
    name: "Enter the person's name.",
    jobTitle: "Enter a job title.",
    startDate: "Choose a start date.",
    newDepartment: "Type a name for the new department, or choose another department.",
  },
  profileUnavailable: "This person isn't available. They may have left, or you may not have access.",
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

/** What the People list's controls hold: "all" or blank means no filter; "none" is the No department option. */
export type PeopleFilterPatch = Partial<Record<keyof PeopleQuery, string>>;

const FILTER_KEYS = ["q", "department", "manager", "status"] as const;

/**
 * The People query after the controls change. Blank and "all" values are dropped. The cursor
 * is kept only when the patch asks for one and leaves every filter as it was: a changed filter
 * starts again from the first page.
 */
export function peopleFilters(previous: PeopleQuery, patch: PeopleFilterPatch): PeopleQuery {
  const next: Record<string, string> = {};
  let changed = false;
  for (const key of FILTER_KEYS) {
    const raw = Object.hasOwn(patch, key) ? patch[key] : previous[key];
    const value = (raw ?? "").trim();
    if (value && value !== "all") next[key] = value;
    if ((next[key] ?? "") !== (previous[key] ?? "")) changed = true;
  }
  if (!changed && patch.cursor) next.cursor = patch.cursor;
  return next as PeopleQuery;
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

const SECTION_LABELS: Record<ProfileSection, string> = { overview: "Overview", access: "Access", history: "History" };

/** The profile's tabs, in the order the server lists the sections. Sections this release does not show are dropped. */
export function profileTabs(sections: readonly string[]): { value: ProfileSection; label: string }[] {
  return sections
    .filter((section): section is ProfileSection => Object.hasOwn(SECTION_LABELS, section))
    .map((section) => ({ value: section, label: SECTION_LABELS[section] }));
}

/**
 * The person form's starting values: every field from the person, or an empty form for Add person.
 * `person.update` replaces every field, so an edit must start from all of them.
 */
export function personFormValues(person?: Person | null): PersonFormValues {
  const text = (value: string | null | undefined) => value ?? "";
  return {
    display_name: text(person?.display_name),
    job_title: text(person?.job_title),
    start_date: text(person?.start_date),
    manager_id: person?.manager_id ?? NO_MANAGER,
    department_id: text(person?.department_id),
    new_department: "",
    work_email: text(person?.work_email),
    work_phone: text(person?.work_phone),
    employment_type: text(person?.employment_type),
    region: text(person?.employment_profile?.region),
    legal_entity_reference: text(person?.employment_profile?.legal_entity_reference),
    employment_category: text(person?.employment_profile?.employment_category),
  };
}

/**
 * The sentence that stops a save before it is sent, else null. A blank "New department…" name
 * must be stopped here: the body would carry no department and the person would silently lose it.
 */
export function personFormIssue(mode: "add" | "update", form: PersonFormValues): string | null {
  if (!form.display_name.trim()) return PEOPLE_COPY.form.name;
  if (mode === "add" && !form.job_title.trim()) return PEOPLE_COPY.form.jobTitle;
  if (mode === "add" && !form.start_date.trim()) return PEOPLE_COPY.form.startDate;
  if (form.department_id === NEW_DEPARTMENT && !form.new_department.trim()) return PEOPLE_COPY.form.newDepartment;
  return employmentProfileIssue(form);
}

/**
 * The manager picker's people. Never the person themself. A viewer who is not the HR owner is not
 * offered as anyone's manager (the server refuses it), unless they already are this person's
 * manager. The current manager is always offered, marked "(left)" when they are no longer in the
 * list, so an unchanged manager is sent back unchanged. `locked`: the viewer is not the HR owner and
 * this is their own record, so they may not change their own manager.
 */
export function managerChoices(input: {
  options: readonly Option[];
  person?: Pick<Person, "employee_id" | "manager_id" | "manager_name"> | null;
  viewer: { owner: boolean; employeeId: string | null };
}): { options: Option[]; locked: boolean } {
  const { person, viewer } = input;
  const current = person?.manager_id ?? null;
  const hidden = (id: string) => id === person?.employee_id
    || (!viewer.owner && viewer.employeeId !== null && id === viewer.employeeId && id !== current);
  const options = input.options.filter(([id]) => !hidden(id));
  if (current && current !== person?.employee_id && !options.some(([id]) => id === current))
    options.push([current, `${person?.manager_name ?? "Current manager"} (left)`]);
  const locked = !viewer.owner && viewer.employeeId !== null && person?.employee_id === viewer.employeeId;
  return { options, locked };
}

/**
 * The department picker: active departments, plus the person's current department even when it is
 * archived ("(archived)") or missing from the list, so an unchanged department is never cleared.
 */
export function departmentChoices(
  departments: readonly Department[], person?: Pick<Person, "department_id" | "department"> | null,
): Option[] {
  const current = person?.department_id ?? null;
  const options: Option[] = departments
    .filter((item) => !item.archived || item.department_id === current)
    .map((item) => [item.department_id, item.archived ? `${item.name} (archived)` : item.name]);
  if (current && !options.some(([id]) => id === current)) options.push([current, person?.department ?? "Current department"]);
  return options;
}

const MISSING = "—";

/** The Overview's label and value pairs. The employment profile and the account flag only show when the server sent them. */
export function overviewRows(person: Person): [label: string, value: string][] {
  const text = (value: string | null) => value?.trim() ? value : MISSING;
  const rows: [string, string][] = [
    ["Name", person.display_name],
    ["Job title", text(person.job_title)],
    ["Department", text(person.department)],
    ["Manager", text(person.manager_name)],
    ["Work email", text(person.work_email)],
    ["Work phone", text(person.work_phone)],
    ["Start date", person.start_date ? dateText(person.start_date) : MISSING],
    ["Employment type", person.employment_type ? EMPLOYMENT_TYPES.find(([id]) => id === person.employment_type)?.[1] ?? MISSING : MISSING],
    ["Status", STATUS_LABELS[person.status]],
  ];
  const profile = person.employment_profile;
  if (profile) rows.push(
    ["Region", Object.hasOwn(REGION_LABELS, profile.region) ? REGION_LABELS[profile.region] : profile.region],
    ["Legal entity", profile.legal_entity_reference],
    ["Employment category", profile.employment_category],
  );
  if (person.account_linked !== null) rows.push(["Console account", person.account_linked ? "Linked" : "Not linked"]);
  return rows;
}

/** Home's quick actions, by what the viewer may do. Writing actions need a login that can write. */
export function homeQuickActions(
  context: Pick<HrContext, "client_owner" | "switches" | "actions">, readonly: boolean,
): { key: string; label: string; href: HrRoute }[] {
  const items: { key: string; label: string; href: HrRoute }[] = [];
  if (!readonly && (context.client_owner || context.switches.includes("manage_people")))
    items.push({ key: "add-person", label: "Add person", href: { tab: "people", view: "new" } });
  if (!readonly && context.actions.includes("training.draft"))
    items.push({ key: "request-training", label: "Request training", href: { tab: "training" } });
  if (context.client_owner) items.push({ key: "access", label: "Open the access page", href: { tab: "settings" } });
  return items;
}

const LEAVE_TYPES: [string, string][] = [
  ["annual", "Annual leave"], ["sick", "Sick leave"], ["unpaid", "Unpaid leave"], ["other", "Other leave"],
];
function leaveLabel(type: string): string {
  const known = LEAVE_TYPES.find(([id]) => id === type)?.[1];
  if (known) return known;
  const words = type.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
function leaveRank(type: string): number {
  const index = LEAVE_TYPES.findIndex(([id]) => id === type);
  return index === -1 ? LEAVE_TYPES.length : index;
}

/** My HR's leave balances: the viewer's own only, newest year first, then by leave type. */
export function myBalanceLines(
  balances: readonly { employee_id: string; year: number; leave_type: string; remaining: string | number; days: string | number }[],
  employeeId: string | null,
): { key: string; text: string }[] {
  if (!employeeId) return [];
  return balances
    .filter((item) => item.employee_id === employeeId)
    .sort((a, b) => b.year - a.year || leaveRank(a.leave_type) - leaveRank(b.leave_type) || a.leave_type.localeCompare(b.leave_type))
    .map((item) => ({
      key: `${item.year}:${item.leave_type}`,
      text: `${item.year} · ${leaveLabel(item.leave_type)}: ${item.remaining} of ${item.days} ${Number(item.days) === 1 ? "day" : "days"} left`,
    }));
}

const TRAINING_STATUS: Record<Training["status"], string> = {
  draft: "Draft", submitted: "Waiting for a decision", approved: "Approved", rejected: "Not approved", withdrawn: "Withdrawn",
};

/** My HR's training requests: the viewer's own only, with plain statuses. */
export function myTrainingLines(
  items: readonly Pick<Training, "request_id" | "employee" | "content" | "status">[], employeeId: string | null,
): { key: string; title: string; status: string }[] {
  if (!employeeId) return [];
  return items
    .filter((item) => item.employee.employee_id === employeeId)
    .map((item) => ({ key: item.request_id, title: item.content.title, status: TRAINING_STATUS[item.status] ?? item.status }));
}
