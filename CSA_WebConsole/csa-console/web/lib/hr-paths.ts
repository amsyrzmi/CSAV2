import type { HrContext } from "./hr-api";

/** The People & HR tabs, the pages under them, and who gets which tab. Pure: no fetches. */
export type HrTab = "home" | "people" | "recruitment" | "leave" | "payroll" | "training" | "requests" | "settings";
export type PeopleView = "list" | "org-chart" | "departments" | "new";
export type HrRoute =
  | { tab: "people"; view: PeopleView }
  | { tab: "people"; view: "profile"; employeeId: string }
  | { tab: Exclude<HrTab, "people"> };

/** Onboarding has no tab and no route until project 3. */
export const HR_TABS: readonly { tab: HrTab; label: string; segment: string | null }[] = [
  { tab: "home", label: "Home", segment: null },
  { tab: "people", label: "People", segment: "people" },
  { tab: "recruitment", label: "Recruitment", segment: "recruitment" },
  { tab: "leave", label: "Leave & attendance", segment: "leave" },
  { tab: "payroll", label: "Payroll", segment: "payroll" },
  { tab: "training", label: "Training", segment: "training" },
  { tab: "requests", label: "Requests", segment: "requests" },
  { tab: "settings", label: "Settings", segment: "settings" },
];

const PEOPLE_VIEWS: ReadonlySet<string> = new Set(["org-chart", "departments", "new"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function tabForSegment(segment: string): HrTab | null {
  const match = HR_TABS.find((item) => item.segment !== null && item.segment === segment);
  return match ? match.tab : null;
}

/** The optional catch-all segments after `/hr` or `/portal/hr`. Anything the pages do not serve is null. */
export function parseHrPath(path: string[] | undefined): HrRoute | null {
  if (!path || path.length === 0) return { tab: "home" };
  const tab = tabForSegment(path[0]);
  if (tab === null) return null;
  if (tab !== "people") return path.length === 1 ? { tab } : null;
  if (path.length === 1) return { tab: "people", view: "list" };
  if (path.length > 2) return null;
  if (PEOPLE_VIEWS.has(path[1])) return { tab: "people", view: path[1] as PeopleView };
  const employeeId = path[1].toLowerCase();
  return UUID.test(employeeId) ? { tab: "people", view: "profile", employeeId } : null;
}

function segments(route: HrRoute): string[] {
  if (route.tab === "home") return [];
  if (route.tab !== "people") return [route.tab];
  if (route.view === "list") return ["people"];
  return ["people", route.view === "profile" ? route.employeeId : route.view];
}

/**
 * `/portal/hr/…` for staff. A platform admin works under `/hr/…` and keeps the tenant in
 * `client_id`, so moving between tabs never drops it.
 */
export function hrHref(route: HrRoute, platform: boolean, tenant: string | null): string {
  const path = `${platform ? "/hr" : "/portal/hr"}${segments(route).map((part) => `/${part}`).join("")}`;
  if (!platform || !tenant) return path;
  return `${path}?${new URLSearchParams({ client_id: tenant })}`;
}

const LEGACY: ReadonlyMap<string, HrRoute> = new Map<string, HrRoute>([
  ["directory", { tab: "people", view: "list" }],
  ["people", { tab: "people", view: "list" }],
  ["training", { tab: "training" }],
  ["workflows", { tab: "requests" }],
  ["requests", { tab: "requests" }],
  ["attendance", { tab: "leave" }],
  ["leave", { tab: "leave" }],
  ["recruitment", { tab: "recruitment" }],
  ["payroll", { tab: "payroll" }],
  ["settings", { tab: "settings" }],
]);

/** Where an old `?section=` deep link (the HR guidance links, saved bookmarks) now lives. */
export function legacyHrRoute(section: string | null): HrRoute | null {
  const route = section === null ? undefined : LEGACY.get(section);
  return route ? { ...route } : null;
}

/** Exactly the paths `parseHrPath` accepts, under `/hr` or `/portal/hr`. */
export function isHrPagePath(pathname: string): boolean {
  for (const base of ["/portal/hr", "/hr"]) {
    if (pathname === base) return true;
    if (pathname.startsWith(`${base}/`)) return parseHrPath(pathname.slice(base.length + 1).split("/")) !== null;
  }
  return false;
}

/** Tabs with nothing the viewer may do are hidden, not greyed. */
export function visibleTabs(ctx: HrContext): HrTab[] {
  const owner = ctx.client_owner;
  const area = (name: HrContext["private_areas"][number]) => ctx.private_areas.includes(name);
  const workflow = (...domains: string[]) => ctx.workflows.some((item) => domains.some((domain) => item.startsWith(`${domain}.`)));
  const show: Record<HrTab, boolean> = {
    home: true,
    people: owner || ctx.switches.includes("manage_people") || ctx.switches.includes("directory"),
    recruitment: area("recruitment") || workflow("recruitment"),
    leave: owner || area("attendance") || workflow("leave", "attendance"),
    payroll: area("payroll"),
    training: ctx.actions.some((item) => item.startsWith("training.")),
    requests: ctx.workflows.length > 0,
    settings: owner,
  };
  return HR_TABS.filter((item) => show[item.tab]).map((item) => item.tab);
}
