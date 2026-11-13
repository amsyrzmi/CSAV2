import type { Me } from "./identity-contracts";
import type { PackageState } from "./services-api";

export type NavKey =
  | "overview" | "services" | "hr" | "activity" | "knowledge_base"
  | "inbox" | "email" | "business" | "tenants" | "members"
  | "team" | "password" | "subscriptions" | "bookings" | "training_library" | "connections"
  | "credit" | "billing";
/** A sub-page of a rail group; the testId names it for tests and the section bar. */
export interface NavChild { key: string; label: string; href: string; testId?: string }
export interface NavLink { href: string; label: string; key: NavKey; children?: NavChild[] }

export type BusinessSection = "orders" | "products" | "stores";
export type BookingsSection = "calendar" | "types" | "people";
export type KnowledgeSection = "library" | "add" | "questions" | "maintenance";

/**
 * The Business management sub-pages in order; Orders keeps the group's old URL. Child literals
 * are written href-first so the key-then-label regex in tests/test_workspace_contracts.py
 * reads only the top-level entries.
 */
export function businessPages(admin: boolean): NavChild[] {
  const base = admin ? "/business" : "/portal/business";
  return [
    // href-first: the contract regex must not read a child as a tab.
    { href: base, key: "orders", label: "Orders", testId: "business-subnav-orders" },
    // href-first: the contract regex must not read a child as a tab.
    { href: `${base}/products`, key: "products", label: "Products & stock", testId: "business-subnav-products" },
    // href-first: the contract regex must not read a child as a tab.
    { href: `${base}/stores`, key: "stores", label: "Store connections", testId: "business-subnav-stores" },
  ];
}

/** The Business knowledge sub-pages in order; "What it knows" keeps the group's old URL. */
export function knowledgePages(admin: boolean): NavChild[] {
  const base = admin ? "/knowledge-base" : "/portal/faq";
  return [
    // href-first: the contract regex must not read a child as a tab.
    { href: base, key: "library", label: "What it knows", testId: "kb-subnav-library" },
    // href-first: the contract regex must not read a child as a tab.
    { href: `${base}/add`, key: "add", label: "Add knowledge", testId: "kb-subnav-add" },
    // href-first: the contract regex must not read a child as a tab.
    { href: `${base}/questions`, key: "questions", label: "Unanswered questions", testId: "kb-subnav-questions" },
    // href-first: the contract regex must not read a child as a tab.
    { href: `${base}/maintenance`, key: "maintenance", label: "Maintenance and settings", testId: "kb-subnav-maintenance" },
  ];
}

/** The Bookings sub-pages in order; Calendar keeps the group's own URL. */
export function bookingsPages(admin: boolean): NavChild[] {
  const base = admin ? "/bookings" : "/portal/bookings";
  return [
    // href-first: the contract regex must not read a child as a tab.
    { href: base, key: "calendar", label: "Calendar", testId: "bookings-subnav-calendar" },
    // href-first: the contract regex must not read a child as a tab.
    { href: `${base}/types`, key: "types", label: "Booking types", testId: "bookings-subnav-types" },
    // href-first: the contract regex must not read a child as a tab.
    { href: `${base}/people-and-rooms`, key: "people", label: "People & rooms", testId: "bookings-subnav-people" },
  ];
}

const pathOf = (href: string) => href.split("?")[0];

/**
 * The sub-page the path is on: an exact match wins, else the deepest child whose page the path
 * lies under. The child that is the parent's own page matches exactly only, so the parent's
 * other pages never read as "Orders".
 */
export function currentChild(children: NavChild[], path: string, parentHref: string): NavChild | null {
  const exact = children.find((child) => pathOf(child.href) === path);
  if (exact) return exact;
  let best: NavChild | null = null;
  for (const child of children) {
    const href = pathOf(child.href);
    if (href === pathOf(parentHref) || !path.startsWith(`${href}/`)) continue;
    if (!best || href.length > pathOf(best.href).length) best = child;
  }
  return best;
}

/**
 * What a group's parent link announces. While the group is shown its child carries the page
 * mark, so the parent has none; hidden, the parent is current on its own page and otherwise
 * "true" (somewhere inside the group). Never "page" for a sub-page, so a nav has one such link.
 */
export function groupParentCurrent(link: NavLink, path: string, open: boolean): "page" | "true" | undefined {
  if (open) return undefined;
  if (path === link.href) return "page";
  return currentChild(link.children ?? [], path, link.href) ? "true" : undefined;
}

/** Home entries are prefixes of every page under them, so they only match exactly. */
const HOME_PATHS = new Set(["/", "/portal"]);

