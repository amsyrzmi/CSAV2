import { WorkspaceAssistant } from "@/components/console/WorkspaceAssistant";
import { requireIdentity } from "@/lib/server-identity";

export default async function WorkspacePage() {
  return <WorkspaceAssistant me={await requireIdentity()} />;
}
