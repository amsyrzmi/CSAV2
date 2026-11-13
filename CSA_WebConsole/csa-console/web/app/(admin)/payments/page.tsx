import { requireIdentity } from "@/lib/server-identity";
import { PaymentsWorkspace } from "@/components/console/PaymentsWorkspace";

/** Platform admins always name the tenant: `?client_id=` preselects it; `?invoice=` opens one. */
export default async function PaymentsPage({ searchParams }: PageProps<"/payments">) {
  const query = await searchParams;
  return <PaymentsWorkspace me={await requireIdentity()}
    initialClientId={typeof query.client_id === "string" ? query.client_id : ""}
    initialInvoiceId={typeof query.invoice === "string" ? query.invoice : ""} />;
}
