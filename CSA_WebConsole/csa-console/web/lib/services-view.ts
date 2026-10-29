import type { LastChange } from "./identity-contracts";
import type { PackageStatus, Service } from "./services-api";

export interface ServiceStats {
  active: number;
  agentsOn: number;
  waiting: number;
  suspended: number;
}

/** Counts for the Services summary strip. An agent listed under two services counts once. */
export function serviceStats(services: Service[]): ServiceStats {
  const agentsOn = new Set<string>();
  const stats: ServiceStats = { active: 0, agentsOn: 0, waiting: 0, suspended: 0 };
  for (const service of services) {
    if (service.status === "active") stats.active += 1;
    else if (service.status === "requested") stats.waiting += 1;
    else if (service.status === "suspended") stats.suspended += 1;
    for (const agent of service.agents ?? []) {
      if (agent.capability?.active) agentsOn.add(agent.name);
    }
  }
  stats.agentsOn = agentsOn.size;
  return stats;
}

/** Only an active service's avatar moves; every other status shows the still first frame. */
export function rowAvatarState(status: PackageStatus): "idle" | "still" {
  return status === "active" ? "idle" : "still";
}

export type StatusTone = "ok" | "wait" | "off" | "neutral";

const LABELS: Record<PackageStatus, { text: string; tone: StatusTone }> = {
  available: { text: "Available", tone: "neutral" },
  requested: { text: "Requested", tone: "wait" },
  active: { text: "Active", tone: "ok" },
  suspended: { text: "Suspended", tone: "off" },
};

/** The status word and its tone; an unsellable service that is still available reads "Coming soon". */
export function statusLabel(service: Service): { text: string; tone: StatusTone } {
  if (!service.sellable && service.status === "available") {
    return { text: "Coming soon", tone: "neutral" };
  }
  return { ...LABELS[service.status] };
}

/** "No agents", "1 agent" or "4 agents". */
export function agentCountLabel(count: number): string {
  if (count <= 0) return "No agents";
  return count === 1 ? "1 agent" : `${count} agents`;
}

const CHANGE_WORDS: Record<LastChange["action"], string> = {
  requested: "Requested",
  activated: "Activated",
  suspended: "Suspended",
  subscribed: "Subscription turned on",
  unsubscribed: "Subscription turned off",
  tenant_switch_on: "Tenant switch turned on",
  tenant_switch_off: "Tenant switch turned off",
};

/** "20 Oct 2026" in the viewer's zone, or null for anything that is not a parseable date string. */
export function shortDate(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return null;
  return when.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/**
 * "Activated 20 Oct 2026 by a platform administrator", or null when there is nothing to say.
 * The actor is exactly the string the server sent (it already depends on who is looking);
 * nothing here looks a name up or adds one. The caller renders it as text, never as markup.
 */
export function lastChangeText(change: LastChange | null | undefined): string | null {
  if (!change) return null;
  // The payload is untyped at runtime: a null date would read 1 Jan 1970, a missing actor "by undefined".
  if (typeof change.at !== "string" || typeof change.actor !== "string" || !change.actor) return null;
  const words = Object.hasOwn(CHANGE_WORDS, change.action) ? CHANGE_WORDS[change.action] : null;
  const date = shortDate(change.at);
  if (!words || !date) return null;
  return `${words} ${date} by ${change.actor}`;
}
