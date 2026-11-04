import { requireIdentity } from "@/lib/server-identity";
import { KnowledgeWorkspace } from "@/components/console/KnowledgeWorkspace";

export default async function KnowledgeQuestionsPage() {
  return <KnowledgeWorkspace me={await requireIdentity()} section="questions" />;
}
