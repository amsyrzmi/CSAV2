"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { hrClient, type Workflow } from "@/lib/hr-api";
import { payrollRequestOptions } from "@/lib/hr-pickers";
import { action, Notice, Panel } from "../ui";
import { dates, Form, ref, regionField, value, type Spec } from "./HrForm";
import type { HrTabProps } from "./HrShell";
import { useHrRunner, useHrSave } from "./useHrRunner";

type Snapshot = { case_id: string; digest: string; status: string; version: number; payload: { currency: string; net_total: string;
  lines: { employee_id: string; net: string; amounts: { gross: string; pcb: string; employee_epf: string; employee_socso: string; employee_eis: string } }[] } };
/** The same 20-page bound as the people picker. */
const MAX_PAGES = 20;

/** Payroll: reviewed contribution rules, the payroll snapshot, and the private review by a distinct reviewer. */
export function PayrollTab({ tenant, readonly, refreshContext }: HrTabProps) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const runner = useHrRunner();
  const { busy, error, message, run, setError, setMessage } = runner;
  const ruleId = useRef(crypto.randomUUID());
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [requests, setRequests] = useState<Workflow[]>([]);
  const load = useCallback(async () => {
    const items: Workflow[] = [];
    let after: string | undefined;
    for (let count = 0; count < MAX_PAGES; count += 1) {
      const page = await client.workflows(after);
      items.push(...page.items);
      if (!page.next_cursor) break;
      after = page.next_cursor;
    }
    setRequests(items);
  }, [client]);
  const save = useHrSave(runner, client, load, refreshContext);
  useEffect(() => { void run(load); }, [run, load]);
  const requestOptions = payrollRequestOptions(requests);
  // With no payroll request to list (none yet, or no workflow read), the typed reference stays usable.
  const caseField = (label: string, typed: string): Spec => requestOptions.length ? { name: "case", label, options: requestOptions } : ref("case", typed);
  return <div className="space-y-5" aria-busy={busy}>
    {message && <Notice>{message}</Notice>}{error && <Notice error>{error}. Check saved records before retrying an uncertain write.</Notice>}
    <Notice>Financial actions are manual. Contribution tables and employment categories need qualified review. PCB values come from a separately reviewed calculation. This workspace does not send payments or statutory filings.</Notice>
    <Form title="Reviewed contribution rules" disabled={busy || readonly} fields={[regionField, ref("entity", "Legal entity reference"), ref("category", "Employment category"),
      { name: "cycle", label: "Pay cycle", options: [["weekly", "Weekly"], ["fortnightly", "Fortnightly"], ["monthly", "Monthly"]] }, ...dates,
      ref("source", "Official source / reviewed version reference"), ref("qualification", "Reviewer qualification evidence"), { name: "file", label: "Reviewed contribution bands CSV", type: "file" }]}
      save={async data => {
        const file = data.get("file"); if (!(file instanceof File) || file.size > 200000) { setError("Choose a reviewed CSV smaller than 200 KB."); return; }
        const bands: Record<string, object[]> = { epf: [], socso: [], eis: [] };
        for (const row of (await file.text()).trim().split(/\r?\n/).slice(1).filter(Boolean)) {
          const [scheme, above, through, employee, employer] = row.split(",").map(item => item.trim());
          if (!Object.hasOwn(bands, scheme)) { setError("Each scheme must be epf, socso or eis."); return; }
          bands[scheme].push({ above, through, employee, employer });
        }
        await save({ operation: "payroll.rules", rule_id: ruleId.current, region: value(data, "region"), legal_entity_reference: value(data, "entity"), employment_category: value(data, "category"), cycle: value(data, "cycle"),
          effective_from: value(data, "starts_on"), effective_until: value(data, "ends_on"), source_reference: value(data, "source"), reviewer_qualification_reference: value(data, "qualification"), ...bands }, () => { ruleId.current = crypto.randomUUID(); });
      }}><p className="text-sm sm:col-span-2">CSV columns: scheme, above, through, employee, employer. Use contiguous wage bands and plain decimal amounts. Rules are immutable; save a new reviewed version for changes.</p></Form>
    <Form title="Payroll snapshot" disabled={busy || readonly} fields={[caseField("Payroll request (create under Requests)", "Payroll request reference (create under Requests)"), { name: "file", label: "Reviewed payroll lines CSV", type: "file" }]}
      save={async data => {
        const file = data.get("file"); if (!(file instanceof File) || file.size > 64000) { setError("Choose a reviewed CSV smaller than 64 KB."); return; }
        const lines = (await file.text()).trim().split(/\r?\n/).slice(1).filter(Boolean).map(row => {
          const [employee_id, gross, epf_wage, socso_wage, eis_wage, reviewed_pcb, reviewed_other_deductions, tax_review_reference] = row.split(",").map(item => item.trim());
          return { employee_id, gross, epf_wage, socso_wage, eis_wage, reviewed_pcb, reviewed_other_deductions, tax_review_reference };
        }); await save({ operation: "payroll.snapshot", case_id: value(data, "case"), lines });
      }}><p className="text-sm sm:col-span-2">CSV columns: employee_id, gross, epf_wage, socso_wage, eis_wage, reviewed_pcb, reviewed_other_deductions, tax_review_reference.</p></Form>
    <Form title="Private payroll review" disabled={busy} fields={[caseField("Payroll request", "Payroll request reference")]}
      save={data => run(async () => setSnapshot((await client.privateRecord<Snapshot>("payroll_snapshot", value(data, "case"))).items[0]))} />
    {snapshot && <Panel title={`Payroll ${snapshot.status}`}><p className="mb-4">MYR net total: {snapshot.payload.net_total}</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{["Employee", "Gross", "EPF", "SOCSO", "EIS", "PCB", "Net"].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead><tbody>
        {snapshot.payload.lines.map(line => <tr key={line.employee_id}>{[line.employee_id, line.amounts.gross, line.amounts.employee_epf, line.amounts.employee_socso, line.amounts.employee_eis, line.amounts.pcb, line.net].map((item, index) => <td className="p-2" key={index}>{item}</td>)}</tr>)}
      </tbody></table></div>
      {snapshot.status === "prepared" && <button className={`${action} mt-4`} disabled={busy || readonly} onClick={() => void run(async () => {
        await client.decide(snapshot.case_id, { action: "approve", expected_version: 0, payroll_digest: snapshot.digest }); setSnapshot(null); setMessage("Exact payroll snapshot approved locally. No payment or filing was sent.");
      })}>Confirm reviewed amounts as a distinct reviewer</button>}
      {snapshot.status === "approved" && <Form title="Payroll reversal" disabled={busy || readonly} fields={[ref("reason", "Reason for reversing this local run")]}
        save={data => save({ operation: "payroll.reverse", case_id: snapshot.case_id, expected_version: snapshot.version, reason: value(data, "reason") }, () => setSnapshot(null))} />}
    </Panel>}
  </div>;
}
