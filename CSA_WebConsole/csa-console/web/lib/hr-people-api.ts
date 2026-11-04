import { api } from "./api";
import type { SwitchId } from "./hr-access-api";

/** Mirrors of csa/hr/people_contracts.py. HR-only fields are null for every other viewer. */
export type PersonStatus = "active" | "starting" | "leaving" | "left";
export type LeavingReason = "resigned" | "contract_ended" | "dismissed" | "retired" | "other";
export type EmploymentType = "full_time" | "part_time" | "contract" | "intern";
export type Region = "peninsular_labuan" | "sabah" | "sarawak";
export type ProfileSection = "overview" | "personal" | "documents" | "access" | "history";
export type HistoryField =
  | "display_name" | "job_title" | "department" | "manager" | "work_email" | "work_phone"
  | "start_date" | "employment_type" | "active" | "last_day" | "leaving_reason" | "region" | "legal_entity_reference"
  | "employment_category"
  // Personal details: the history says a field changed, never its value.
  | "date_of_birth" | "home_address" | "personal_phone" | "emergency_name" | "emergency_relationship"
  | "emergency_phone" | "bank_name" | "ic_or_passport" | "bank_account" | "tax_number" | "epf_number" | "socso_number"
  // Old and new read like "Contract: offer.pdf".
  | "document";

export interface EmploymentProfile {
  region: Region;
  legal_entity_reference: string;
  employment_category: string;
}
/** Why a person is leaving. HR viewers only (null for everyone else). */
export interface Leaving { reason: LeavingReason; note: string | null }
export interface Person {
  employee_id: string;
  display_name: string;
  job_title: string | null;
  department_id: string | null;
  department: string | null;
  manager_id: string | null;
  manager_name: string | null;
  work_email: string | null;
  work_phone: string | null;
  /** A calendar date, `YYYY-MM-DD`. */
  start_date: string | null;
  employment_type: EmploymentType | null;
  status: PersonStatus;
  /** The last working day, `YYYY-MM-DD`: HR viewers, the person and their direct manager. */
  last_day: string | null;
  leaving: Leaving | null;
  employment_profile: EmploymentProfile | null;
  version: number | null;
  account_linked: boolean | null;
}
export interface PeoplePage { items: Person[]; next_cursor: string | null; total: number }
export interface PersonProfile { person: Person; sections: ProfileSection[]; can_edit: boolean }
export interface FieldChange { field: HistoryField; old: string | null; new: string | null }
export interface PersonChange { changed_at: string; actor_name: string | null; fields: FieldChange[] }
export interface PersonHistory { items: PersonChange[]; truncated: boolean }
export interface LinkedAccount { user_id: string; display_name: string | null }
export interface PersonAccess {
  account: LinkedAccount | null;
  on: SwitchId[];
  custom: string[];
  is_owner: boolean;
}
export interface OrgNode {
  employee_id: string;
  display_name: string;
  job_title: string | null;
  department: string | null;
  manager_id: string | null;
}
export interface OrgChart { nodes: OrgNode[]; looped: string[] }
export interface Department {
  department_id: string;
  name: string;
  archived: boolean;
  version: number;
  people: number;
}
export interface DepartmentList { items: Department[] }
export interface HrHome { training_waiting: number; requests_waiting: number }
/** An interview the viewer runs, as `records("my_interviews")` returns it. */
export interface Interview { id: string; starts_at: string; ends_at: string; status: string }

/** The People list filters, in the order the query carries them. */
export interface PeopleQuery {
  q?: string;
  department?: string;
  manager?: string;
  status?: PersonStatus | "";
  cursor?: string;
}

/** `client_id` first (as `hrClient` sends it), then any non-empty filters in order. */
function path(tenant: string, rest: string, params: Record<string, string | undefined> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
  const extra = query.size ? `&${query}` : "";
  return `/console/v1/hr${rest}?client_id=${encodeURIComponent(tenant)}${extra}`;
}

/** Every call is a GET, so nothing here needs an idempotency key or a CSRF token. */
export function peopleClient(tenant: string) {
  const person = (id: string, rest = "") => path(tenant, `/people/${encodeURIComponent(id)}${rest}`);
  return {
    page: (query: PeopleQuery) => {
      const { q, department, manager, status, cursor } = query;
      return api<PeoplePage>(path(tenant, "/people", { q, department, manager, status, cursor }));
    },
    person: (id: string) => api<PersonProfile>(person(id)),
    history: (id: string) => api<PersonHistory>(person(id, "/history")),
    access: (id: string) => api<PersonAccess>(person(id, "/access")),
    orgChart: () => api<OrgChart>(path(tenant, "/org-chart")),
    departments: () => api<DepartmentList>(path(tenant, "/departments")),
    home: () => api<HrHome>(path(tenant, "/home")),
  };
}
