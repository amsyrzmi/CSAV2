import { requireIdentity } from "@/lib/server-identity";
import { SubscriptionsWorkspace } from "@/components/console/SubscriptionsWorkspace";

/** Platform admins always name the tenant: `?client_id=` preselects it, as for the dock. */
export default async function SubscriptionsPage({ searchParams }: PageProps<"/subscriptions">) {
  const query = await searchParams;
  return <SubscriptionsWorkspace me={await requireIdentity()}
    initialClientId={typeof query.client_id === "string" ? query.client_id : ""} />;
}
