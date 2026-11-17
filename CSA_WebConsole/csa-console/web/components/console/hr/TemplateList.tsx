"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { hrHref } from "@/lib/hr-paths";
import { peopleClient, type Department } from "@/lib/hr-people-api";
import { processClient, type TemplateSummary } from "@/lib/hr-process-api";
import { processLoadError } from "@/lib/hr-process-copy";
import { processKindLabel } from "@/lib/hr-process-view";
import { rulesText, templateTies } from "@/lib/hr-template-form";
import StatusLabel from "../fs/StatusLabel";
import { action, secondary, Notice, Panel } from "../ui";

const card = "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

/** The company's departments for the rules: `name(id)` reads "…" while loading and null for one not listed. */
export function useDepartments(tenant: string, enabled = true) {
  const [state, setState] = useState<{ items: Department[]; loaded: boolean; failed: boolean }>(
    { items: [], loaded: false, failed: false });
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    peopleClient(tenant).departments()
      .then(result => { if (active) setState({ items: result.items, loaded: true, failed: false }); })
      .catch(() => { if (active) setState({ items: [], loaded: true, failed: true }); });
    return () => { active = false; };
  }, [tenant, enabled]);
  const name = (departmentId: string): string | null => {
    if (!state.loaded) return "…";
    if (state.failed) return "a department";
    return state.items.find(item => item.department_id === departmentId)?.name ?? null;
  };
  return { ...state, name };
}

type Load = { items: TemplateSummary[] } | { failed: string } | null;

/**
 * The HR team's templates: name, kind, who each applies to in words, its steps, active or not, and
 * the other templates it may tie with (the tie rule of the server, worked out from the list). A table
 * from `md`, cards below it. Each name opens the editor; "New template" is for those who may edit.
 */
export function TemplateList({ tenant, readonly, platform }: { tenant: string; readonly: boolean; platform: boolean }) {
  const client = useMemo(() => processClient(tenant), [tenant]);
  const departments = useDepartments(tenant);
  const [load, setLoad] = useState<Load>(null);
  const [reads, setReads] = useState(0);
  useEffect(() => {
    let active = true;
    client.templates()
      .then(list => { if (active) setLoad({ items: list.items }); })
      .catch(cause => { if (active) setLoad({ failed: processLoadError(cause) }); });
    return () => { active = false; };
  }, [client, reads]);
  const href = (templateId: string) => hrHref({ tab: "processes", view: "template", templateId }, platform, tenant);

  let body: React.ReactNode;
  if (!load) body = <Notice>Loading templates…</Notice>;
  else if ("failed" in load) body = <div className="space-y-3">
    <Notice error>{load.failed}</Notice>
    <button type="button" className={secondary} onClick={() => setReads(count => count + 1)}>Try again</button>
  </div>;
  else if (load.items.length === 0) body = <div className="space-y-2 text-sm">
    <p className="font-medium">No templates yet.</p>
    <p className="text-ink-500">A template lists the steps of an onboarding, an offboarding or another process, and who it
      applies to. Processes start from it.</p>
  </div>;
  else {
    const ties = templateTies(load.items);
    const nameOf = (templateId: string) => load.items.find(item => item.template_id === templateId)?.name ?? "another template";
    const rows = load.items.map(item => ({
      item,
      applies: rulesText(item.rules, departments.name),
      steps: `${item.step_count} ${item.step_count === 1 ? "step" : "steps"}`,
      ties: (ties.get(item.template_id) ?? []).map(nameOf),
    }));
    body = <>
      <div className="overflow-x-auto"><table data-testid="hr-template-table" className="hidden w-full text-left text-sm md:table">
        <thead><tr>{["Template", "Kind", "Applies to", "Steps", "Status"].map(label =>
          <th className="p-2" key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.item.template_id} data-testid={`hr-template-row-${row.item.template_id}`}
          className="border-t border-ink-200 align-top">
          <td className="p-2 [overflow-wrap:anywhere]"><Link className="font-medium underline" href={href(row.item.template_id)}>
            {row.item.name}</Link></td>
          <td className="p-2">{processKindLabel(row.item.kind)}</td>
          <td className="p-2 [overflow-wrap:anywhere]">{row.applies}</td>
          <td className="p-2">{row.steps}</td>
          <td className="p-2"><Status active={row.item.is_active} ties={row.ties} /></td>
        </tr>)}</tbody>
      </table>
      <ul data-testid="hr-template-cards" className="space-y-3 md:hidden">{rows.map(row =>
        <li key={row.item.template_id} data-testid={`hr-template-card-${row.item.template_id}`} className={card}>
          <Link className="font-medium underline [overflow-wrap:anywhere]" href={href(row.item.template_id)}>{row.item.name}</Link>
          <dl className="mt-2 space-y-1 text-sm">
            <div className="flex justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Kind</dt>
              <dd className="min-w-0 text-right">{processKindLabel(row.item.kind)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="shrink-0 text-[color:var(--fs-text-secondary)]">Applies to</dt>
              <dd className="min-w-0 text-right [overflow-wrap:anywhere]">{row.applies}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Steps</dt>
              <dd className="min-w-0 text-right">{row.steps}</dd></div>
          </dl>
          <div className="mt-2"><Status active={row.item.is_active} ties={row.ties} /></div>
        </li>)}</ul></div>
    </>;
  }
  return <div data-testid="hr-template-list">
    <Panel title="Templates">
      {!readonly && <Link className={`${action} mb-4`} href={hrHref({ tab: "processes", view: "new-template" }, platform, tenant)}>
        New template</Link>}
      {body}
    </Panel>
  </div>;
}

function Status({ active, ties }: { active: boolean; ties: string[] }) {
  return <div className="space-y-1">
    <StatusLabel tone={active ? "success" : "neutral"}>{active ? "Active" : "Inactive"}</StatusLabel>
    {ties.length > 0 && <p className="flex flex-wrap items-center gap-1 [overflow-wrap:anywhere]">
      <StatusLabel tone="attention">May tie</StatusLabel> <span className="min-w-0">with {ties.join(", ")}</span></p>}
  </div>;
}
