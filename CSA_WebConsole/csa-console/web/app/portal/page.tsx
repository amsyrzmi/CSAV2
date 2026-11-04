import { requireIdentity } from "@/lib/server-identity";
import { OverviewHome } from "@/components/console/overview/OverviewHome";

/** Overview is the assistant home; email, role and tenant live in the user menu. */
export default async function PortalOverview() {
  return <OverviewHome me={await requireIdentity()} />;
}
