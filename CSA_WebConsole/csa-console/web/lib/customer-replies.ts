import { api, ApiError } from "./api";

export type ReplyMode = "off" | "approve" | "auto";
export interface CustomerReplies {
  mode: ReplyMode;
  brand_name: string | null;
  can_manage: boolean;
  warnings: string[];
}

export const MODE_CHOICES: { mode: ReplyMode; label: string }[] = [
  { mode: "off", label: "Off: customers get no reply" },
  { mode: "approve", label: "Staff approve each reply" },
  { mode: "auto", label: "Bot replies by itself: replies it isn't sure about still wait for staff" },
];

const PANEL_WARNINGS: Record<string, string> = {
  no_knowledge: "Your bot has nothing to answer from yet. Add files in Knowledge base.",
  base_only: "With Customer support alone, most customer questions go to your team.",
};

const REFUSALS: Record<string, string> = {
  brand_name_required: "Add your brand name before turning replies on.",
  package_suspended: "Customer support is suspended, so replies can't be changed right now.",
};

/** `clientId` is null for a tenant member and the target tenant for a platform admin. */
function path(clientId: string | null) {
  const suffix = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
  return `/console/v1/services/customer-replies${suffix}`;
}

export function readReplies(clientId: string | null, signal?: AbortSignal) {
  return api<CustomerReplies>(path(clientId), { signal });
}

export function saveReplies(clientId: string | null, body: { mode: ReplyMode; brand_name: string }) {
  return api<CustomerReplies>(path(clientId), { method: "PUT", body: JSON.stringify(body) });
}

/** Why Save is off, or null when it can go. Off never needs a name. */
export function saveBlockedReason(mode: ReplyMode, brandName: string): string | null {
  return mode !== "off" && !brandName.trim() ? "Add your brand name first." : null;
}

export function panelWarnings(warnings: string[]): string[] {
  return warnings.flatMap((code) => (PANEL_WARNINGS[code] ? [PANEL_WARNINGS[code]] : []));
}

/** One line at a time: while replies are off, nothing else matters yet. */
export function bannerLines(warnings: string[]): string[] {
  if (warnings.includes("replies_off")) return ["Your customers aren't getting replies yet."];
  if (warnings.includes("no_knowledge")) return ["Your bot has nothing to answer from yet."];
  return [];
}

export function refusalText(code: string): string | null {
  return REFUSALS[code] ?? null;
}

export function saveErrorText(cause: unknown): string {
  if (cause instanceof ApiError) return refusalText(cause.code) ?? cause.message;
  return "We couldn't save that. Try again.";
}
