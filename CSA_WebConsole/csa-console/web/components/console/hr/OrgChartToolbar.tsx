"use client";
import { useState } from "react";
import { orgChartCsv, type OrgChartFilter, type OrgFilterChoices } from "@/lib/hr-org-chart";
import type { OrgChart as OrgChartData } from "@/lib/hr-people-api";
import { field, secondary, Notice } from "../ui";

export const FILE_NAME = "org-chart.csv";

/**
 * Hands `text` to the browser as a UTF-8 file. The byte order mark (U+FEFF, written as an escape so it can be seen) goes in here, at download time
 * only, so a spreadsheet opens Malay names correctly while the CSV text stays clean.
 */
function saveCsv(text: string) {
  const url = URL.createObjectURL(new Blob(["\uFEFF", text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = FILE_NAME;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export interface OrgChartToolbarProps {
  /** What the chart shows now: the CSV is this, not the whole company. */
  shown: OrgChartData;
  choices: OrgFilterChoices;
  filter: OrgChartFilter;
  onFilter: (change: Partial<OrgChartFilter>) => void;
  onClear: () => void;
  onExpandAll: () => void;
  onCollapseAll: () => void;
}

/**
 * The org chart's controls: department, legal entity (only when the company has any) and search
 * narrow the chart; Expand all and Collapse all fold it; Print and Download CSV take it away.
 * Nothing here moves focus: each control keeps it while the chart changes underneath.
 */
export function OrgChartToolbar({ shown, choices, filter, onFilter, onClear, onExpandAll, onCollapseAll }: OrgChartToolbarProps) {
  const [problem, setProblem] = useState("");
  const hasEntities = choices.legalEntities.length > 0;
  const filtered = Boolean(filter.department_id || filter.legal_entity || filter.query?.trim());
  const people = shown.nodes.length;
  function download() {
    try { saveCsv(orgChartCsv(shown, { includeLegalEntity: hasEntities })); setProblem(""); }
    catch { setProblem("We could not prepare the CSV. Try again."); }
  }
  return <div data-org-toolbar className="space-y-3">
    <form role="search" aria-label="Narrow the org chart" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" onSubmit={event => event.preventDefault()}>
      <label className="block text-sm sm:col-span-2 lg:col-span-1">Search people
        <input className={`${field} mt-1`} type="search" maxLength={120} placeholder="Name or job title"
          value={filter.query ?? ""} onChange={event => onFilter({ query: event.target.value })} /></label>
      <label className="block text-sm">Department
        <select className={`${field} mt-1`} value={filter.department_id ?? ""} onChange={event => onFilter({ department_id: event.target.value })}>
          <option value="">All departments</option>
          {choices.departments.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
      {hasEntities && <label className="block text-sm">Legal entity
        <select className={`${field} mt-1`} value={filter.legal_entity ?? ""} onChange={event => onFilter({ legal_entity: event.target.value })}>
          <option value="">All legal entities</option>
          {choices.legalEntities.map(item => <option key={item} value={item}>{item}</option>)}
        </select></label>}
    </form>
    <div role="group" aria-label="Org chart actions" className="flex flex-wrap gap-2">
      <button type="button" className={secondary} onClick={onExpandAll}>Expand all</button>
      <button type="button" className={secondary} onClick={onCollapseAll}>Collapse all</button>
      <button type="button" className={secondary} onClick={() => window.print()}>Print</button>
      <button type="button" className={secondary} onClick={download}>Download CSV ({people} {people === 1 ? "person" : "people"})</button>
      {filtered && <button type="button" className={secondary} onClick={onClear}>Clear filters</button>}
    </div>
    {problem && <Notice error>{problem}</Notice>}
  </div>;
}
