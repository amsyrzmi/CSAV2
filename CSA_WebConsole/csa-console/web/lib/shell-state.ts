import type { NavKey } from "./navigation";

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
  chat: "Bot",
  inbox: "Inbox",
  email: "Mail",
  business: "Briefcase",
  tenants: "Building2",
  members: "UsersRound",
  team: "UserRoundCog",
  password: "KeyRound",
  subscriptions: "CreditCard",
};
