import { requireIdentity } from "@/lib/server-identity";
import { HrWorkspace } from "@/components/console/HrWorkspace";

export default async function HrPage({ searchParams }: PageProps<"/hr">) {
  const query = await searchParams;
  return <HrWorkspace me={await requireIdentity()}
    initialTenant={typeof query.client_id === "string" ? query.client_id : ""}
    initialSection={query.section === "directory" ? "directory" : "training"} />;
}
