import { requireIdentity } from "@/lib/server-identity";
import { ServicesWorkspace } from "@/components/console/ServicesWorkspace";

export default async function ServicesPage({ searchParams }: PageProps<"/services">) {
  const query = await searchParams;
  return <ServicesWorkspace me={await requireIdentity()}
    initialClientId={typeof query.client_id === "string" ? query.client_id : ""} />;
}
