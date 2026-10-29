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
