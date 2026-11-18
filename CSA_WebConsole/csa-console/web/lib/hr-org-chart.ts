import type { OrgChart, OrgNode } from "./hr-people-api";
import { orgTree, type OrgTreeNode } from "./hr-people";

export interface OrgChartFilter { department_id?: string; legal_entity?: string; query?: string }
export interface OrgFilterChoices { departments: { id: string; name: string }[]; legalEntities: string[] }

const clean = (value: string | null | undefined) => (value ?? "").trim();
const fold = (value: string | null | undefined) => clean(value).toLowerCase();

/**
 * The chart narrowed to the people who match every filter that is set, plus the manager chain of
 * each match so nobody is shown without the line above them. Search matches the name or the job
 * title; the legal entity is compared trimmed and case-insensitively. With no filter set the
 * chart is returned as it came.
 */
export function filterOrgChart(chart: OrgChart, f: OrgChartFilter): OrgChart {
  const query = fold(f.query);
  const department = clean(f.department_id);
  const entity = fold(f.legal_entity);
  if (!query && !department && !entity) return chart;
  const byId = new Map(chart.nodes.map((item) => [item.employee_id, item]));
  const matches = (item: OrgNode) =>
    (!query || fold(item.display_name).includes(query) || fold(item.job_title).includes(query)) &&
    (!department || item.department_id === department) &&
    (!entity || fold(item.legal_entity_reference) === entity);
  const keep = new Set<string>();
  for (const item of chart.nodes) {
    if (!matches(item)) continue;
    // `keep` doubles as the loop guard: a reporting loop stops at the first person already kept.
    for (let at: OrgNode | undefined = item; at && !keep.has(at.employee_id); at = at.manager_id ? byId.get(at.manager_id) : undefined) {
      keep.add(at.employee_id);
    }
  }
  return {
    nodes: chart.nodes.filter((item) => keep.has(item.employee_id)),
    looped: chart.looped.filter((id) => keep.has(id)),
  };
}

/** The departments and legal entities the chart carries, each once and sorted by name. */
export function orgFilterChoices(chart: OrgChart): OrgFilterChoices {
  const departments = new Map<string, string>();
  const entities = new Map<string, string>();
  for (const item of chart.nodes) {
    if (item.department_id && !departments.has(item.department_id)) {
      departments.set(item.department_id, clean(item.department) || item.department_id);
    }
    const entity = clean(item.legal_entity_reference);
    if (entity && !entities.has(entity.toLowerCase())) entities.set(entity.toLowerCase(), entity);
  }
  return {
    departments: [...departments].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    legalEntities: [...entities.values()].sort((a, b) => a.localeCompare(b)),
  };
}

/** The badge a node carries: someone about to start or about to leave. */
export function orgMarker(node: Pick<OrgNode, "status">): "starting" | "leaving" | null {
  return node.status === "starting" || node.status === "leaving" ? node.status : null;
}

/** Stable FNV-1a (32 bit) over the department id, so a department keeps its tint (1 to 6). */
export function departmentTint(departmentId: string | null): 1 | 2 | 3 | 4 | 5 | 6 | null {
  if (!departmentId) return null;
  let hash = 0x811c9dc5;
  for (let i = 0; i < departmentId.length; i += 1) {
    hash ^= departmentId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (((hash >>> 0) % 6) + 1) as 1 | 2 | 3 | 4 | 5 | 6;
}

/** A spreadsheet runs a cell that starts with one of these as a formula. */
const FORMULA_START = /^[=+\-@\t\r]/;

function cell(value: string | null | undefined): string {
  let text = value ?? "";
  if (FORMULA_START.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function flatten(tree: OrgTreeNode[]): OrgNode[] {
  return tree.flatMap(({ children, ...item }) => [item, ...flatten(children)]);
}

/**
 * The chart as CSV (RFC 4180, CRLF line ends, no byte order mark): one row per person in chart
 * order. Text that a spreadsheet would run as a formula gets a leading apostrophe. The legal
 * entity column is there only when asked for.
 */
export function orgChartCsv(chart: OrgChart, opts: { includeLegalEntity: boolean }): string {
  const names = new Map(chart.nodes.map((item) => [item.employee_id, item.display_name]));
  const header = ["Name", "Job title", "Department", "Manager", "Status", "Start date", "Last day"];
  if (opts.includeLegalEntity) header.push("Legal entity");
  const rows = flatten(orgTree(chart)).map((item) => {
    const row = [
      item.display_name,
      item.job_title,
      item.department,
      item.manager_id ? names.get(item.manager_id) : null,
      item.status,
      item.start_date,
      item.last_day,
    ];
    if (opts.includeLegalEntity) row.push(item.legal_entity_reference);
    return row;
  });
  return [header, ...rows].map((row) => row.map(cell).join(",")).join("\r\n");
}
