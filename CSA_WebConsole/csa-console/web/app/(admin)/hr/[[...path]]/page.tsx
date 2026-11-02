import { notFound, redirect } from "next/navigation";
import { HrShell } from "@/components/console/hr/HrShell";
import { hrHref, legacyHrRoute, parseHrPath } from "@/lib/hr-paths";
import { requireIdentity } from "@/lib/server-identity";

export default async function HrPage({ params, searchParams }: PageProps<"/hr/[[...path]]">) {
  const { path } = await params;
  const query = await searchParams;
  const section = typeof query.section === "string" ? query.section : null;
  const clientId = typeof query.client_id === "string" ? query.client_id : "";
  const legacy = !path || path.length === 0 ? legacyHrRoute(section) : null;
  if (legacy) redirect(hrHref(legacy, true, clientId));
  const route = parseHrPath(path) ?? notFound();
  return <HrShell me={await requireIdentity()} route={route} initialTenant={clientId} />;
}
