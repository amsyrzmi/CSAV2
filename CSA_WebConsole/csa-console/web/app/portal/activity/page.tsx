import { requireIdentity } from "@/lib/server-identity";
import { RuntimeWorkspace } from "@/components/console/RuntimeWorkspace";

export default async function ActivityPage({
  searchParams,
}: {
  searchParams: Promise<{ agent?: string | string[] }>;
}) {
  const params = await searchParams;
  return <RuntimeWorkspace me={await requireIdentity()} mode="activity"
    initialAgent={typeof params.agent === "string" ? params.agent : ""} />;
}
