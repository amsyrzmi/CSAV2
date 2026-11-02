"use client";
import Link from "next/link";
import { orgTree, type OrgTreeNode } from "@/lib/hr-people";
import type { OrgChart as OrgChartData } from "@/lib/hr-people-api";
import { Notice } from "../ui";

function Branch({ nodes, hrefFor, root = false }: { nodes: OrgTreeNode[]; hrefFor: (employeeId: string) => string; root?: boolean }) {
  return <ul aria-label={root ? "Org chart" : undefined} className={root ? "space-y-2" : "mt-2 space-y-2 border-l border-ink-200 pl-5"}>
    {nodes.map(node => <li key={node.employee_id}>
      <Link className="font-medium underline" href={hrefFor(node.employee_id)}>{node.display_name}</Link>
      {node.job_title && <span className="ml-2 text-sm text-ink-500">{node.job_title}</span>}
      {node.children.length > 0 && <Branch nodes={node.children} hrefFor={hrefFor} />}
    </li>)}
  </ul>;
}

/**
 * The whole company as a tree by manager. People in a reporting loop stand at the top, flat; only the
 * owner is told about the loop, because only they can fix it.
 */
export function OrgChart({ chart, owner, hrefFor }: { chart: OrgChartData; owner: boolean; hrefFor: (employeeId: string) => string }) {
  const names = new Map(chart.nodes.map(node => [node.employee_id, node.display_name]));
  const looped = chart.looped.flatMap(id => names.get(id) ?? []);
  return <div className="space-y-4">
    {owner && looped.length > 0 && <Notice error>
      These people are each other&apos;s managers in a loop: {looped.join(", ")}. Change a manager on their profile to fix it.
    </Notice>}
    <Branch nodes={orgTree(chart)} hrefFor={hrefFor} root />
  </div>;
}
