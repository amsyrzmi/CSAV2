"use client";
import Link from "next/link";
import { useId, useMemo, useRef, useState } from "react";
import { departmentTint, filterOrgChart, orgFilterChoices, orgMarker, type OrgChartFilter } from "@/lib/hr-org-chart";
import { orgTree, type OrgTreeNode } from "@/lib/hr-people";
import type { OrgChart as OrgChartData } from "@/lib/hr-people-api";
import StatusLabel from "../fs/StatusLabel";
import { secondary, Notice } from "../ui";
import { OrgChartToolbar } from "./OrgChartToolbar";

/** Levels below this one start folded: roots and their reports show, deeper teams open on request. */
const OPEN_DEPTH = 2;
/** A fold the person chose (true is open); a node they never touched follows the default. */
type Folds = ReadonlyMap<string, boolean>;

interface BranchProps {
  nodes: OrgTreeNode[];
  depth: number;
  prefix: string;
  /** The people the server reports in a reporting loop (their `manager_id` is null). */
  loopIds: ReadonlySet<string>;
  hrefFor: (employeeId: string) => string;
  open: (id: string, depth: number) => boolean;
  onToggle: (id: string, now: boolean) => void;
}

/** The department as a coloured chip. The name is always printed beside the colour, never left to it. */
function DepartmentChip({ node }: { node: OrgTreeNode }) {
  const tint = departmentTint(node.department_id);
  if (!node.department) return null;
  return <span data-testid="org-department" data-org-chip data-tint={tint ?? undefined}
    className="rounded-[var(--fs-radius-pill)] px-2 py-0.5 text-xs font-semibold leading-snug [overflow-wrap:anywhere]"
    style={tint ? { background: `var(--fs-dept-${tint}-bg)`, color: `var(--fs-dept-${tint}-text)` }
      : { background: "var(--fs-neutral-bg)", color: "var(--fs-neutral-text)" }}>{node.department}</span>;
}

/**
 * Why someone stands at the top, from what the server sends: a manager who left or is hidden from
 * this viewer (`manager_outside`, the id is withheld), a reporting loop (`manager_id` is null for
 * everyone in it), or no manager at all. A root with a manager on the chart gets no label.
 */
function rootLabel(node: OrgTreeNode, loopIds: ReadonlySet<string>): string | null {
  if (node.manager_outside) return "Manager not in the chart";
  if (loopIds.has(node.employee_id)) return "In a reporting loop";
  return node.manager_id ? null : "No manager";
}

function Chevron({ open }: { open: boolean }) {
  return <svg aria-hidden="true" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    style={{ transform: open ? "rotate(90deg)" : undefined }}><path d="M6 3l5 5-5 5" /></svg>;
}

