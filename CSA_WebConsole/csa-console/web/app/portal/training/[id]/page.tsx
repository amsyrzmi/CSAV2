import { notFound } from "next/navigation";
import { TrainingWorkspace } from "@/components/console/TrainingWorkspace";
import { requireIdentity } from "@/lib/server-identity";
import { isItemId } from "@/lib/training-view";

export default async function TrainingItemPage({ params }: PageProps<"/portal/training/[id]">) {
  const { id } = await params;
  if (!isItemId(id)) notFound();
  return <TrainingWorkspace me={await requireIdentity()} itemId={id} />;
}
