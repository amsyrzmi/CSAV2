import { requireIdentity } from "@/lib/server-identity";
import { HrWorkspace } from "@/components/console/HrWorkspace";

export default async function HrPage({ searchParams }: PageProps<"/portal/hr">) {
  const query = await searchParams;
  return <HrWorkspace me={await requireIdentity()}
    initialSection={query.section === "directory" ? "directory" : "training"} />;
}
