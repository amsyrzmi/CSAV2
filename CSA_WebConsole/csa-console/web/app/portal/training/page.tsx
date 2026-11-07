import { Heading } from "@/components/console/ui";
import { requireIdentity } from "@/lib/server-identity";

// Placeholder until the Training library workspace lands: it then renders that component with
// `me` and no item, in place of the heading.
export default async function TrainingLibraryPage() {
  await requireIdentity();
  return <Heading title="Training library" description="Your company's training PDFs will appear here." />;
}
