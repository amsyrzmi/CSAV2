import { api, post } from "./api";
import type { Agent } from "./identity-contracts";

export type AgentRow = Agent;
export type PackageStatus = "available" | "requested" | "active" | "suspended";
export interface PackageState { key: string; status: PackageStatus }
export interface Service {
  key: string;
  title: string;
  summary: string;
  kind: "base" | "addon" | "standalone";
  requires: string[];
  sellable: boolean;
  tabs: string[];
  status: PackageStatus;
  reference: string | null;
  note: string | null;
  requested_at: string | null;
  activated_at: string | null;
  requested_by: string | null;
  activated_by: string | null;
  agents: AgentRow[];
  inner_switch: boolean | null;
  consistent: boolean;
  inconsistencies: string[];
  can_request: boolean;
  can_activate: boolean;
  can_suspend: boolean;
}

/** `clientId` is null for a tenant member and the target tenant for a platform admin. */
export function servicesClient(clientId: string | null) {
  const suffix = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
  const path = (rest = "") => `/console/v1/services${rest}${suffix}`;
  const keyed = (key: string, action: string) => path(`/${encodeURIComponent(key)}/${action}`);
  return {
    list: () => api<Service[]>(path()),
    active: () => api<{ packages: PackageState[] }>(path("/active")).then(r => r.packages),
    request: (key: string) => post<Service>(keyed(key, "request"), {}),
    activate: (key: string, reference: string, note?: string | null) =>
      post<Service>(keyed(key, "activate"), { reference, note: note ?? null }),
    suspend: (key: string, note?: string | null) =>
      post<Service>(keyed(key, "suspend"), { note: note ?? null }),
  };
}
