/** Mirrors CSAV2 Pydantic identity contracts. No legacy actor/role headers exist. */
export type TenantRole = "tenant_admin" | "tenant_staff" | "read_only";
export type ConsoleRole = "platform_admin" | TenantRole;
export interface Membership {
  client_id: string;
  role: TenantRole;
}
export interface Me {
  user: {
    id: string;
    email: string;
    display_name: string;
    is_platform_admin: boolean;
    must_change_password: boolean;
  };
  memberships: Membership[];
  active_client_id: string | null;
  role: ConsoleRole | null;
  csrf_token: string;
}
export interface TokenInfo {
  email: string;
  display_name: string;
  role: ConsoleRole | null;
  client_id: string | null;
  expires_at: string;
  kind: "invite" | "reset";
}
export interface Client {
  id: string;
  slug: string;
  name: string;
  timezone: string;
  status: string;
}
export interface KbFile {
  id: string;
  name: string;
  media_type: string;
  size_bytes: number;
  document_id: string | null;
  created_at: string;
}
export interface KbDocument {
  id: string;
  title: string;
  source_uri: string | null;
  category: string | null;
  language: string;
  version: number;
  is_active: boolean;
  file_name: string | null;
  chunk_count: number;
  kind: string;
  embed_model: string | null;
  created_at: string;
  updated_at: string;
}
export interface KbJob {
  id: string;
  source: string;
  status: string;
  files: { name: string; file_id: string | null }[];
  attempts: number;
  error: string | null;
  meta: { results: {
    file: string; status: string; error: string | null;
    document_id: string | null; chunks: number | null; version: number | null;
  }[] };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface KbGap {
  id: string;
  query: string;
  hits: number;
  first_seen: string;
  last_seen: string;
  resolved: boolean;
}
export interface Member {
  user_id: string;
  client_id: string;
  email: string;
  display_name: string;
  role: TenantRole;
  is_enabled: boolean;
}
export interface IssuedInvitation {
  id: string;
  token: string;
  expires_at: string;
  delivery: "manual";
}
export interface Capability {
  name: string;
  is_enabled: boolean;
  client_enabled: boolean;
  active: boolean;
  capability_kind: string;
  circuit_state: string;
  fail_count: number;
  updated_at: string;
}
export interface Agent {
  name: string;
  title: string;
  capability: Capability | null;
  implementation: "local" | "prepared_only" | "not_implemented";
  readiness: "available" | "setup_required" | "configured_unverified" | "reindex_required";
  detail: string;
  required_system: boolean;
  specialized: boolean;
  can_view_specialized: boolean;
  can_subscribe: boolean;
  can_toggle: boolean;
  embedding_model: string | null;
  compatible_chunks: number | null;
  incompatible_chunks: number | null;
}
export interface AgentRun {
  id: number;
  trace_id: string;
  conversation_id: string | null;
  agent: string;
  ok: boolean;
  status: string;
  error_code: string | null;
  error_category: string | null;
  latency_ms: number | null;
  created_at: string;
}
export interface RunPage {
  items: AgentRun[];
  next_before_id: number | null;
}
export interface RuntimeOverview {
  runs: number;
  succeeded: number;
  failed: number;
  last_run_at: string | null;
  window_hours: number;
}
export interface RuntimeEvent {
  id: number;
  node: string;
  agent: string | null;
  status: string;
  duration_ms: number;
  created_at: string;
}

export function homeFor(me: Me): string {
  return me.user.must_change_password
    ? "/change-password"
    : me.role === "platform_admin"
      ? "/"
      : "/portal";
}
export const roleLabels: Record<ConsoleRole, string> = {
  platform_admin: "Platform admin",
  tenant_admin: "Tenant admin",
  tenant_staff: "Tenant staff",
  read_only: "Read only",
};