function Branch({ nodes, depth, prefix, loopIds, hrefFor, open, onToggle }: BranchProps) {
  return <ul aria-label={depth === 0 ? "Org chart" : undefined}
    className={depth === 0 ? "space-y-1" : "mt-1 space-y-1 border-l border-[color:var(--fs-border)] pl-2 sm:pl-5"}>
    {nodes.map(node => {
      const kids = node.children.length;
      const isOpen = open(node.employee_id, depth);
      const marker = orgMarker(node);
      const listId = `${prefix}-${node.employee_id}`;
      return <li key={node.employee_id}>
        <div data-testid="org-row" className="flex flex-wrap items-center gap-x-2 gap-y-1">
          {kids > 0
            ? <button type="button" aria-expanded={isOpen} aria-controls={listId}
              aria-label={`${isOpen ? "Collapse" : "Expand"} ${node.display_name}'s team (${kids})`}
              onClick={() => onToggle(node.employee_id, !isOpen)}
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-[var(--fs-radius-control)] text-[color:var(--fs-text-secondary)] hover:bg-[var(--fs-tile)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:size-11">
              <Chevron open={isOpen} /></button>
            : <span aria-hidden="true" className="size-8 shrink-0 pointer-coarse:size-11" />}
          <Link className="min-w-0 font-medium underline [overflow-wrap:anywhere]" href={hrefFor(node.employee_id)}>{node.display_name}</Link>
          {node.job_title && <span className="min-w-0 text-sm text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">{node.job_title}</span>}
          <DepartmentChip node={node} />
          {marker === "starting" && <span data-testid="org-marker"><StatusLabel tone="info">Starting</StatusLabel></span>}
          {marker === "leaving" && <span data-testid="org-marker"><StatusLabel tone="attention">Leaving</StatusLabel></span>}
          {depth === 0 && rootLabel(node, loopIds) && <span className="text-xs text-[color:var(--fs-text-secondary)]">{rootLabel(node, loopIds)}</span>}
        </div>
        {kids > 0 && <div id={listId} hidden={!isOpen}>
          <Branch nodes={node.children} depth={depth + 1} prefix={prefix} loopIds={loopIds} hrefFor={hrefFor} open={open} onToggle={onToggle} />
        </div>}
      </li>;
    })}
  </ul>;
}

/** How many of `nodes` can be seen: a folded node hides everyone under it. */
const seen = (nodes: OrgTreeNode[], depth: number, open: BranchProps["open"]): number =>
  nodes.reduce((sum, node) => sum + 1 + (open(node.employee_id, depth) ? seen(node.children, depth + 1, open) : 0), 0);

/**
 * The whole company as a tree by manager. Every person is here: people in a reporting loop, and
 * people whose manager left or sits outside the chart, stand at the top. Each manager folds their
 * team; the department, legal entity and search narrow the chart to the matches and the line of
 * managers above them. Only the owner is told about a loop, because only they can fix it.
 */
export function OrgChart({ chart, owner, canSeeLegalEntity, hrefFor }: {
  chart: OrgChartData; owner: boolean; canSeeLegalEntity: boolean; hrefFor: (employeeId: string) => string;
}) {
  const prefix = useId();
  const result = useRef<HTMLHeadingElement>(null);
  const [filter, setFilter] = useState<OrgChartFilter>({});
  const [folds, setFolds] = useState<Folds>(new Map());
  const filtered = Boolean(filter.department_id || filter.legal_entity || filter.query?.trim());
  const shown = useMemo(() => filterOrgChart(chart, filter), [chart, filter]);
  const tree = useMemo(() => orgTree(shown), [shown]);
  const choices = useMemo(() => {
    const found = orgFilterChoices(chart);
    return canSeeLegalEntity ? found : { ...found, legalEntities: [] };
  }, [chart, canSeeLegalEntity]);
  const names = new Map(chart.nodes.map(node => [node.employee_id, node.display_name]));
  const looped = chart.looped.flatMap(id => names.get(id) ?? []);
  // Narrowing opens every branch so a match is never tucked away, but a fold the person chose stays.
  const open = (id: string, depth: number) => folds.get(id) ?? (filtered || depth < OPEN_DEPTH);
  const folded = shown.nodes.length - seen(tree, 0, open);
  const loopIds = useMemo(() => new Set(chart.looped), [chart]);
  const setAll = (value: boolean) => setFolds(new Map(chart.nodes.map(node => [node.employee_id, value])));
  const clear = () => { setFilter({}); result.current?.focus(); };

  return <div className="space-y-4">
    {owner && looped.length > 0 && <Notice error>
      These people are each other&apos;s managers in a loop: {looped.join(", ")}. Change a manager on their profile to fix it.
    </Notice>}
    <OrgChartToolbar shown={shown} choices={choices} filter={filter}
      onFilter={change => setFilter(old => ({ ...old, ...change }))} onClear={clear}
      onExpandAll={() => setAll(true)} onCollapseAll={() => setAll(false)} />
    <div data-org-print className="space-y-3">
      <p className="hidden font-semibold print:block">Org chart</p>
      <h3 ref={result} tabIndex={-1} className="font-(family-name:--fs-font-heading) text-sm font-semibold focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]">Reporting lines</h3>
      <p className="text-sm text-[color:var(--fs-text-secondary)]">
        <span data-testid="org-count" aria-live="polite">{shown.nodes.length} {shown.nodes.length === 1 ? "person" : "people"} shown</span>
        {folded > 0 && <span data-org-screen data-testid="org-folded">{" "}({folded} inside folded teams)</span>}
      </p>
      {shown.nodes.length === 0
        ? <div className="space-y-3">
          <p className="text-sm">No one matches the search or the filters.</p>
          <button type="button" className={secondary} onClick={clear}>Clear filters</button>
        </div>
        : <div className="max-w-full overflow-x-auto"><Branch nodes={tree} depth={0} prefix={prefix} loopIds={loopIds} hrefFor={hrefFor} open={open} onToggle={(id, now) => setFolds(old => new Map(old).set(id, now))} /></div>}
    </div>
  </div>;
}
