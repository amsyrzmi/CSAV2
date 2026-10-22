export function workspaceEndpoint(
  tenantId: string | null,
  threadId?: string | null,
  turns?: boolean,
): string;

export function guidanceText(key: string): string;

export type HrGuidanceAction =
  | "directory.read"
  | "training.read"
  | "training.draft"
  | "training.submit"
  | "training.withdraw"
  | "training.decide";
export function hrGuidanceText(action: HrGuidanceAction): string;
export function hrGuidancePath(
  action: HrGuidanceAction,
  tenantId: string,
  platform: boolean,
): string;
