import { api, ApiError } from "./api";

/** Mirrors of csa/hr/access_contracts.py and the switch ids in csa/hr/access_switches.py. */
export type SwitchId =
  | "directory"
  | "own_requests"
  | "approves_team"
  | "hr_team"
  | "manage_people"
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
  "directory", "own_requests", "approves_team", "hr_team", "manage_people",
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
/** `on` is the desired set; `base` is the person's `on` when the page loaded them. */
export interface AccessChange { user_id: string; on: SwitchId[]; base: SwitchId[] }
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
  /** "invitation" when the access was set on an invitation and applied when it was accepted. */
  source: "page" | "invitation";
  preset: PresetId | null;
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
/** An accepted invitation whose HR access has not been applied or dismissed yet. */
export interface PendingInvitationAccess {
  token_id: string;
  user_id: string;
  display_name: string;
  preset: PresetId;
  chosen_by: string;
  /** "FourStacks admin" for a platform admin; null once the chooser is no longer a member. */
  chooser_name: string | null;
  accepted_at: string;
}
export interface PendingInvitationPage { items: PendingInvitationAccess[] }
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

export function pendingInvitations(tenant: string): Promise<PendingInvitationPage> {
  return api<PendingInvitationPage>(path(tenant, "/invitations"));
}

export function applyInvitation(tenant: string, tokenId: string): Promise<AccessPersonResult> {
  return api<AccessPersonResult>(path(tenant, `/invitations/${encodeURIComponent(tokenId)}/apply`), { method: "POST" });
}

export function dismissInvitation(tenant: string, tokenId: string): Promise<void> {
  return api<void>(path(tenant, `/invitations/${encodeURIComponent(tokenId)}/dismiss`), { method: "POST" });
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
  hr_team: ["directory", "own_requests", "hr_team", "manage_people", "recruitment", "attendance"],
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

/**
 * Turns one switch on or off. `base` is the person's saved set: turning an outer switch off puts
 * the switches it included back to their saved value, so nothing is left pending by accident.
 */
export function setSwitch(on: readonly SwitchId[], id: SwitchId, value: boolean, base: readonly SwitchId[]): SwitchId[] {
  if (!value && includedWith(on, id)) return withIncluded(on);
  if (value) return withIncluded([...on, id]);
  const inner = (Object.entries(INCLUDED_BY) as [SwitchId, SwitchId][])
    .filter(([item, outer]) => outer === id && !base.includes(item)).map(([item]) => item);
  return withIncluded(on.filter((item) => item !== id && !inner.includes(item)));
}

/** Pending changes per member: the set the page loaded them with, and the set the owner wants. */
export interface PendingEntry { base: SwitchId[]; desired: SwitchId[]; person: AccessPerson }
export type Pending = Record<string, PendingEntry>;

/**
 * Moves pending changes onto freshly loaded people. Each refreshed member keeps only the owner's
 * own edits (what they turned on or off against the old base), applied over the fresh switches; a
 * member left with nothing to change leaves the bar. Members not refreshed keep their old base, and
 * the server refuses their save if their switches moved since.
 */
export function rebasePending(pending: Pending, fresh: readonly AccessPerson[]): Pending {
  const next: Pending = { ...pending };
  for (const person of fresh) {
    const entry = pending[person.user_id];
    if (!entry) continue;
    const added = entry.desired.filter((id) => !entry.base.includes(id));
    const removed = entry.base.filter((id) => !entry.desired.includes(id));
    const desired = withIncluded([...person.on, ...added].filter((id) => !removed.includes(id)));
    if (changeCount(person.on, desired) === 0) delete next[person.user_id];
    else next[person.user_id] = { base: [...person.on], desired, person };
  }
  return next;
}

/** How many switches differ between the saved set and the desired set. */
export function changeCount(saved: readonly SwitchId[], desired: readonly SwitchId[]): number {
  const before = new Set(saved);
  const after = new Set(desired);
  return SWITCH_IDS.filter((id) => before.has(id) !== after.has(id)).length;
}
