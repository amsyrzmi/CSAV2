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
