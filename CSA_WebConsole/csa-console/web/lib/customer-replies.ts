import { api, ApiError } from "./api";

export type ReplyMode = "off" | "approve" | "auto";
/** The customer bot's voice: wording only, on top of a default guide that always applies. */
export interface PersonaFields {
  persona_name: string;
  brand_tone: string;
  intro_line: string;
}
export interface CustomerReplies {
  mode: ReplyMode;
  brand_name: string | null;
  persona_name: string | null;
  brand_tone: string | null;
  intro_line: string | null;
  can_manage: boolean;
  warnings: string[];
}

export const MODE_CHOICES: { mode: ReplyMode; label: string }[] = [
  { mode: "off", label: "Off: customers get no reply" },
  {
    mode: "approve",
    label: "Staff approve each reply: a draft waits up to 24 hours, then your team gets a ticket and the customer is told you'll follow up",
  },
  { mode: "auto", label: "Bot replies by itself: when it can't answer, it hands over to your team" },
];

const PANEL_WARNINGS: Record<string, string> = {
  no_knowledge: "Your bot has nothing to answer from yet. Add files in Business knowledge.",
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

export function saveReplies(
  clientId: string | null,
  body: { mode: ReplyMode; brand_name: string } & Partial<PersonaFields>,
) {
  return api<CustomerReplies>(path(clientId), { method: "PUT", body: JSON.stringify(body) });
}

const PERSONA_KEYS: (keyof PersonaFields)[] = ["persona_name", "brand_tone", "intro_line"];

/**
 * Only the persona fields the Admin changed: a field left alone is left out of the save, so the
 * server keeps it, and a cleared one is sent as an empty string (back to the default). A read
 * that predates a field (undefined) counts as empty.
 */
export function changedPersona(
  read: Partial<Record<keyof PersonaFields, string | null | undefined>>,
  edited: PersonaFields,
): Partial<PersonaFields> {
  const changed: Partial<PersonaFields> = {};
  for (const key of PERSONA_KEYS) {
    const next = edited[key].trim();
    if (next !== (read[key] ?? "").trim()) changed[key] = next;
  }
  return changed;
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
  if (cause instanceof ApiError) {
    if (cause.status === 422 && !refusalText(cause.code)) {
      return "One line each: brand name up to 120 characters, persona name 60, tone 200, greeting 300.";
    }
    return refusalText(cause.code) ?? cause.message;
  }
  return "We couldn't save that. Try again.";
}
