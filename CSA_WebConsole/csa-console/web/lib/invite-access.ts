import type { PresetId } from "./hr-access-api";
import { PRESET_COPY } from "./hr-access-copy";
import type { TenantRole } from "./identity-contracts";

/** What the invite form offers for "HR access": no access, or one of the B2 presets. */
export type HrAccessValue = "none" | PresetId;
export interface HrAccessOption { value: HrAccessValue; label: string; explanation: string }

const PRESET_ORDER: PresetId[] = ["staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"];

/**
 * The choices for the invite form. Without the HR package there is nothing to choose, so the
 * select is hidden. Any admin may choose no access or Staff; the other presets are the HR owner's
 * to give (the server checks this again). `role` only sets the default, not the list.
 */
export function hrAccessOptions(
  { hasHrPackage, isHrOwner }: { hasHrPackage: boolean; isHrOwner: boolean; role?: TenantRole },
): HrAccessOption[] {
  if (!hasHrPackage) return [];
  const presets = isHrOwner ? PRESET_ORDER : PRESET_ORDER.filter((id) => id === "staff");
  return [
    { value: "none", label: "No HR access", explanation: "Cannot open People & HR until someone gives them access." },
    ...presets.map((id) => ({ value: id, label: PRESET_COPY[id].label, explanation: PRESET_COPY[id].description })),
  ];
}

/** Staff can see basic details and make their own requests; a read-only member starts with nothing. */
export function defaultHrAccess(role: TenantRole): "staff" | "none" {
  return role === "read_only" ? "none" : "staff";
}

/** The same shape for an invitation, a renewal and a reset. */
export interface Delivered {
  token: string | null;
  delivery: "manual" | "email";
  delivery_note?: "mail_off" | "send_failed" | null;
}

/**
 * Mail switched off is normal, so it only says where the link goes. Only a send that failed
 * says so. A response without a note (an older server) is treated as mail switched off.
 */
export function deliveryMessage(result: Delivered, email: string, kind: "invite" | "reset" = "invite"): string {
  if (result.delivery === "email") return `${kind === "reset" ? "Reset link" : "Invitation"} emailed to ${email}`;
  return result.delivery_note === "send_failed"
    ? "We couldn't send the email. Copy this link and send it yourself."
    : `Copy this link and send it to ${email}.`;
}

/** The link to share by hand; empty when the server emailed it and kept the token to itself. */
export function inviteLink(result: Delivered, origin: string): string {
  return result.token === null ? "" : `${origin}/invite#token=${encodeURIComponent(result.token)}`;
}
