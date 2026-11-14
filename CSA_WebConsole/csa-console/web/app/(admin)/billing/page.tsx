import { notFound } from "next/navigation";
import { requireIdentity } from "@/lib/server-identity";
import { BillingAdminWorkspace } from "@/components/console/BillingAdminWorkspace";

/**
 * Every company's billing, raw cost and margin, for the platform team only. The admin layout
 * already sends everyone else to /portal; this check and the API's own keep it that way.
 */
export default async function BillingPage() {
  const me = await requireIdentity();
  if (me.role !== "platform_admin") notFound();
  return <BillingAdminWorkspace />;
}
