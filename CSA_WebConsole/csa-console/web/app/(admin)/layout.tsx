import { redirect } from "next/navigation";
import { requireIdentity } from "@/lib/server-identity";
import { AppShell } from "@/components/AppShell";

export default async function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const me = await requireIdentity();
  if (me.user.must_change_password) redirect("/change-password");
  if (me.role !== "platform_admin") redirect("/portal");
  return <AppShell me={me}>{children}</AppShell>;
}
