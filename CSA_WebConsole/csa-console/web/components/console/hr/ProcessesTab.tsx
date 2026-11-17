"use client";
import { hrHref, type HrRoute } from "@/lib/hr-paths";
import SectionNav from "../kit/SectionNav";
import { Panel } from "../ui";
import type { HrTabProps } from "./HrShell";
import { ProcessDetail } from "./ProcessDetail";
import { ProcessList } from "./ProcessList";

export type ProcessesRoute = Extract<HrRoute, { tab: "processes" }>;

/**
 * The HR team's Processes tab: Processes (the list and each process) and Templates, two pages of
 * their own URL under one section bar. The shell shows the tab only to the HR team (`visibleTabs`).
 */
export function ProcessesTab({ route, tenant, readonly, platform, userId }: HrTabProps & { route: ProcessesRoute }) {
  const href = (next: HrRoute) => hrHref(next, platform, tenant);
  const templates = route.view === "templates" || route.view === "template" || route.view === "new-template";
  return <div data-testid="hr-processes" className="space-y-5">
    <SectionNav label="Process pages" testId="hr-process-views" current={templates ? "templates" : "list"} items={[
      { key: "list", label: "Processes", href: href({ tab: "processes", view: "list" }), testId: "hr-process-view-list" },
      { key: "templates", label: "Templates", href: href({ tab: "processes", view: "templates" }),
        testId: "hr-process-view-templates" },
    ]} />
    {route.view === "list" && <ProcessList tenant={tenant} readonly={readonly} platform={platform} />}
    {route.view === "detail" && <ProcessDetail key={route.processId} tenant={tenant} processId={route.processId}
      readonly={readonly} userId={userId} />}
    {/* Task 5 replaces this placeholder */}
    {templates && <Panel title="Templates">
      <p data-testid="hr-templates-placeholder" className="text-sm">Templates are set up in the next step.</p>
    </Panel>}
  </div>;
}
