import { api, ApiError } from "./api";

/** Mirrors of csa/hr/access_contracts.py and the switch ids in csa/hr/access_switches.py. */
export type SwitchId =
  | "directory"
  | "own_requests"
  | "approves_team"
  | "hr_team"
  | "recruitment"
  | "attendance"
  | "payroll_prepare"
  | "payroll_review";
export type PresetId = "staff" | "manager" | "hr_team" | "payroll_preparer" | "payroll_reviewer";
export type WarningKind =
  | "no_approver"
  | "no_manager"
  | "no_payroll_reviewer"
  | "manager_without_team"
  | "prepares_and_reviews";

export const SWITCH_IDS: readonly SwitchId[] = [
  "directory", "own_requests", "approves_team", "hr_team",
  "recruitment", "attendance", "payroll_prepare", "payroll_review",
];

export interface AccessPerson {
  user_id: string;
  display_name: string | null;
  employee_id: string | null;
  department: string | null;
  manager_id: string | null;
  team_size: number;
  is_owner: boolean;
  on: SwitchId[];
  included: Partial<Record<SwitchId, SwitchId>>;
  /** The rows no switch describes, in plain words. Every save keeps them. */
  custom: string[];
}
export interface AccessWarningOut {
  kind: WarningKind;
  user_id: string | null;
  employee_id: string | null;
  user_name: string | null;
  employee_name: string | null;
  manager_name: string | null;
}
export interface AccessPeoplePage {
  items: AccessPerson[];
  next_cursor: string | null;
  version: string;
  /** Whole client, whatever the filters. */
  counts: Partial<Record<SwitchId, number>>;
  warnings: AccessWarningOut[];
}
export interface AccessChange { user_id: string; on: SwitchId[] }
export interface AccessChanges {
  batch_id: string;
  version: string;
  dry_run: boolean;
  changes: AccessChange[];
}
export interface AccessPersonResult {
  user_id: string;
  outcome: "saved" | "unchanged" | "refused";
  reason: string | null;
}
export interface AccessChangeResult {
  results: AccessPersonResult[];
  version: string;
  warnings: AccessWarningOut[];
}
export interface AccessEvent {
  occurred_at: string;
  actor_user_id: string;
  actor_name: string | null;
  target_user_id: string;
  target_name: string | null;
  turned_on: SwitchId[];
  turned_off: SwitchId[];
  area: string | null;
  area_enabled: boolean | null;
}
export interface AccessEventPage { items: AccessEvent[]; next_cursor: string | null }
export interface AccessFilters {
  cursor?: string;
  q?: string;
  team?: string;
  has?: SwitchId | "";
  reports_to?: string;
}

/** The server takes at most this many people per save, and pages the list by the same size. */
export const PAGE_SIZE = 50;

/** The same `client_id` suffix `hrClient` sends, then any non-empty query values. */
function path(tenant: string, rest: string, params: Record<string, string | undefined> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
  const extra = query.size ? `&${query}` : "";
  return `/console/v1/hr/access${rest}?client_id=${encodeURIComponent(tenant)}${extra}`;
}

export function accessPeople(tenant: string, params: AccessFilters): Promise<AccessPeoplePage> {
  const { cursor, q, team, has, reports_to } = params;
  return api<AccessPeoplePage>(path(tenant, "/people", { cursor, q, team, has, reports_to }));
}

export function saveAccessChanges(tenant: string, body: AccessChanges): Promise<AccessChangeResult> {
  return api<AccessChangeResult>(path(tenant, "/changes"), { method: "POST", body: JSON.stringify(body) });
}

export function accessRecent(tenant: string, cursor?: string): Promise<AccessEventPage> {
  return api<AccessEventPage>(path(tenant, "/recent", { cursor }));
}

/** Someone changed access or a reporting line since the page loaded. */
export function isAccessChanged(cause: unknown): boolean {
  return cause instanceof ApiError && cause.status === 409 && cause.code === "access_changed";
}

/**
 * A switch whose rows sit inside another switch's rows. The server reports it on whenever the
 * larger switch is on (csa/hr/access_switches.py), so the page keeps the pair together.
 */
const INCLUDED_BY: Partial<Record<SwitchId, SwitchId>> = { directory: "hr_team" };

const PRESETS: Record<PresetId, SwitchId[]> = {
  staff: ["directory", "own_requests"],
  manager: ["directory", "own_requests", "approves_team"],
  hr_team: ["directory", "own_requests", "hr_team", "recruitment", "attendance"],
  payroll_preparer: ["directory", "own_requests", "payroll_prepare"],
  payroll_reviewer: ["directory", "own_requests", "payroll_review"],
};

/** The set in switch order, with every included switch added. */
export function withIncluded(on: Iterable<SwitchId>): SwitchId[] {
  const set = new Set(on);
  for (const [inner, outer] of Object.entries(INCLUDED_BY) as [SwitchId, SwitchId][])
    if (set.has(outer)) set.add(inner);
  return SWITCH_IDS.filter((id) => set.has(id));
}

/** The switch that includes `id` in this set, or null when `id` can be turned off on its own. */
export function includedWith(on: readonly SwitchId[], id: SwitchId): SwitchId | null {
  const outer = INCLUDED_BY[id];
  return outer && on.includes(outer) ? outer : null;
}

/** Presets only turn switches on. */
export function applyPreset(on: readonly SwitchId[], preset: PresetId): SwitchId[] {
  return withIncluded([...on, ...PRESETS[preset]]);
}

export function setSwitch(on: readonly SwitchId[], id: SwitchId, value: boolean): SwitchId[] {
  if (!value && includedWith(on, id)) return withIncluded(on);
  return withIncluded(value ? [...on, id] : on.filter((item) => item !== id));
}

/** How many switches differ between the saved set and the desired set. */
export function changeCount(saved: readonly SwitchId[], desired: readonly SwitchId[]): number {
  const before = new Set(saved);
  const after = new Set(desired);
  return SWITCH_IDS.filter((id) => before.has(id) !== after.has(id)).length;
}
