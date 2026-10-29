"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { identity } from "@/lib/identity-api";
import type { Me } from "@/lib/identity-contracts";
import { roleLabels } from "@/lib/identity-contracts";
import { navigationFor } from "@/lib/navigation";
import { writeRail, type RailState } from "@/lib/shell-state";
import type { PackageState } from "@/lib/services-api";
import { AssistantDock } from "@/components/console/AssistantDock";
import { MobileMenu } from "@/components/console/shell/MobileMenu";
import { Rail } from "@/components/console/shell/Rail";
import { Notice } from "@/components/console/ui";
import { PageContextProvider } from "@/lib/page-context";

export function AppShell({
  me,
  packages,
  children,
  initialRail,
}: {
  me: Me;
  packages: PackageState[];
  children: React.ReactNode;
  initialRail?: RailState;
}) {
  const path = usePathname();
  const router = useRouter();
  const admin = me.role === "platform_admin";
  const home = admin ? "/" : "/portal";
  const links = navigationFor(me, packages);
  const [rail, setRail] = useState<RailState>(initialRail ?? "collapsed");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  function toggleRail() {
    const next: RailState = rail === "expanded" ? "collapsed" : "expanded";
    setRail(next);
    writeRail(next);
  }
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
  // Task 9 moves the name, role and Sign out into a top-bar user menu.
  const account = {
    name: me.user.display_name || me.user.email,
    role: me.role ? roleLabels[me.role] : "No active membership",
    busy,
    onSignOut: signOut,
  };
  return (
    <div className="min-h-dvh bg-[var(--fs-shell)] sm:p-3 lg:p-4">
      <div className="flex min-h-dvh border-[color:var(--fs-border)] bg-[var(--fs-surface)] text-[color:var(--fs-text)] shadow-[var(--fs-shadow-card)] sm:min-h-[calc(100dvh-1.5rem)] sm:rounded-2xl sm:border lg:min-h-[calc(100dvh-2rem)]">
        <Rail
          links={links}
          path={path}
          home={home}
          state={rail}
          onToggle={toggleRail}
          account={account}
        />
        <div className="min-w-0 flex-1">
          <header className="flex items-center gap-3 border-b border-[color:var(--fs-border)] px-5 py-4 sm:px-8 sm:py-5">
            <MobileMenu links={links} path={path} account={account} />
            <Link
              href={home}
              aria-label="fourstacks home"
              className="flex items-center lg:hidden"
            >
              <Image
                src="/brand/fourstacks-mark.svg"
                alt=""
                width={32}
                height={32}
              />
            </Link>
            <div className="min-w-0">
              <p className="eyebrow">CSA Console</p>
              <p className="mt-1 text-sm text-[color:var(--fs-text-secondary)]">
                {admin ? "Platform administration" : "Tenant workspace"}
              </p>
            </div>
          </header>
          <main className="mx-auto max-w-6xl p-5 sm:p-8">
            {error && (
              <div className="mb-5">
                <Notice error>{error}</Notice>
              </div>
            )}
            <PageContextProvider>
              {children}
              {/* The dock reads ?client_id=, so it renders client-side under Suspense. */}
              <Suspense fallback={null}>
                <AssistantDock me={me} packages={packages} />
              </Suspense>
            </PageContextProvider>
          </main>
        </div>
      </div>
    </div>
  );
}
