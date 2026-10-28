import { requireIdentity } from "@/lib/server-identity";
import { SubscriptionsWorkspace } from "@/components/console/SubscriptionsWorkspace";

export default async function SubscriptionsPage() {
  return <SubscriptionsWorkspace me={await requireIdentity()} />;
}
