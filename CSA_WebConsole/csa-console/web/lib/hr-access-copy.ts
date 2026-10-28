import type { AccessEvent, AccessWarningOut, PresetId, SwitchId } from "./hr-access-api";

/** Words for the "Who can do what" page. Copy follows docs/WRITING_GUIDE.md. */
export type SwitchGroup = "Everyday" | "Responsibilities" | "Private areas";
export const GROUPS: readonly SwitchGroup[] = ["Everyday", "Responsibilities", "Private areas"];

export const SWITCH_COPY: Record<SwitchId, { label: string; tag: string; explanation: string; group: SwitchGroup }> = {
  directory: {
    label: "See everyone's basic details",
    tag: "Directory",
    explanation: "Looks up any colleague's name, job title, team, work email and manager, like a company phone book.",
    group: "Everyday",
  },
  own_requests: {
    label: "Can request their own training, leave and attendance",
    tag: "Own requests",
    explanation: "Drafts and sends their own training requests. Asks for leave and attendance corrections.",
    group: "Everyday",
  },
  approves_team: {
    label: "Approves training, leave and attendance for their team",
    tag: "Approves",
    explanation: "Approves or turns down requests from the people who report to them.",
    group: "Responsibilities",
  },
  hr_team: {
    label: "HR team member",
    tag: "HR team",
    explanation: "Reads everyone's training, leave and attendance records. Prepares recruitment requests.",
    group: "Responsibilities",
  },
  payroll_prepare: {
    label: "Prepares payroll",
    tag: "Prepares payroll",
    explanation: "Prepares each payroll run and opens the private payroll records.",
    group: "Responsibilities",
  },
  payroll_review: {
    label: "Reviews payroll",
    tag: "Reviews payroll",
    explanation: "Checks and approves a payroll run that someone else prepared. Opens the private payroll records.",
    group: "Responsibilities",
  },
  recruitment: {
    label: "Works on recruitment",
    tag: "Recruitment",
    explanation: "Opens candidates' CVs and interview notes, and books interviews.",
    group: "Private areas",
  },
  attendance: {
    label: "Works on attendance",
    tag: "Attendance",
    explanation: "Imports attendance records and plans shifts.",
    group: "Private areas",
  },
};

export const PRESET_COPY: Record<PresetId, { label: string; description: string }> = {
  staff: { label: "Staff", description: "Basic details and their own requests." },
  manager: { label: "Manager", description: "Staff, plus approves for their team." },
  hr_team: { label: "HR team", description: "Staff, plus HR team member, recruitment and attendance." },
  payroll_preparer: { label: "Payroll preparer", description: "Staff, plus prepares payroll." },
  payroll_reviewer: { label: "Payroll reviewer", description: "Staff, plus reviews payroll." },
};

export const CUSTOM_COPY = {
  marker: "Custom access",
  title: "Custom access",
  intro: "The switches don't describe this access. It came from earlier settings, and saving here keeps it.",
  link: "Change custom access in Advanced",
};

export const PAGE_COPY = {
  title: "Who can do what",
  description: "Turn on what each member can do in People & HR. Changes wait in the bar below until you save them.",
  conflict: "Someone changed access since you opened this page. Reload to see the latest; your pending changes are kept.",
  tooMany: "You can change up to 50 people at a time. Save these changes first, then carry on.",
  empty: "No members match. Clear the search or the filters to see everyone.",
  choose: "Choose a member from the list to see what they can do.",
};

export const FEATURES_COPY = {
  title: "What the HR assistant can help with",
  description: "These switches only control what the HR assistant does for your company. They don't change what any member can do on this page.",
};

export const FEATURE_LABELS: Record<string, string> = {
  hr_directory: "Directory",
  hr_training: "Training",
  hr_recruitment: "Recruitment",
  hr_attendance: "Attendance and leave",
  hr_payroll: "Payroll",
  hr_assistant: "HR assistant chat",
};

/** A member who has since left has no name; the page still shows what they did. */
export function formerMember(name: string | null): string {
  return name ?? "Former member";
}

const employee = (w: AccessWarningOut) => w.employee_name ?? "An employee";
const member = (w: AccessWarningOut) => w.user_name ?? "A member";

export function warningText(w: AccessWarningOut): string {
  switch (w.kind) {
    case "no_approver":
      return w.manager_name
        ? `${employee(w)}'s manager, ${w.manager_name}, can't approve their training, leave or attendance. Turn on "${SWITCH_COPY.approves_team.label}" for ${w.manager_name}.`
        : `${employee(w)}'s manager can't approve their training, leave or attendance. Link the manager to a member in Advanced.`;
    case "no_manager":
      return `${employee(w)} has no manager set, so their training, leave and attendance come to you. Only you, as HR owner, can approve these.`;
    case "no_payroll_reviewer":
      return `Nobody reviews payroll. Turn on "${SWITCH_COPY.payroll_review.label}" for someone so payroll runs can be approved.`;
    case "manager_without_team":
      return `${member(w)} approves for their team, but nobody reports to them. Set them as someone's manager in Advanced, or turn this off.`;
    case "prepares_and_reviews":
      return `${member(w)} both prepares and reviews payroll. Ask someone else to review the runs they prepare.`;
  }
}

const REASONS: Record<string, string> = {
  not_a_member: "This person is no longer a member. Discard their change.",
  too_many_rows: "This person has too much custom access to save here. Change their access in Advanced.",
  changed_since_load: "Their access changed since you opened this page. Reload to see the latest.",
  partly_saved: "Only part of this change was saved. Check their switches, then save again.",
  access_changed: "Their access changed since this save started. Reload to see the latest.",
};

/** Why one person's change was not saved; the raw code stays off the screen. */
export function refusalText(reason: string | null): string {
  return (reason && REASONS[reason]) ?? "We could not save this change. Check their access in Advanced, then try again.";
}

export function changesSummary(changes: number, people: number): string {
  return `${changes} ${changes === 1 ? "change" : "changes"} for ${people} ${people === 1 ? "person" : "people"}`;
}

const AREAS: Record<string, string> = {
  recruitment: "the private recruitment area",
  attendance: "the private attendance area",
  payroll: "the private payroll records",
};

const quoted = (ids: SwitchId[]) => ids.map((id) => `"${SWITCH_COPY[id].label}"`).join(", ");

/** One line of the recent-changes panel, without the time. */
export function eventText(e: AccessEvent): string {
  const actor = formerMember(e.actor_name);
  const target = formerMember(e.target_name);
  if (e.area) {
    const area = AREAS[e.area] ?? `the private ${e.area} area`;
    return `${actor} ${e.area_enabled ? "opened" : "closed"} ${area} for ${target}`;
  }
  const parts = [
    ...(e.turned_on.length ? [`turned on ${quoted(e.turned_on)}`] : []),
    ...(e.turned_off.length ? [`turned off ${quoted(e.turned_off)}`] : []),
  ];
  return parts.length
    ? `${actor} ${parts.join(" and ")} for ${target}`
    : `${actor} changed custom access for ${target}`;
}
