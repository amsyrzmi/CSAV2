/** Pure rules behind the Overview page (no React, no I/O). */
import type { NavLink } from "./navigation";
import type { PackageState } from "./services-api";

/**
 * Overview is the assistant page for everyone who has the assistant (`assistant`: the shared
 * conversation is there), for every kind of company; everyone else gets the small home. The
 * packages no longer choose the body: they decide only the replies banner (showsRepliesBanner).
 */
export function overviewBody(packages: PackageState[] | null, assistant: boolean): "assistant" | "home" {
  void packages;
  return assistant ? "assistant" : "home";
}

/**
 * The customer-replies banner belongs to a company with Customer support (active, or suspended so
 * staff still hear about held replies). `null` means the packages read failed: the banner stays,
 * so a failed read never removes anything (it is silent unless customers aren't being answered).
 */
export function showsRepliesBanner(packages: PackageState[] | null): boolean {
  if (packages === null) return true;
  const support = packages.find((item) => item.key === "customer_support")?.status;
  return support === "active" || support === "suspended";
}

export interface HomeLink { key: string; label: string; href: string }

/** The pages the small home links to, in this order, when the company has them. */
const HOME_KEYS = ["hr", "email", "bookings", "services"];

/**
 * Links to the rail's own pages for what the company has: `rail` is what `navigationFor` gives a
 * member of the company, so its package and role rules decide. For a platform admin the member's
 * `/portal` pages become the admin pages, and the links name the chosen company with `client_id`.
 */
export function homeLinks(rail: NavLink[], admin: boolean, tenant: string | null): HomeLink[] {
  const links: HomeLink[] = [];
  for (const key of HOME_KEYS) {
    const entry = rail.find((link) => link.key === key);
    if (!entry) continue;
    const href = admin ? entry.href.replace(/^\/portal/, "") : entry.href;
    links.push({
      key,
      label: entry.label,
      href: admin && tenant ? `${href}?client_id=${encodeURIComponent(tenant)}` : href,
    });
  }
  return links;
}
