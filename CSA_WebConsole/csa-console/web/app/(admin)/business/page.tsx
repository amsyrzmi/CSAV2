import { requireIdentity } from "@/lib/server-identity";
import { BusinessWorkspace } from "@/components/console/BusinessWorkspace";

export default async function BusinessPage() {
  return <BusinessWorkspace me={await requireIdentity()} />;
}
