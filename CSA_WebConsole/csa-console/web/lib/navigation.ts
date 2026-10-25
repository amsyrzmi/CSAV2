import type { Me } from "./identity-contracts";
import type { PackageState } from "./services-api";

export type NavKey =
  | "overview" | "services" | "hr" | "activity" | "knowledge_base" | "chat"
  | "workspace" | "inbox" | "email" | "business" | "tenants" | "members"
  | "team" | "password";
export interface NavLink { href: string; label: string; key: NavKey }

// Tab -> packages that unlock it (any one of them is enough).
const gate: Partial<Record<NavKey, string[]>> = {
  inbox: ["customer_support"],
  knowledge_base: ["customer_support"],
  business: ["orders_products", "marketplaces"],
  hr: ["hr"],
  email: ["staff_email"],
};

export function navigationFor(me: Me, packages: PackageState[]): NavLink[] {
  const admin = me.role === "platform_admin";
  const writer = Boolean(me.role) && me.role !== "read_only";
  const active = new Set(packages.filter((p) => p.status === "active").map((p) => p.key));
  const to = (path: string) => (admin ? `/${path}` : `/portal/${path}`);
  const entries: Array<NavLink & { show?: boolean }> = [
    { key: "overview", label: "Overview", href: admin ? "/" : "/portal" },
    { key: "services", label: "Services", href: to("services") },
    { key: "hr", label: "People & HR", href: to("hr") },
    { key: "activity", label: "Runtime activity", href: to("activity") },
    { key: "knowledge_base", label: "Knowledge base", href: admin ? "/knowledge-base" : "/portal/faq" },
    { key: "chat", label: "Sandbox assistant", href: to("chat") },
    { key: "workspace", label: "Staff workspace", href: to("workspace"), show: writer },
    { key: "inbox", label: "Customer inbox", href: to("inbox"), show: writer },
    { key: "email", label: "Email assistant", href: to("email"), show: writer },
    { key: "business", label: "Business management", href: to("business"), show: writer },
    { key: "tenants", label: "Tenants", href: "/settings/clients", show: admin },
    { key: "members", label: "Members", href: "/settings/team", show: admin },
    { key: "team", label: "Team", href: "/portal/team", show: me.role === "tenant_admin" },
    { key: "password", label: "Password", href: "/change-password" },
  ];
  return entries
    .filter((e) => {
      if (e.show === false) return false;
      const needs = gate[e.key];
      return admin || !needs || needs.some((k) => active.has(k));
    })
    .map(({ href, label, key }) => ({ href, label, key }));
}
