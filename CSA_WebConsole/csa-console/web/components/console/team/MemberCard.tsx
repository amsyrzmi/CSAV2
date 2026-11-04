"use client";

import { useEffect, useRef, useState } from "react";
import type { Member, TenantRole } from "@/lib/identity-contracts";
import { roleLabels } from "@/lib/identity-contracts";
import { memberStatus, revokedMessage, signOutSentence, type MemberStatus } from "@/lib/team-view";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { DetailsRow } from "../kit";
import { action, field, Notice, secondary } from "../ui";
import MemberAvatar from "./MemberAvatar";

const ROLES: TenantRole[] = ["tenant_admin", "tenant_staff", "read_only"];
const STATUS: Record<MemberStatus, { label: string; tone: StatusTone }> = {
  active: { label: "Active", tone: "success" },
  disabled: { label: "Disabled", tone: "attention" },
  invited: { label: "Invited", tone: "neutral" },
};

/** The one question on a card at a time: a new role, or switching access off. */
type Pending = { kind: "role"; role: TenantRole } | { kind: "disable" };

/**
 * One member as a card: initials, name, email, role and status up front. Everything that
 * changes access sits behind the arrow, with the original labels and wording. A role change
 * and Disable access ask first, and say that the person is signed out (the server revokes
 * their sessions for this company whenever a member is changed).
 */
