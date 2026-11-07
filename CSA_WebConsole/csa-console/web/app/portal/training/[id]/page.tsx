import { notFound } from "next/navigation";
import { Heading } from "@/components/console/ui";
import { requireIdentity } from "@/lib/server-identity";
import { isItemId } from "@/lib/training-view";

// Placeholder until the Training library workspace lands: it then renders that component with
// `me` and this item id, in place of the heading.
export default async function TrainingItemPage({ params }: PageProps<"/portal/training/[id]">) {
  const { id } = await params;
  if (!isItemId(id)) notFound();
  await requireIdentity();
  return <Heading title="Training library" description="This PDF will open here." />;
}
