import { TrainingWorkspace } from "@/components/console/TrainingWorkspace";
import { requireIdentity } from "@/lib/server-identity";

export default async function TrainingLibraryPage() {
  return <TrainingWorkspace me={await requireIdentity()} />;
}
