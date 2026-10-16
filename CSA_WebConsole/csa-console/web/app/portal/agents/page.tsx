import { requireIdentity } from "@/lib/server-identity";
import { RuntimeWorkspace } from "@/components/console/RuntimeWorkspace";

export default async function AgentsPage() {
  return <RuntimeWorkspace me={await requireIdentity()} mode="agents" />;
}
