import { requireIdentity } from "@/lib/server-identity";
import { HrWorkspace } from "@/components/console/HrWorkspace";

export default async function HrPage() {
  return <HrWorkspace me={await requireIdentity()} />;
}