export default function MemberCard({
  member,
  platform,
  selfId,
  busy,
  onChange,
  onReset,
  onRevoke,
}: {
  member: Member;
  platform: boolean;
  /** The signed-in user's id; their own card says "You will be signed out". */
  selfId?: string;
  busy: boolean;
  /** Sends the change; resolves to the server's message when it refuses, else null. */
  onChange: (member: Member, change: { role?: TenantRole; is_enabled?: boolean }) => Promise<string | null>;
  onReset: (member: Member) => void;
  onRevoke: (member: Member) => Promise<string | null>;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const selectRef = useRef<HTMLSelectElement>(null);
  const accessRef = useRef<HTMLButtonElement>(null);
  const revokeRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Where focus goes when the question closes: the control that opened it, which stays mounted.
  const returnTo = useRef<HTMLElement | null>(null);
  const [, wake] = useState(0);
  const testId = `team-card-${member.user_id}`;
  const status = STATUS[memberStatus(member)];

  // Runs after every render on purpose: a control that was disabled while a change was in flight
  // loses focus, and only once the page has re-enabled it can it take focus back.
  useEffect(() => {
    if (busy || !returnTo.current) return;
    const target = returnTo.current;
    returnTo.current = null;
    if (target.isConnected && !target.hasAttribute("disabled")) target.focus();
  });
  useEffect(() => {
    // Disable access is a deliberate click, so the safe answer takes focus. A role is chosen in
    // the select, which keeps focus so the arrow keys can keep moving through the roles.
    if (pending?.kind === "disable") cancelRef.current?.focus();
  }, [pending?.kind]);

  /** Asks for focus once the page is quiet again; the bump makes sure a render follows. */
  function giveFocusTo(target: HTMLElement | null) {
    returnTo.current = target;
    wake((value) => value + 1);
  }
  function trigger(question: Pending | null): HTMLElement | null {
    return question?.kind === "role" ? selectRef.current : accessRef.current;
  }
  function cancel() {
    if (busy) return;
    giveFocusTo(trigger(pending));
    setPending(null);
    setError("");
  }
  async function confirm() {
    if (!pending || busy) return;
    setError("");
    const failure = await onChange(
      member,
      pending.kind === "role" ? { role: pending.role } : { is_enabled: false },
    );
    if (failure) {
      setError(failure);
      giveFocusTo(cancelRef.current);
      return;
    }
    giveFocusTo(trigger(pending));
    setPending(null);
  }
  async function enable() {
    if (busy) return;
    setNotice("");
    setError("");
    const failure = await onChange(member, { is_enabled: true });
    if (failure) setError(failure);
    giveFocusTo(accessRef.current);
  }
  async function revoke() {
    if (busy) return;
    setPending(null);
    setNotice("");
    setError("");
    const failure = await onRevoke(member);
    if (failure) setError(failure);
    else setNotice(revokedMessage(member.display_name, member.email));
    giveFocusTo(revokeRef.current);
  }

  const question = pending && (
    <div
      id={`${testId}-confirm`}
      role="group"
      aria-label={pending.kind === "role" ? "Confirm role change" : "Confirm disable access"}
      data-testid={`team-confirm-${member.user_id}`}
      className="mt-3 rounded-[var(--fs-radius-control)] border border-[color:var(--fs-primary)] bg-[var(--fs-surface-blue)] p-3"
    >
      <p className="text-sm font-medium">
        {pending.kind === "role"
          ? `Change ${member.display_name} to ${roleLabels[pending.role]}?`
          : `Disable access for ${member.display_name}?`}
      </p>
      <p className="mt-1 text-sm text-[color:var(--fs-text-secondary)]">
        {signOutSentence(member.display_name, pending.kind === "role" ? "role" : "disable", member.email, selfId === member.user_id)}
      </p>
      {error && (
        <div className="mt-2">
          <Notice error testId={`team-confirm-${member.user_id}-error`}>
            {error}
          </Notice>
        </div>
      )}
      <div className="mt-3 flex flex-wrap justify-start gap-2">
        <button
          ref={cancelRef}
          type="button"
          data-testid={`team-confirm-${member.user_id}-cancel`}
          className={secondary}
          disabled={busy}
          onClick={cancel}
        >
          Cancel
        </button>
        <button
          type="button"
          data-testid={`team-confirm-${member.user_id}-yes`}
          className={action}
          disabled={busy}
          onClick={() => void confirm()}
        >
          {pending.kind === "role" ? "Confirm role change" : "Confirm and disable access"}
        </button>
      </div>
    </div>
  );

  return (
    <li
      className="rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3"
      onKeyDown={(event) => {
        // Escape closes the question first; nothing else on the page should also react to it.
        if (event.key === "Escape" && pending && !busy) {
          event.preventDefault();
          event.stopPropagation();
          cancel();
        }
      }}
    >
      <DetailsRow
        testId={testId}
        toggleLabel={member.display_name}
        open={open}
        onOpenChange={(next) => {
          if (!next) {
            // Folding the card away drops an open question; focus stays on the arrow.
            if (busy) return;
            setPending(null);
            setError("");
            setNotice("");
          }
          setOpen(next);
        }}
        summary={
          <div className="flex min-w-0 items-start gap-3">
            <MemberAvatar
              id={member.user_id}
              name={member.display_name}
              email={member.email}
              testId={`${testId}-avatar`}
            />
            <div className="min-w-0">
              <p className="break-words font-medium">{member.display_name}</p>
              <p className="break-all text-sm text-[color:var(--fs-text-secondary)]">{member.email}</p>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                <span data-testid={`${testId}-role`}>
                  <StatusLabel tone="neutral">{roleLabels[member.role]}</StatusLabel>
                </span>
                <span data-testid={`${testId}-status`}>
                  <StatusLabel tone={status.tone}>{status.label}</StatusLabel>
                </span>
              </div>
            </div>
          </div>
        }
      >
        <p className="text-[color:var(--fs-text-secondary)]">
          {member.is_enabled ? "Membership enabled" : "Membership disabled"}
        </p>
        <div className="mt-3">
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Role</span>
            <select
              ref={selectRef}
              aria-label={`Role for ${member.email}`}
              data-testid={`team-role-${member.user_id}`}
              className={field + " w-auto max-w-full"}
              disabled={busy}
              value={pending?.kind === "role" ? pending.role : member.role}
              onChange={(e) => {
                const next = e.target.value as TenantRole;
                setNotice("");
                setError("");
                setPending(next === member.role ? null : { kind: "role", role: next });
              }}
            >
              {ROLES.map((value) => (
                <option key={value} value={value}>
                  {roleLabels[value]}
                </option>
              ))}
            </select>
          </label>
          {pending?.kind === "role" && question}
        </div>
        <div className="mt-3 flex flex-wrap justify-start gap-2">
          <button
            ref={accessRef}
            type="button"
            data-testid={`team-access-${member.user_id}`}
            aria-expanded={member.is_enabled ? pending?.kind === "disable" : undefined}
            aria-controls={pending?.kind === "disable" ? `${testId}-confirm` : undefined}
            disabled={busy}
            className={secondary}
            onClick={() => {
              if (!member.is_enabled) {
                void enable();
                return;
              }
              setNotice("");
              setError("");
              setPending({ kind: "disable" });
            }}
          >
            {member.is_enabled ? "Disable access" : "Enable access"}
          </button>
          {platform && (
            <>
              <button
                type="button"
                data-testid={`team-reset-${member.user_id}`}
                disabled={busy}
                className={secondary}
                onClick={() => onReset(member)}
              >
                Create password reset link
              </button>
              <button
                ref={revokeRef}
                type="button"
                data-testid={`team-revoke-${member.user_id}`}
                disabled={busy}
                className={secondary}
                onClick={() => void revoke()}
              >
                Sign out everywhere
              </button>
            </>
          )}
        </div>
        {pending?.kind === "disable" && question}
        {!pending && error && (
          <div className="mt-3">
            <Notice error testId={`${testId}-error`}>
              {error}
            </Notice>
          </div>
        )}
        {notice && (
          <div className="mt-3">
            <Notice testId={`team-notice-${member.user_id}`}>{notice}</Notice>
          </div>
        )}
      </DetailsRow>
    </li>
  );
}
