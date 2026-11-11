import { requireIdentity } from "@/lib/server-identity";
import { EmailWorkspace } from "@/components/console/EmailWorkspace";

export default async function EmailPage({ searchParams }: PageProps<"/email">) {
  const query = await searchParams;
  return <EmailWorkspace me={await requireIdentity()}
    initialTenant={typeof query.client_id === "string" ? query.client_id : ""} />;
}
