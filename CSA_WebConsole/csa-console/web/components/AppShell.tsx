"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useLayoutEffect, useState } from "react";
import { identity } from "@/lib/identity-api";
import type { Me } from "@/lib/identity-contracts";
import { roleLabels } from "@/lib/identity-contracts";
import type { HrNavItem } from "@/lib/hr-paths";
import { navigationFor } from "@/lib/navigation";
import { writeRail, type RailState } from "@/lib/shell-state";
import type { PackageState } from "@/lib/services-api";
import { AssistantDock } from "@/components/console/AssistantDock";
import { useHrNav } from "@/components/console/hr/useHrNav";
import { Rail } from "@/components/console/shell/Rail";
import type { ShellAccount } from "@/components/console/shell/UserMenu";
import { TopBar } from "@/components/console/shell/TopBar";
import { Notice } from "@/components/console/ui";
import { PageContextProvider } from "@/lib/page-context";

/**
 * Reports the People & HR sub-pages for the rail. It reads `?client_id=` (a platform admin's
 * tenant), so it renders under Suspense and renders nothing itself.
 */
function HrNavBridge({ me, enabled, path, onItems }: {
  me: Me;
  enabled: boolean;
  path: string;
  onItems: (items: HrNavItem[] | null) => void;
}) {
  const platform = me.role === "platform_admin";
  const param = useSearchParams().get("client_id")?.trim() || null;
  const tenant = platform ? param : me.active_client_id;
  const items = useHrNav({ enabled, platform, tenant, path });
  // A layout effect, so a changed tenant's old sub-links are dropped before the next paint.
  useLayoutEffect(() => { onItems(items); }, [items, onItems]);
  return null;
}

export function AppShell({
  me,
  packages,
  children,
  initialRail,
  tenantName,
}: {
  me: Me;
  packages: PackageState[];
  children: React.ReactNode;
  initialRail?: RailState;
  tenantName?: string | null;
}) {
  const path = usePathname();
  const router = useRouter();
  const admin = me.role === "platform_admin";
  const home = admin ? "/" : "/portal";
  const [hrItems, setHrItems] = useState<HrNavItem[] | null>(null);
  const baseLinks = navigationFor(me, packages);
  // People & HR gets its sub-pages once the HR context is known; until then (and if it fails) it is a plain link.
  const links = baseLinks.map((link) =>
    link.key === "hr" && hrItems?.length ? { ...link, children: hrItems } : link,
  );
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
  const account: ShellAccount = {
    name: me.user.display_name || me.user.email,
    email: me.user.email,
    tenant: tenantName ?? null,
    role: me.role ? roleLabels[me.role] : "Not linked to a company yet",
    busy,
    onSignOut: signOut,
  };
  return (
    <div
      data-testid="shell-root"
      className="flex min-h-dvh bg-[var(--fs-surface)] text-[color:var(--fs-text)]"
    >
      <Suspense fallback={null}>
        <HrNavBridge
          me={me}
          enabled={baseLinks.some((link) => link.key === "hr")}
          path={path}
          onItems={setHrItems}
        />
      </Suspense>
      <Rail
        links={links}
        path={path}
        home={home}
        state={rail}
        onToggle={toggleRail}
      />
      {/* A page marked `data-fill` (Overview) gets the whole window below the top bar: the
          column becomes exactly the viewport tall (never shorter than 30rem) and <main> fills
          it edge to edge, so the page's own scroll areas scroll instead of the document. */}
      <div className="min-w-0 flex-1 has-[[data-fill]]:flex has-[[data-fill]]:h-dvh has-[[data-fill]]:min-h-[30rem] has-[[data-fill]]:flex-col">
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
        <main className="mx-auto max-w-6xl p-5 sm:p-8 has-[[data-fill]]:flex has-[[data-fill]]:min-h-0 has-[[data-fill]]:w-full has-[[data-fill]]:max-w-none has-[[data-fill]]:flex-1 has-[[data-fill]]:flex-col has-[[data-fill]]:p-0">
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