/**
 * The top-level entry a path belongs to: the one whose href or a child href is the path, else
 * the one with the longest href the path lies under (never a home entry).
 */
export function entryForPath(links: NavLink[], path: string): NavLink | null {
  const exact = links.find(
    (link) => link.href === path || link.children?.some((child) => pathOf(child.href) === path),
  );
  if (exact) return exact;
  let best: NavLink | null = null;
  for (const link of links) {
    if (HOME_PATHS.has(link.href) || !path.startsWith(`${link.href}/`)) continue;
    if (!best || link.href.length > best.href.length) best = link;
  }
  return best;
}

// Tab -> packages that unlock it (any one of them is enough).
const gate: Partial<Record<NavKey, string[]>> = {
  inbox: ["customer_support"],
  activity: ["customer_support"],
  knowledge_base: ["knowledge_base"],
  business: ["orders_products", "marketplaces"],
  hr: ["hr"],
  email: ["staff_email"],
  subscriptions: ["subscriptions"],
  bookings: ["bookings"],
  training_library: ["training_library"],
};
// Statuses that unlock a tab; mirrors the server gates. The Inbox stays reachable while
// Customer Support is suspended so staff can resolve held replies and escalations; Assistant
// activity follows it, since an employee-only company has no customer conversations to show.
const unlocks: Partial<Record<NavKey, PackageState["status"][]>> = {
  inbox: ["active", "suspended"],
  activity: ["active", "suspended"],
};
const ACTIVE_ONLY: PackageState["status"][] = ["active"];

export function navigationFor(me: Me, packages: PackageState[]): NavLink[] {
  const admin = me.role === "platform_admin";
  const writer = Boolean(me.role) && me.role !== "read_only";
  const status = new Map(packages.map((p) => [p.key, p.status] as const));
  const unlocked = (key: NavKey, pkg: string) => {
    const current = status.get(pkg);
    return current !== undefined && (unlocks[key] ?? ACTIVE_ONLY).includes(current);
  };
  const to = (path: string) => (admin ? `/${path}` : `/portal/${path}`);
  const entries: Array<NavLink & { show?: boolean }> = [
    // Overview is the assistant home: the sandbox chat lives there (/chat redirects to it).
    { key: "overview", label: "Overview", href: admin ? "/" : "/portal" },
    { key: "services", label: "Services", href: to("services") },
    { key: "hr", label: "People & HR", href: to("hr") },
    { key: "activity", label: "Assistant activity", href: to("activity") },
    { key: "knowledge_base", label: "Business knowledge", href: admin ? "/knowledge-base" : "/portal/faq", children: knowledgePages(admin) },
    { key: "inbox", label: "Customer inbox", href: to("inbox"), show: writer },
    { key: "email", label: "Email assistant", href: to("email"), show: writer },
    { key: "business", label: "Business management", href: to("business"), show: writer, children: businessPages(admin) },
    // Read-only members see the tab too; the page hides its action controls for them.
    { key: "subscriptions", label: "Subscriptions", href: to("subscriptions") },
    // Every member sees Bookings, read-only included; the setup pages hide their edit controls.
    { key: "bookings", label: "Bookings", href: to("bookings"), children: bookingsPages(admin) },
    // Every member reads the library, read-only included; only managers see its edit controls.
    // "Training library", not "Training": HR's own Training tab keeps that name.
    { key: "training_library", label: "Training library", href: to("training") },
    // Every member sees Connections, read-only included; each card gates itself by package and role.
    { key: "connections", label: "Connections", href: to("connections") },
    { key: "tenants", label: "Tenants", href: "/settings/clients", show: admin },
    { key: "members", label: "Members", href: "/settings/team", show: admin },
    // Every company's usage, raw cost and margin, and the billing plan, price and FX forms.
    { key: "billing", label: "Billing", href: "/billing", show: admin },
    { key: "team", label: "Team", href: "/portal/team", show: me.role === "tenant_admin" },
    // The company's own usage and bill, for its Admin only and with no package. The platform
    // team reads every company's billing on its own page.
    { key: "credit", label: "Credit", href: "/portal/credit", show: me.role === "tenant_admin" },
    { key: "password", label: "Password", href: to("password") },
  ];
  return entries
    .filter((e) => {
      if (e.show === false) return false;
      const needs = gate[e.key];
      return admin || !needs || needs.some((k) => unlocked(e.key, k));
    })
    .map(({ href, label, key, children }) => (children ? { href, label, key, children } : { href, label, key }));
}
