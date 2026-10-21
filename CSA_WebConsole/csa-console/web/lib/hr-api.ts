import { api } from "./api";

export interface HrContext {
  employee: { tenant_id: string; employee_id: string } | null;
  client_owner: boolean;
  private_areas: ("recruitment" | "attendance" | "payroll")[];
  actions: string[];
}
export interface Employee {
  employee: { tenant_id: string; employee_id: string };
  values: { display_name?: string; job_title?: string; department?: string;
    work_email?: string; assigned_manager?: { employee_id: string } };
}
export interface Training {
  request_id: string;
  employee: { employee_id: string };
  content: { title: string; learning_objective: string };
  status: "draft" | "submitted" | "approved" | "rejected" | "withdrawn";
  version: number;
  decision?: { subject_id: string; action: string; note?: string } | null;
}
export type Domain = "recruitment" | "attendance" | "leave" | "payroll";
export interface Workflow {
  id: string;
  domain: Domain;
  created_by: string;
  content: Record<string, string | string[] | null>;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  version: number;
  interview_guide: string[];
  decision_reason: string | null;
  execution: "local_workflow_only";
}
export interface AgentSwitch { name: string; enabled: boolean }
export interface Confirmation {
  kind: "confirmation_required";
  confirmation_id: string;
  request_id: string;
  action: string;
  expected_version: number;
  expires_at: string;
  command: { action: string; expected_version: number; decision_note?: string | null };
}
export type AgentResult = { operation: string; status: string; resource_id: string } | Confirmation | { kind: "persisted_training"; request: Training } | Workflow | Employee | Training | { clarification: string; action?: null };
export interface Page<T> { items: T[]; next_cursor: string | null }

/** Keys stay with an exact operation after an uncertain response. No HR data is stored locally. */
export function hrClient(tenant: string) {
  const suffix = `?client_id=${encodeURIComponent(tenant)}`;
  const keys = new Map<string, string>();
  function write<T>(path: string, body: unknown, method = "POST"): Promise<T> {
    const serialized = JSON.stringify(body);
    const fingerprint = `${method}:${path}:${serialized}`;
    let key = keys.get(fingerprint);
    if (!key) { key = crypto.randomUUID(); keys.set(fingerprint, key); }
    return api<T>(`/console/v1/hr${path}${suffix}`, {
      method, body: serialized, headers: { "Idempotency-Key": key },
    }).then(result => { keys.delete(fingerprint); return result; });
  }
  const page = <T>(path: string, after?: string) => api<Page<T>>(
    `/console/v1/hr${path}${suffix}${after ? `&after=${encodeURIComponent(after)}` : ""}`,
  );
  return {
    operation: (body: unknown) => write<{ operation: string; resource_id: string; version: number; status: string; duplicate: boolean }>("/operations", body),
    records: <T>(area: string) => api<{ items: T[] }>(`/console/v1/hr/records/${encodeURIComponent(area)}${suffix}`),
    privateRecord: <T>(area: string, id: string) => api<{ items: T[] }>(`/console/v1/hr/private/${encodeURIComponent(area)}/${encodeURIComponent(id)}${suffix}`),
    chat: (message: string, source: "text" | "voice") => write<AgentResult>("/assistant", { message, source }),
    context: () => api<HrContext>(`/console/v1/hr/context${suffix}`),
    directory: (after?: string) => page<Employee>("/employees", after),
    training: (after?: string) => page<Training>("/training-requests", after),
    workflows: (after?: string) => page<Workflow>("/workflows", after),
    agents: () => api<AgentSwitch[]>(`/console/v1/hr/agents${suffix}`),
    draft: (content: unknown) => write("/training-requests", content),
    transition: (id: string, command: unknown) => write(`/training-requests/${encodeURIComponent(id)}/transitions`, command),
    prepare: (content: unknown) => write<Workflow>("/workflows", content),
    decide: (id: string, command: unknown) => write<Workflow>(`/workflows/${encodeURIComponent(id)}/transitions`, command),
    execute: (command: unknown) => write<AgentResult>("/agents/execute", command),
    confirm: (id: string) => write(`/confirmations/${encodeURIComponent(id)}/confirm`, {}),
    switch: (setting: AgentSwitch) => write<AgentSwitch>("/agents", setting, "PUT"),
  };
}
