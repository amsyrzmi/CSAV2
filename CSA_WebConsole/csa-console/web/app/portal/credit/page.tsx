import { notFound } from "next/navigation";
import { requireIdentity } from "@/lib/server-identity";
import { CreditWorkspace } from "@/components/console/CreditWorkspace";

/** The company's AI Usage Charges and bill, for its Admin only (the server refuses everyone else too). */
export default async function CreditPage() {
  const me = await requireIdentity();
  if (me.role !== "tenant_admin") notFound();
  return <CreditWorkspace />;
}
