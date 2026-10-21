"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import {
  Building2,
  LayoutDashboard,
  LogOut,
  Users,
  KeyRound,
  Bot,
  Activity,
  BookOpen,
  MessageSquare,
} from "lucide-react";
import { identity } from "@/lib/identity-api";
import type { Me } from "@/lib/identity-contracts";
import { roleLabels } from "@/lib/identity-contracts";
import { Notice } from "@/components/console/ui";

export function AppShell({
  me,
  children,
}: {
  me: Me;
  children: React.ReactNode;
}) {
  const path = usePathname();
  const router = useRouter();
  const admin = me.role === "platform_admin";
  const home = admin ? "/" : "/portal";
  const links = [
    { href: home, label: "Overview", icon: LayoutDashboard },
    { href: admin ? "/agents" : "/portal/agents", label: "Agents", icon: Bot },
    { href: admin ? "/hr" : "/portal/hr", label: "People & HR", icon: Users },
    { href: admin ? "/activity" : "/portal/activity", label: "Runtime activity", icon: Activity },
    { href: admin ? "/knowledge-base" : "/portal/faq", label: "Knowledge base", icon: BookOpen },
    { href: admin ? "/chat" : "/portal/chat", label: "Sandbox assistant", icon: MessageSquare },
    ...(me.role && me.role !== "read_only"
      ? [{ href: admin ? "/workspace" : "/portal/workspace", label: "Staff workspace", icon: MessageSquare }]
      : []),
    ...(me.role && me.role !== "read_only" ? [{ href: admin ? "/inbox" : "/portal/inbox", label: "Customer inbox", icon: MessageSquare }] : []),
    ...(me.role && me.role !== "read_only" ? [{ href: admin ? "/business" : "/portal/business", label: "Business management", icon: Building2 }] : []),
    ...(admin
      ? [
          { href: "/settings/clients", label: "Tenants", icon: Building2 },
          { href: "/settings/team", label: "Members", icon: Users },
        ]
      : me.role === "tenant_admin"
        ? [{ href: "/portal/team", label: "Team", icon: Users }]
        : []),
    { href: "/change-password", label: "Password", icon: KeyRound },
  ];
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function signOut() {
    setBusy(true);
    setError("");
    try {
      await identity.logout();
      router.replace("/login");
      router.refresh();
    } catch (cause) {
      setError((cause as Error).message);
      setBusy(false);
    }
  }
  return (
    <div className="min-h-screen lg:flex">
      <aside className="shell-sidebar flex flex-col lg:sticky lg:top-0 lg:h-screen lg:w-60 lg:shrink-0">
        <Link href={home} className="flex h-20 items-center gap-3 px-6">
          <Image
            src="/brand/fourstacks-mark.svg"
            alt=""
            width={32}
            height={32}
          />
          <span className="brand-wordmark text-lg font-semibold">
            fourstacks.
          </span>
        </Link>
        <nav
          aria-label="Main navigation"
          className="flex flex-wrap gap-1 p-3 lg:flex-col"
        >
          {links.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              aria-current={path === href ? "page" : undefined}
              className="shell-nav-link"
            >
              <Icon size={18} />
              {label}
            </Link>
          ))}
        </nav>
        <div className="mt-auto border-t border-ink-200 p-5">
          <p className="break-words text-sm font-semibold">
            {me.user.display_name || me.user.email}
          </p>
          <p className="mt-1 text-xs text-ink-500">
            {me.role ? roleLabels[me.role] : "No active membership"}
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={signOut}
            className="mt-4 flex min-h-11 items-center gap-2 text-sm text-ink-600 hover:text-brand-600"
          >
            <LogOut size={16} />
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <header className="border-b border-ink-200 bg-white px-5 py-5 sm:px-8">
          <p className="eyebrow">CSA Console</p>
          <p className="mt-1 text-sm text-ink-600">
            {admin ? "Platform administration" : "Tenant workspace"}
          </p>
        </header>
        <main className="mx-auto max-w-6xl p-5 sm:p-8">
          {error && (
            <div className="mb-5">
              <Notice error>{error}</Notice>
            </div>
          )}
          {children}
        </main>
      </div>
    </div>
  );
}
