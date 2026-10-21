import { api } from "@/lib/api";
import { workspaceEndpoint } from "@/lib/workspace-paths.mjs";

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

export type WorkspaceResult =
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
  pageHint: WorkspacePageHint,
  requestKey: string,
  signal?: AbortSignal,
): Promise<WorkspaceTurn> {
  return request(
    workspaceEndpoint(tenantId, threadId, true),
    "POST",
    { request_key: requestKey, body, page_hint: pageHint },
    signal,
  );
}
