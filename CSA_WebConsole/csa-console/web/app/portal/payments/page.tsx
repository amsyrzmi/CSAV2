import { requireIdentity } from "@/lib/server-identity";
import { PaymentsWorkspace } from "@/components/console/PaymentsWorkspace";

/** `?invoice=` opens that invoice (the Subscriptions drawer links here). */
export default async function PaymentsPage({ searchParams }: PageProps<"/portal/payments">) {
  const query = await searchParams;
  return <PaymentsWorkspace me={await requireIdentity()}
    initialInvoiceId={typeof query.invoice === "string" ? query.invoice : ""} />;
}
