"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { knowledgePages, type KnowledgeSection } from "@/lib/navigation";
import { usePublishContext } from "@/lib/page-context";
import { PageIntro, SectionNav, useSectionArrivalFocus } from "./kit";
import AddSection from "./knowledge/AddSection";
import LibrarySection from "./knowledge/LibrarySection";
import MaintenanceSection from "./knowledge/MaintenanceSection";
import QuestionsSection from "./knowledge/QuestionsSection";
import { field, Notice } from "./ui";

const HEADINGS: Record<KnowledgeSection, string> = {
  library: "What your assistant knows",
  add: "Add knowledge",
  questions: "Unanswered questions",
  maintenance: "Maintenance and settings",
};

export function KnowledgeWorkspace({ me, section = "library" }: { me: Me; section?: KnowledgeSection }) {
  usePublishContext({ tab: "knowledge_base", section });
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const platform = me.role === "platform_admin";
  const [clients, setClients] = useState<Client[] | null>(null);
  // An administrator's chosen tenant lives in the URL (?client_id=) so it survives a move
  // between the four pages and a reload.
  const [selected, setSelected] = useState(search.get("client_id") ?? "");
  const [error, setError] = useState("");
  const pageHeading = useRef<HTMLHeadingElement>(null);
  useSectionArrivalFocus(pageHeading);
  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients).catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [platform]);
  const clientId = platform ? selected : me.active_client_id;
  const query = platform && clientId ? `client_id=${encodeURIComponent(clientId)}` : "";
  const pages = knowledgePages(platform);
  const chooseTenant = (value: string) => {
    setSelected(value);
    router.replace(value ? `${pathname}?client_id=${encodeURIComponent(value)}` : pathname, { scroll: false });
  };
  const canWrite = me.role !== null && me.role !== "read_only";
  const servicesHref = platform ? "/services" : "/portal/services";
  const addHref = query ? `${pages[1].href}?${query}` : pages[1].href;
  return <>
    <h1 className="page-title mb-2 font-(family-name:--fs-font-heading) text-(length:--fs-text-page) font-semibold">Business knowledge</h1>
    <PageIntro>Tenant documents, original sources, ingestion jobs and unanswered questions.</PageIntro>
    <SectionNav label="Business knowledge pages" testId="kb-subnav" items={pages} current={section}
      query={query || undefined} />
    <h2 ref={pageHeading} tabIndex={-1} data-testid="kb-page-heading"
      className="mb-3 font-(family-name:--fs-font-heading) text-lg font-semibold focus:outline-none">
      {HEADINGS[section]}
    </h2>
    {error && <Notice error>{error}</Notice>}
    {platform && <div className="mb-5">
      <label htmlFor="kb-tenant" className="mb-2 block text-sm">Tenant</label>
      <select id="kb-tenant" className={field} value={selected} onChange={(e) => chooseTenant(e.target.value)}>
        <option value="">{clients ? "Select a tenant" : "Loading tenants..."}</option>
        {clients?.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </div>}
    {clientId ? <div key={clientId}>
      {section === "library" && <LibrarySection query={query} canWrite={canWrite} addHref={addHref} />}
      {section === "add" && <AddSection query={query} canWrite={canWrite} />}
      {section === "questions" && <QuestionsSection query={query} canWrite={canWrite} />}
      {section === "maintenance" && <MaintenanceSection query={query} canWrite={canWrite} servicesHref={servicesHref} />}
    </div> : <Notice>Select a tenant to continue.</Notice>}
  </>;
}
