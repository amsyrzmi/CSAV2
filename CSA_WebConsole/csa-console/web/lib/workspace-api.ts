import { api } from "@/lib/api";

export type WorkspacePageKey =
  | "workspace_home"
  | "business_orders"
  | "business_order";

export type WorkspacePageHint =
  | { page: "workspace_home" | "business_orders" }
  | {
      page: "business_order";
      entity: { kind: "local_order"; order_id: string };
    };

export type TabKey =
  | "overview"
  | "services"
  | "inbox"
  | "knowledge_base"
  | "business"
  | "hr"
  | "email"
  | "activity"
  | "chat";

export interface RecordRef {
  kind:
    | "conversation"
    | "approval"
    | "training_request"
    | "draft"
    | "order"
    | "employee";
  id: string;
  version?: number | null;
}

export interface PageContext {
  tab: TabKey;
  section?: string | null;
  record?: RecordRef | null;
}

export type ProposalKind = "email_export" | "training_submit" | "inbox_decision";

export interface ProposalCard {
  proposal_id: string;
  kind: ProposalKind;
  title: string;
  summary: string;
  fields: { label: string; value: string }[];
  expires_at: string;
  expected_version: string | null;
}

export interface ProposalOut {
  id: string;
  kind: ProposalKind;
  status: "requested" | "confirmed" | "executed" | "expired" | "cancelled";
  card: ProposalCard;
  result: Record<string, unknown> | null;
  created_at: string;
  expires_at: string;
}

export interface PersonOut {
  employee_id: string;
  display_name: string;
  job_title: string | null;
  work_email: string | null;
}

export interface MyPeopleOut {
  employee_id: string | null;
  manager: PersonOut | null;
  reason: "no_employee_link" | "no_manager" | "package_inactive" | null;
}

/** Server error codes callers branch on via `ApiError.code`. */
export type WorkspaceErrorCode =
  | "package_inactive"
  | "proposal_expired"
  | "proposal_consumed"
  | "proposal_session_mismatch"
  | "proposal_stale"
  | "planner_unavailable"
  | "planner_invalid_output";

export interface WorkspaceThread {
  id: string;
  sequence: number;
  created_at: string;
  duplicate: boolean;
}

export interface WorkspaceTurn {
  id: number;
  request_key: string;
  body: string;
  created_at: string;
  duplicate: boolean;
  result: WorkspaceResult;
}

export interface ClarificationOption {
  label: string;
  value: string;
}

/** Planner results (csa/workspace/contracts.py ProposalResult … ClarificationResult). */
export type WorkspaceActionResult =
  | { action: "proposal"; proposal: ProposalOut }
  | { action: "answer"; markdown: string }
  | {
      action: "navigate";
      tab: TabKey;
      section: string | null;
      record: RecordRef | null;
      resume: boolean;
    }
  | { action: "directory_lookup"; candidates: PersonOut[] }
  | {
      action: "clarification";
      question: string;
      options: ClarificationOption[];
    };

export type WorkspaceResult =
  | WorkspaceActionResult
  | {
      status: "completed";
      action: "page_guidance";
      page: WorkspacePageKey;
      instruction_key:
        | "workspace_home_help"
        | "business_orders_help"
        | "business_order_help";
      source: "application_guide";
      as_of: string;
    }
  | {
      status: "completed";
      action: "local_order_read";
      order: {
        resource: { kind: "local_order"; order_id: string };
        status: string;
        payment_status: string;
        total: string;
        currency: string;
        channel: string;
        external_order_id: string | null;
        external_shop_id: string | null;
        tracking_no: string | null;
        carrier: string | null;
        placed_at: string | null;
        shipped_at: string | null;
        created_at: string;
        updated_at: string;
      };
      source: "local_records";
      marketplace_sync: "unavailable";
      as_of: string;
    }
  | { status: "denied"; code: string }
  | { status: "unavailable"; code: string; retryable: boolean };

interface WorkspaceThreadPage {
  items: WorkspaceThread[];
  next_cursor: number | null;
}

interface WorkspaceTurnPage {
  items: WorkspaceTurn[];
  next_cursor: number | null;
}

/** Tenant-scoped query; platform admins must always name the tenant. */
function tenantQuery(tenantId?: string | null): string {
  return tenantId ? `?${new URLSearchParams({ client_id: tenantId })}` : "";
}

export function workspaceEndpoint(
  tenantId: string | null,
  threadId: string | null = null,
  turns = false,
): string {
  const threadPath = threadId ? `/${encodeURIComponent(threadId)}` : "";
  const suffix = turns ? "/turns" : "";
  return `/console/v1/workspace/conversations${threadPath}${suffix}${tenantQuery(tenantId)}`;
}

function request<T>(
  path: string,
  method: "GET" | "POST",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return api<T>(path, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal,
  });
}

export function listWorkspaceThreads(
  tenantId: string,
  signal?: AbortSignal,
): Promise<WorkspaceThreadPage> {
  return request(workspaceEndpoint(tenantId), "GET", undefined, signal);
}

export function listWorkspaceTurns(
  tenantId: string,
  threadId: string,
  signal?: AbortSignal,
): Promise<WorkspaceTurnPage> {
  return request(
    workspaceEndpoint(tenantId, threadId, true),
    "GET",
    undefined,
    signal,
  );
}

export function createWorkspaceThread(
  tenantId: string,
  requestKey: string,
  signal?: AbortSignal,
): Promise<WorkspaceThread> {
  return request(
    workspaceEndpoint(tenantId),
    "POST",
    { request_key: requestKey },
    signal,
  );
}

export function createWorkspaceTurn(
  tenantId: string,
  threadId: string,
  body: string,
  pageHint: WorkspacePageHint | null,
  requestKey: string,
  signal?: AbortSignal,
  pageContext?: PageContext | null,
): Promise<WorkspaceTurn> {
  return request(
    workspaceEndpoint(tenantId, threadId, true),
    "POST",
    {
      request_key: requestKey,
      body,
      ...(pageHint ? { page_hint: pageHint } : {}),
      ...(pageContext ? { page_context: pageContext } : {}),
    },
    signal,
  );
}

const PROPOSALS = "/console/v1/workspace/proposals";
const PEOPLE = "/console/v1/workspace/people";

export function createProposal(
  kind: ProposalKind,
  payload: Record<string, unknown>,
  threadId?: string,
  signal?: AbortSignal,
): Promise<ProposalOut> {
  return request(
    PROPOSALS,
    "POST",
    { kind, payload, ...(threadId ? { thread_id: threadId } : {}) },
    signal,
  );
}

export function confirmProposal(
  id: string,
  signal?: AbortSignal,
  tenantId?: string | null,
): Promise<ProposalOut> {
  return request(
    `${PROPOSALS}/${encodeURIComponent(id)}/confirm${tenantQuery(tenantId)}`,
    "POST",
    undefined,
    signal,
  );
}

export function cancelProposal(
  id: string,
  signal?: AbortSignal,
  tenantId?: string | null,
): Promise<ProposalOut> {
  return request(
    `${PROPOSALS}/${encodeURIComponent(id)}/cancel${tenantQuery(tenantId)}`,
    "POST",
    undefined,
    signal,
  );
}

export function searchPeople(
  q: string,
  signal?: AbortSignal,
): Promise<PersonOut[]> {
  return request(`${PEOPLE}?${new URLSearchParams({ q })}`, "GET", undefined, signal);
}

export function myPeople(signal?: AbortSignal): Promise<MyPeopleOut> {
  return request(`${PEOPLE}/me`, "GET", undefined, signal);
}
