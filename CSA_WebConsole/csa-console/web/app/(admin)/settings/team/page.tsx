import { requireIdentity } from "@/lib/server-identity";
import { TeamManagement } from "@/components/console/TeamManagement";
export default async function TeamPage() {
  const me = await requireIdentity();
  return <TeamManagement platform selfId={me.user.id} />;
}
