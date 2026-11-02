/**
 * Plain rules for the Team page (components/console/TeamManagement.tsx). No imports, so node
 * --test can load it. Nothing here talks to the server; the sign-out sentences state what the
 * server does today (csa/console/identity.py `patch_member` ends every member change by revoking
 * that member's sessions for the company, and `revoke_user_sessions` revokes all of them).
 */

/** What a member card shows as a status. */
export type MemberStatus = "active" | "disabled" | "invited";
export type InvitationStatus = "Accepted" | "Revoked" | "Expired" | "Pending";
export type ChipTone = "neutral" | "info" | "success" | "attention";

function firstCharacter(word: string): string {
  return String.fromCodePoint(word.codePointAt(0) ?? 63).toUpperCase();
}

/**
 * The letters in a member's avatar: the first letter of the first two words of the name, else
 * the first letter of the email, else a question mark. Whole characters, never half a pair.
 */
export function initials(name: string, email: string): string {
  const words = name.split(/\s+/).filter(Boolean).slice(0, 2);
  if (words.length > 0) return words.map(firstCharacter).join("");
  const mail = email.trim();
  return mail ? firstCharacter(mail) : "?";
}

/**
 * Active or disabled from the membership flag. A person who has not accepted yet (`accepted`
 * false) is invited. The members list only holds accepted members today, so it never sets it.
 */
export function memberStatus(member: { is_enabled: boolean; accepted?: boolean }): MemberStatus {
  if (member.accepted === false) return "invited";
  return member.is_enabled ? "active" : "disabled";
}

/** The plain word and tone for an invitation's status; the original word stays in its details. */
export function invitationChip(status: InvitationStatus): { label: string; tone: ChipTone } {
  switch (status) {
    case "Accepted":
      return { label: "Joined", tone: "success" };
    case "Revoked":
      return { label: "Cancelled", tone: "neutral" };
    case "Expired":
      return { label: "Expired", tone: "attention" };
    default:
      return { label: "Waiting", tone: "info" };
  }
}

function called(name: string, email?: string): string {
  return name.trim() || email?.trim() || "This person";
}

/**
 * What a role change or Disable access does, in the words of the confirm step. The server
 * revokes that member's sessions for this company in the same transaction, so they are signed
 * out of this company straight away. It says nothing about other companies. When the person
 * changing the access is the member themselves (`self`), the sentence speaks to them.
 */
export function signOutSentence(name: string, change: "role" | "disable", email?: string, self = false): string {
  const who = self ? "You" : called(name, email);
  if (change === "role") return `${who} will be signed out of this company straight away and will need to sign in again.`;
  return self
    ? "You will be signed out of this company straight away and will not be let back in until another administrator enables your access again."
    : `${who} will be signed out of this company straight away and will not be let back in until you enable access again.`;
}

/** The message after Revoke sessions: the server ends every session that person has. */
export function revokedMessage(name: string, email?: string): string {
  return `${called(name, email)} has been signed out everywhere and will need to sign in again.`;
}
