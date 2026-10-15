import { redirect } from "next/navigation";
import { requireIdentity } from "@/lib/server-identity";
import { AppShell } from "@/components/AppShell";

export default async function PortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const me = await requireIdentity();
  if (me.user.must_change_password) redirect("/change-password");
  if (me.role === "platform_admin") redirect("/");
  return <AppShell me={me}>{children}</AppShell>;
}
