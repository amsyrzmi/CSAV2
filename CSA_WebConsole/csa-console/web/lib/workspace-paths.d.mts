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
