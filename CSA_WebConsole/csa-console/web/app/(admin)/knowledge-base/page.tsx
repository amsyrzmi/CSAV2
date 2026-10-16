import { requireIdentity } from "@/lib/server-identity";
import { KnowledgeWorkspace } from "@/components/console/KnowledgeWorkspace";

export default async function KnowledgePage() {
  return <KnowledgeWorkspace me={await requireIdentity()} />;
}
