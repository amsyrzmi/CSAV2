/**
 * The checks the Password page shows while a person types, and the length-based strength hint.
 * Pure and client-only: nothing here stores, logs or sends a password. The hint is advice; only
 * the three rules gate the Save button, and they mirror what the server enforces.
 */

/** The shortest new password the server accepts. */
export const MIN_LENGTH = 12;

export interface PasswordRules {
  length: boolean;
  different: boolean;
  match: boolean;
}

export const RULE_LABELS: Record<keyof PasswordRules, string> = {
  length: "At least 12 characters",
  different: "Different from your current password",
  match: "Matches the confirmation",
};

/** Display order of the rules. */
export const RULE_ORDER: ReadonlyArray<keyof PasswordRules> = ["length", "different", "match"];

export function checkRules(current: string, password: string, confirmation: string): PasswordRules {
  return {
    length: password.length >= MIN_LENGTH,
    // An empty new password is not "different"; there is nothing to compare yet.
    different: password !== "" && password !== current,
    match: confirmation !== "" && password === confirmation,
  };
}

export function rulesPass(rules: PasswordRules): boolean {
  return RULE_ORDER.every((key) => rules[key]);
}

/** One sentence for a screen reader: how many checks pass and, if any are left, which. */
export function announcementFor(rules: PasswordRules): string {
  const open = RULE_ORDER.filter((key) => !rules[key]);
  if (open.length === 0) return "All 3 checks pass. You can save.";
  const met = RULE_ORDER.length - open.length;
  return `${met} of ${RULE_ORDER.length} checks pass. Still needed: ${open
    .map((key) => RULE_LABELS[key])
    .join(", ")}.`;
}

export interface Strength {
  /** 0 for nothing typed, then 1 (too short) to 4 (strong). */
  level: 0 | 1 | 2 | 3 | 4;
  label: string;
}

/** Advisory only and by length alone: no dictionary, no character classes, no network. */
export function passwordStrength(password: string): Strength {
  const length = password.length;
  if (length === 0) return { level: 0, label: "" };
  if (length < MIN_LENGTH) return { level: 1, label: "Too short" };
  if (length < 16) return { level: 2, label: "Okay" };
  if (length < 20) return { level: 3, label: "Good" };
  return { level: 4, label: "Strong" };
}
