import type { NavKey, NavLink } from "./navigation";

/** Rail width choice. The server reads the cookie so the first response already has it. */
export type RailState = "collapsed" | "expanded";

export const RAIL_COOKIE = "fs-rail";
const ONE_YEAR_SECONDS = 31536000;

/** Exact, case-sensitive match; anything else (a tampered cookie included) is collapsed. */
export function parseRail(raw: string | null | undefined): RailState {
  return raw === "expanded" ? "expanded" : "collapsed";
}

export function railCookie(state: RailState): string {
  return `${RAIL_COOKIE}=${parseRail(state)}; Path=/; Max-Age=${ONE_YEAR_SECONDS}; SameSite=Lax`;
}

/** Client only: remember the choice for the next server render. */
export function writeRail(state: RailState): void {
  document.cookie = railCookie(state);
}

/**
 * One lucide icon per navigation entry, by name; components/console/shell/icons.ts maps the
 * names to components. No two entries share an icon.
 */
export const NAV_ICONS: Record<NavKey, string> = {
  overview: "LayoutDashboard",
  services: "Layers",
  hr: "Users",
  activity: "Activity",
  knowledge_base: "BookOpen",
  inbox: "Inbox",
  email: "Mail",
  business: "Briefcase",
  tenants: "Building2",
  members: "UsersRound",
  team: "UserRoundCog",
  password: "KeyRound",
  subscriptions: "CreditCard",
  bookings: "CalendarDays",
  training_library: "GraduationCap",
  connections: "Plug",
};

/** Home entries are prefixes of every page under them, so they only match exactly. */
const HOME_HREFS = new Set(["/", "/portal"]);

/** The navigation entry for the current page: the exact one, else the longest parent entry. */
export function activeNav(links: NavLink[], path: string): NavLink | null {
  const exact = links.find((link) => link.href === path);
  if (exact) return exact;
  let best: NavLink | null = null;
  for (const link of links) {
    if (HOME_HREFS.has(link.href) || !path.startsWith(`${link.href}/`)) continue;
    if (!best || link.href.length > best.href.length) best = link;
  }
  return best;
}

/** Roving-focus target for a menu key press, or null when the key is not a menu movement. */
export function menuIndex(key: string, index: number, count: number): number | null {
  if (count <= 0) return null;
  if (key === "ArrowDown") return (index + 1) % count;
  if (key === "ArrowUp") return (index - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}
