/** Pure rules behind the Overview page (no React, no I/O). */
import type { NavLink } from "./navigation";
import type { PackageState } from "./services-api";

/**
 * The sandbox chat is for companies with Customer support (active, or suspended so staff can still
 * try it). A company without it gets the small home. `null` means the packages read failed: the
 * chat stays, so a failed read never removes anything.
 */
export function overviewBody(packages: PackageState[] | null): "chat" | "home" {
  if (packages === null) return "chat";
  const support = packages.find((item) => item.key === "customer_support")?.status;
  return support === "active" || support === "suspended" ? "chat" : "home";
}

export interface HomeLink { key: string; label: string; href: string }

/** The pages the small home links to, in this order, when the company has them. */
const HOME_KEYS = ["hr", "email", "services"];

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
