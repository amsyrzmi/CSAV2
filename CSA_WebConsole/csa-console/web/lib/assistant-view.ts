/** Pure helpers behind the assistant dock and proposal card (no React, no I/O). */
import type { Me } from "@/lib/identity-contracts";
import { navigationFor } from "@/lib/navigation";
import type { PackageState } from "@/lib/services-api";
import type {
  ClarificationOption,
  PageContext,
  PersonOut,
  ProposalOut,
  TabKey,
  WorkspaceResult,
  WorkspaceTurn,
} from "@/lib/workspace-api";

const TABS: readonly TabKey[] = [
  "overview",
  "services",
  "inbox",
  "knowledge_base",
  "business",
  "hr",
  "email",
  "activity",
  "chat",
];

/**
 * The tenant the dock talks to, or null when the dock must not render: read-only
 * members never see it, and platform admins only on pages that name `?client_id=`.
 */
export function dockTenant(me: Me, clientIdParam: string | null): string | null {
  if (me.role === "platform_admin") return clientIdParam?.trim() || null;
  if (me.role === "tenant_admin" || me.role === "tenant_staff")
    return me.active_client_id || null;
  return null;
}

/** Route for a tab through the shell navigation; null when the tab is not offered. */
export function tabHref(
  tab: TabKey,
  me: Me,
  packages: PackageState[],
  tenantId: string | null,
  section: string | null = null,
): string | null {
  const link = navigationFor(me, packages).find((item) => item.key === tab);
  if (!link) return null;
  const query = new URLSearchParams();
  if (section) query.set("section", section);
  // Platform admins keep the tenant in the URL so the dock stays available.
  if (me.role === "platform_admin" && tenantId) query.set("client_id", tenantId);
  return query.size ? `${link.href}?${query}` : link.href;
}

/** The tab whose navigation link points at this pathname, if any. */
export function tabForPath(
  pathname: string,
  me: Me,
  packages: PackageState[],
): TabKey | null {
  const link = navigationFor(me, packages).find((item) => item.href === pathname);
  const key = link?.key;
  return TABS.find((tab) => tab === key) ?? null;
}

export function tabLabel(tab: TabKey, me: Me, packages: PackageState[]): string {
  return navigationFor(me, packages).find((item) => item.key === tab)?.label ?? tab;
}

/** What the tab published, else the bare tab derived from the route. */
export function effectiveContext(
  published: PageContext | null,
  pathTab: TabKey | null,
): PageContext | null {
  return published ?? (pathTab ? { tab: pathTab } : null);
}

/**
 * Context for a resumed turn after a navigate: the target tab's published context once
 * it is there, otherwise null (keep waiting); `fallback` forces the bare tab and section.
 */
export function resumeContext(
  published: PageContext | null,
  target: { tab: TabKey; section: string | null },
  fallback: boolean,
): PageContext | null {
  if (published?.tab === target.tab) return published;
  if (!fallback) return null;
  return target.section ? { tab: target.tab, section: target.section } : { tab: target.tab };
}

/** Follow-up turn text when the user picks a directory candidate. */
export function candidateFollowUp(person: PersonOut): string {
  return `Use employee ${person.employee_id}`;
}

const guidance: Record<string, string> = {
  workspace_home_help:
    "Use this internal workspace for staff guidance and authorized local business reads. Customer sandbox conversations are separate.",
  business_orders_help:
    "Business order views read tenant-scoped local records only. They do not query a marketplace or change an order.",
  business_order_help:
    "This order lookup reads one tenant-scoped local record. Confirm its source and local update time before relying on it.",
};

type OrderRead = Extract<WorkspaceResult, { action: "local_order_read" }>;

