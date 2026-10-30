"use client";

import { usePathname, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { identity } from "@/lib/identity-api";
import type { Me } from "@/lib/identity-contracts";
import { roleLabels } from "@/lib/identity-contracts";
import { navigationFor } from "@/lib/navigation";
import { writeRail, type RailState } from "@/lib/shell-state";
import type { PackageState } from "@/lib/services-api";
import { AssistantDock } from "@/components/console/AssistantDock";
import { Rail } from "@/components/console/shell/Rail";
import { TopBar } from "@/components/console/shell/TopBar";
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
  const account = {
    name: me.user.display_name || me.user.email,
    role: me.role ? roleLabels[me.role] : "No active membership",
    busy,
    onSignOut: signOut,
  };
  return (
    <div
      data-testid="shell-root"
      className="flex min-h-dvh bg-[var(--fs-surface)] text-[color:var(--fs-text)]"
    >
      <Rail
        links={links}
        path={path}
        home={home}
        state={rail}
        onToggle={toggleRail}
      />
      <div className="min-w-0 flex-1">
        <TopBar
          links={links}
          path={path}
          home={home}
          fallbackTitle={admin ? "Platform administration" : "Workspace"}
          account={account}
        />
        {/* Outside <main> and every dialog, so a failed sign-out is never hidden behind one. */}
        {error && (
          <div className="px-4 pt-4 sm:px-8">
            <Notice error>{error}</Notice>
          </div>
        )}
        <main className="mx-auto max-w-6xl p-5 sm:p-8">
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
  );
}
