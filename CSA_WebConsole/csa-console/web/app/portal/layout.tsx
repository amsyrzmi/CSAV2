import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { activePackages, requireIdentity } from "@/lib/server-identity";
import { AppShell } from "@/components/AppShell";
import { RAIL_COOKIE, parseRail } from "@/lib/shell-state";

export default async function PortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [me, packages] = await Promise.all([requireIdentity(), activePackages()]);
  if (me.user.must_change_password) redirect("/change-password");
  if (me.role === "platform_admin") redirect("/");
  const rail = parseRail((await cookies()).get(RAIL_COOKIE)?.value);
  return (
    <AppShell me={me} packages={packages} initialRail={rail}>
      {children}
    </AppShell>
  );
}
