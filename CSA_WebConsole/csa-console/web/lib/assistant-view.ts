/** Pure helpers behind the assistant dock and proposal card (no React, no I/O). */
import type { Me } from "@/lib/identity-contracts";
import { formatWhen } from "@/lib/email-recipients";
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
  "subscriptions",
  "activity",
  "chat",
];

/**
 * The tenant the dock talks to, or null when the dock must not render: the gateway's
 * assistant flag is off, read-only members never see it, and platform admins only on
 * pages that name `?client_id=`.
 */
export function dockTenant(me: Me, clientIdParam: string | null): string | null {
  if (!me.workspace_assistant) return null; // WORKSPACE_ASSISTANT_ENABLED is off
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

/** How much of the originating message a follow-up turn repeats. */
const ORIGIN_CHARS = 1000;
/** WorkspaceTurnInput.body max_length on the gateway. */
const BODY_CHARS = 4000;

/**
 * A planner-written question can quote mailbox data, so its addresses never travel in a turn:
 * the gateway accepts an address the current message carries as one the user wrote.
 */
function maskAddresses(text: string): string {
  return text.replace(/[^\s@<>]+@[^\s@<>]+/g, "[address]");
}

function originOf(text: string, limit: number = ORIGIN_CHARS): string {
  const trimmed = text.trim();
  return trimmed.length > limit ? `${trimmed.slice(0, limit)}…` : trimmed;
}

/**
 * A message for the planner and the request it continues. `origin` is kept in the console's
 * memory, unredacted: stored turn bodies have addresses masked, so a later follow-up repeats
 * the console's copy instead.
 */
export interface Outgoing {
  text: string;
  origin: string;
}

/** The request a turn's result continues: the console's copy, else the stored (redacted) body. */
export function originFor(turn: WorkspaceTurn, origins: Record<string, string>): string {
  return origins[turn.request_key] ?? turn.body;
}

/** Follow-up turn text when the user picks a directory candidate from `origin`'s result. */
export function candidateFollowUp(person: PersonOut, origin: string): string {
  return `Use employee ${person.employee_id} for: ${originOf(origin)}`;
}

/** A picked candidate's turn; the follow-up itself is the request later turns continue. */
export function candidateAnswer(person: PersonOut, origin: string): Outgoing {
  const text = candidateFollowUp(person, origin);
  return { text, origin: text };
}

/**
 * Follow-up turn text answering a clarification (a clicked option or a typed reply), carrying
 * its question and request. The request is cut to ORIGIN_CHARS, and further when the whole turn
 * would pass the gateway's body limit; with no room left the answer goes alone.
 */
export function clarificationAnswer(
  answer: ClarificationOption | string,
  question: string,
  origin: string,
): string {
  const value = typeof answer === "string" ? answer.trim() : answer.value;
  const head = `${value}\n(Answer to “${maskAddresses(question)}” for: `;
  const room = BODY_CHARS - head.length - ")".length - "…".length;
  if (room < 1) return value;
  return `${head}${originOf(origin, Math.min(ORIGIN_CHARS, room))})`;
}

const ANSWERED = "; answered “";

/**
 * The request with one more answer folded in, at most ORIGIN_CHARS where it can be: the first
 * request is kept, the oldest answers are dropped first, and the first request is shortened
 * only when the newest answer would not fit beside it.
 */
function foldAnswer(origin: string, question: string, answer: string): string {
  const whole = origin.trim();
  const cut = whole.indexOf(ANSWERED);
  let head = cut < 0 ? whole : whole.slice(0, cut);
  const answers = cut < 0 ? [] : whole.slice(cut + ANSWERED.length).split(ANSWERED);
  answers.push(`${maskAddresses(question)}”: ${answer.trim()}`);
  const joined = () => [head, ...answers].join(ANSWERED);
  while (answers.length > 1 && joined().length > ORIGIN_CHARS) answers.shift();
  const room = ORIGIN_CHARS - (joined().length - head.length) - "…".length;
  if (joined().length > ORIGIN_CHARS) head = `${head.slice(0, Math.max(room, 100))}…`;
  return joined();
}

/**
 * The answer's turn; later clarifications continue the request with this answer folded in. A
 * clicked option's value is planner-written, so its addresses are masked in the fold; a typed
 * answer is the user's own and stays as typed.
 */
function answered(value: string, question: string, origin: string, clicked: boolean): Outgoing {
  return {
    text: clarificationAnswer(value, question, origin),
    origin: foldAnswer(origin, question, clicked ? maskAddresses(value) : value),
  };
}

/** The turn for a clicked clarification option. */
export function optionAnswer(
  option: ClarificationOption,
  question: string,
  origin: string,
): Outgoing {
  return answered(option.value, question, origin, true);
}

/**
 * The turn for text typed into the dock. When the thread's latest result is a clarification it
 * is still unanswered, so the text answers it exactly as a clicked option would; otherwise the
 * text is a new request.
 */
export function typedMessage(
  text: string,
  turns: WorkspaceTurn[],
  origins: Record<string, string>,
): Outgoing {
  const last = turns.at(-1);
  if (last && "action" in last.result && last.result.action === "clarification")
    return answered(text, last.result.question, originFor(last, origins), false);
  return { text, origin: text };
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
    case "email_export":
      // The receipt is {draft_id, status, gmail_draft_id}: no subject or address is stored.
      return "Saved to your Gmail drafts. Nothing was sent.";
    case "email_send": {
      // csa/workspace/executors.py EmailSendExecutor: outcome, time and Gmail's id only.
      const sentAt = text(field(result, "sent_at"));
      return field(result, "outcome") === "sent" && sentAt
        ? `Sent at ${formatWhen(sentAt)}.`
        : "Sent from your Gmail.";
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
    case "subscription_action": {
      // csa/subscriptions/executor.py receipt: subscription_id, status, version, event_id.
      const status = text(field(result, "status"));
      return status ? `Subscription updated. It is now ${status}.` : "Subscription updated.";
    }
  }
}
