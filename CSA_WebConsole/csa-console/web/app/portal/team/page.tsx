import { notFound } from "next/navigation";
import { requireIdentity } from "@/lib/server-identity";
import { TeamManagement } from "@/components/console/TeamManagement";
export default async function TeamPage() {
  const me = await requireIdentity();
  if (me.role !== "tenant_admin") notFound();
  return <TeamManagement clientId={me.active_client_id} />;
}
