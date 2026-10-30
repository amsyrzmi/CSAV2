import { requireIdentity } from "@/lib/server-identity";
import { OverviewHome } from "@/components/console/overview/OverviewHome";

/** Overview is the assistant home; Tenants and Members stay in the navigation. */
export default async function Overview() {
  return <OverviewHome me={await requireIdentity()} />;
}
