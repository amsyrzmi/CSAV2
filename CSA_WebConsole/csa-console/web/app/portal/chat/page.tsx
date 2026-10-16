import { requireIdentity } from "@/lib/server-identity";
import { SandboxWorkspace } from "@/components/console/SandboxWorkspace";

export default async function ChatPage() {
  return <SandboxWorkspace me={await requireIdentity()} />;
}
