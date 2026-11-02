import { api } from "./api";
import type { Member } from "./identity-contracts";

/** Mirrors csa/console/contracts.py HrOwnerPerson, HrOwnerOut and HrOwnerMoveOut. */
export type HrOwner = { user_id: string; display_name: string; usable: boolean };
export type HrOwnerState = { available: boolean; owner: HrOwner | null };
export type MoveMail = "sent" | "not_sent" | "repeat";
export type MoveResult = { owner: HrOwner; mail: MoveMail };

/**
 * Reads and moves the company's HR owner. `key` is the caller's Idempotency-Key: the panel keeps one
 * per exact request (see `moveKey`), so a retry after a lost answer returns the first receipt.
 */
export function hrOwnerClient(clientId: string) {
  return {
    read: () =>
      api<HrOwnerState>(`/console/v1/team/hr-owner?client_id=${encodeURIComponent(clientId)}`),
    move: (newOwnerId: string, expectedOwnerId: string | null, key: string) =>
      api<MoveResult>("/console/v1/team/hr-owner", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify({
          client_id: clientId,
          new_owner_id: newOwnerId,
          expected_owner_id: expectedOwnerId,
        }),
      }),
  };
}

/** Who the admin may pick: enabled, able to write, not already the owner, by name. */
export function eligibleOwners(members: Member[], current: HrOwner | null): Member[] {
  return members
    .filter(
      (member) =>
        member.is_enabled && member.role !== "read_only" && member.user_id !== current?.user_id,
    )
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
}

const refusals: Record<string, string> = {
  owner_read_only: "A read-only member can't be the HR owner. Change their role first.",
  owner_leaving: "This person is leaving, so they can't be the HR owner. Choose someone else.",
  owner_unavailable:
    "This person can't be the HR owner yet. They need to sign in and change their password first.",
  owner_unchanged: "This person is already the HR owner.",
  owner_changed: "The HR owner changed since you opened this page. Reload to see who it is now.",
  try_again: "Someone else changed HR records at the same moment. Please try again.",
};
const FALLBACK = "We couldn't change the HR owner. Please try again.";

/** The sentence for a refused move, from the server's detail code (`ApiError.code`). */
export function ownerRefusalText(cause: unknown): string {
  const code =
    typeof cause === "object" && cause !== null && "code" in cause ? String(cause.code) : "";
  return Object.hasOwn(refusals, code) ? refusals[code] : FALLBACK;
}

/**
 * What the page says when the move may or may not have happened. It points at the panel only if the
 * panel was read again; otherwise the name on screen could be the one from before the move.
 */
export function uncertainText(reloaded: boolean): string {
  return reloaded
    ? "We couldn't confirm the change. Check who the HR owner is above, then try again."
    : "We couldn't confirm the change or reload the HR owner. Reload the page before you try again.";
}

/**
 * True when the move may have happened anyway: the request never got an answer (a network error has
 * no HTTP status) or the server failed. Any 4xx is a definite refusal.
 */
export function moveUncertain(cause: unknown): boolean {
  const status =
    typeof cause === "object" && cause !== null && "status" in cause ? cause.status : undefined;
  return typeof status !== "number" || status >= 500;
}

/** Whether a refusal means the page is out of date, so the panel should read the owner again. */
export function ownerStale(cause: unknown): boolean {
  return (
    typeof cause === "object" && cause !== null && "code" in cause && cause.code === "owner_changed"
  );
}

export type HeldKey = { key: string; newOwnerId: string; expectedOwnerId: string | null };

/**
 * One key per exact request. The same person against the same expected owner reuses the key, so a
 * retry is recognised; any other request gets a new one, because the server refuses a key that is
 * reused for something else.
 */
export function moveKey(
  held: HeldKey | null,
  newOwnerId: string,
  expectedOwnerId: string | null,
  make: () => string = () => crypto.randomUUID(),
): HeldKey {
  return held && held.newOwnerId === newOwnerId && held.expectedOwnerId === expectedOwnerId
    ? held
    : { key: make(), newOwnerId, expectedOwnerId };
}

/** What the confirmation says. No old owner means there is nobody to lose the powers. */
export function confirmText({
  oldOwnerName,
  isSelf,
}: {
  oldOwnerName: string | null;
  isSelf: boolean;
}): string {
  return [
    "They get full control of who has HR access and can see sensitive employee details.",
    oldOwnerName ? `${oldOwnerName} loses HR owner powers straight away.` : null,
    "Payroll stays off until they turn it on. Everyone affected gets an email.",
    isSelf ? "You are making yourself the HR owner." : null,
  ]
    .filter(Boolean)
    .join(" ");
}

/** What the page says once the server has answered 200. */
export function moveDoneText(mail: MoveMail): string {
  if (mail === "sent") return "HR owner changed.";
  if (mail === "not_sent")
    return "HR owner changed, but we couldn't send the emails. Please tell the people affected.";
  return "HR owner changed. If you're not sure the emails went out, please tell the people affected.";
}
