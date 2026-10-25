import { requireIdentity } from "@/lib/server-identity";
import { ServicesWorkspace } from "@/components/console/ServicesWorkspace";

export default async function ServicesPage() {
  return <ServicesWorkspace me={await requireIdentity()} />;
}
