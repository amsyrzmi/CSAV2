import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { activePackages, optionalTenantName, requireIdentity } from "@/lib/server-identity";
import { AppShell } from "@/components/AppShell";
import { RAIL_COOKIE, parseRail } from "@/lib/shell-state";

export default async function PortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // The tenant name only decorates the user menu: read it alongside identity and packages,
  // and use it only when there is an active tenant.
  const [me, packages, tenantName] = await Promise.all([
    requireIdentity(),
    activePackages(),
    optionalTenantName(),
  ]);
  if (me.user.must_change_password) redirect("/change-password");
  if (me.role === "platform_admin") redirect("/");
  const rail = parseRail((await cookies()).get(RAIL_COOKIE)?.value);
  return (
    <AppShell
      me={me}
      packages={packages}
      initialRail={rail}
      tenantName={me.active_client_id ? tenantName : null}
    >
      {children}
    </AppShell>
  );
}