export type ResultView =
  | { kind: "notice"; error: boolean; text: string }
  | { kind: "guidance"; text: string; source: string; asOf: string }
  | { kind: "order"; result: OrderRead }
  | { kind: "proposal"; proposal: ProposalOut }
  | { kind: "answer"; text: string }
  | { kind: "navigate"; tab: TabKey; resume: boolean }
  | { kind: "directory"; candidates: PersonOut[] }
  | { kind: "clarification"; question: string; options: ClarificationOption[] };

export function resultView(result: WorkspaceResult): ResultView {
  if ("status" in result && result.status === "denied")
    return {
      kind: "notice",
      error: true,
      text:
        result.code === "resource_unavailable"
          ? "The requested record is unavailable in this tenant."
          : "This request could not be authorized.",
    };
  if ("status" in result && result.status === "unavailable")
    return { kind: "notice", error: true, text: "The assistant could not complete this request." };
  switch (result.action) {
    case "page_guidance":
      return {
        kind: "guidance",
        text: guidance[result.instruction_key] ?? "No guidance is available for this page.",
        source: result.source,
        asOf: result.as_of,
      };
    case "local_order_read":
      return { kind: "order", result };
    case "proposal":
      return { kind: "proposal", proposal: result.proposal };
    case "answer":
      return { kind: "answer", text: result.markdown };
    case "navigate":
      return { kind: "navigate", tab: result.tab, resume: result.resume };
    case "directory_lookup":
      return result.candidates.length
        ? { kind: "directory", candidates: result.candidates }
        : { kind: "notice", error: false, text: "No matching colleague was found." };
    case "clarification":
      return { kind: "clarification", question: result.question, options: result.options };
  }
}

/** Proposals in these turns that are still awaiting a decision at `now`. */
export function openProposals(
  turns: WorkspaceTurn[],
  outcomes: Record<string, ProposalOut>,
  now: number = Date.now(),
): ProposalOut[] {
  const found = new Map<string, ProposalOut>();
  for (const turn of turns) {
    if (!("action" in turn.result) || turn.result.action !== "proposal") continue;
    const proposal = outcomes[turn.result.proposal.id] ?? turn.result.proposal;
    if (proposal.status === "requested" && Date.parse(proposal.expires_at) > now)
      found.set(proposal.id, proposal);
  }
  return [...found.values()];
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function field(record: unknown, key: string): unknown {
  return typeof record === "object" && record !== null && key in record
    ? (record as Record<string, unknown>)[key]
    : undefined;
}

/** One line describing a settled proposal, from the executor's receipt where possible. */
export function resultSummary(proposal: ProposalOut): string {
  if (proposal.status === "cancelled") return "Cancelled. Nothing was changed.";
  if (proposal.status === "expired") return "This proposal expired. Nothing was changed.";
  if (proposal.status === "requested") return "Waiting for your confirmation.";
  if (proposal.status === "confirmed") return "Confirmed. The action is being completed.";
  const result = proposal.result;
  switch (proposal.kind) {
    case "email_export": {
      const subject = text(field(result, "subject"));
      const recipient = text(field(result, "recipient"));
      return subject && recipient
        ? `Saved “${subject}” to your Gmail drafts, addressed to ${recipient}. Nothing was sent.`
        : "Saved to your Gmail drafts. Nothing was sent.";
    }
    case "training_submit": {
      const request = field(result, "request");
      const title = text(field(request, "title"));
      const status = text(field(request, "status"));
      return title
        ? `Submitted “${title}”${status ? ` (now ${status.replaceAll("_", " ")})` : ""}.`
        : "Training request submitted.";
    }
    case "inbox_decision": {
      // csa/console/inbox.py InboxResult: an expired or already-handled approval is not an error.
      const queued = field(result, "queued") === true;
      switch (field(result, "outcome")) {
        case "expired":
          return `Approval expired.${queued ? " Follow-up reply queued." : " No reply queued."}`;
        case "already_handled":
          return "Already handled. Nothing was changed.";
        default:
          return `Your decision on the held reply was recorded.${queued ? " Reply queued for delivery." : ""}`;
      }
    }
  }
}
