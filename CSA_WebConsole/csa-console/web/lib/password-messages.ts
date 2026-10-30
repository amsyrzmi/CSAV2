/**
 * Sentences for the plain lowercase messages the password change endpoint sends. They are mapped
 * here, next to the one form that shows them; an unknown message passes through unchanged.
 */
const PASSWORD_MESSAGES: Record<string, string> = {
  "invalid current password": "That is not your current password.",
  "new password must differ from the current password":
    "Choose a new password that is different from your current one.",
  "authentication required": "Your session has ended. Sign in again.",
};

const THROTTLED = "Too many attempts. Wait a few minutes, then try again.";

/** What to show for a failed password change; `status` is the HTTP status when there was one. */
export function passwordErrorText(message: string, status?: number): string {
  if (status === 429) return THROTTLED;
  return PASSWORD_MESSAGES[message] ?? message;
}
