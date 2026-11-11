import { requireIdentity } from "@/lib/server-identity";
import { ConnectionsWorkspace } from "@/components/console/ConnectionsWorkspace";

export default async function ConnectionsPage() {
  return <ConnectionsWorkspace me={await requireIdentity()} />;
}
