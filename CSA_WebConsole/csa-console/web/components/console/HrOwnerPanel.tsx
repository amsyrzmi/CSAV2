"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Modal } from "@/components/ui/kit";
import type { Member } from "@/lib/identity-contracts";
import {
  UNCONFIRMED_TEXT,
  confirmText,
  eligibleOwners,
  hrOwnerClient,
  moveDoneText,
  moveKey,
  moveUncertain,
  ownerRefusalText,
  ownerStale,
  type HeldKey,
  type HrOwnerState,
} from "@/lib/hr-owner";
import { action, field, Notice, secondary } from "./ui";

/**
 * Names the company's HR owner and moves the title. Renders nothing until the owner has been read,
 * and nothing when the company has no HR package. A platform admin is not a member, so
 * `currentUserId` is left out there and nobody is "yourself".
 */
export function HrOwnerPanel({
  clientId,
  members,
  currentUserId,
}: {
  clientId: string;
  members: Member[];
  currentUserId?: string;
}) {
  const [state, setState] = useState<HrOwnerState | null>(null);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  const [refusal, setRefusal] = useState("");
  const [busy, setBusy] = useState(false);
  // The key stays with the exact request, so a retry after a lost answer is recognised.
  const held = useRef<HeldKey | null>(null);
  const reads = useRef(0);
  const mounted = useRef(false);

  // One read at a time wins: a later read, or a move's answer, makes an earlier one stale.
  const read = useCallback(async () => {
    const mine = ++reads.current;
    try {
      const next = await hrOwnerClient(clientId).read();
      return mounted.current && mine === reads.current ? next : null;
    } catch {
      /* Keep what is shown: the Team page has its own errors, and the move stays refusable. */
      return null;
    }
  }, [clientId]);
  const load = useCallback(async () => {
    const next = await read();
    if (next) setState(next);
  }, [read]);
  useEffect(() => {
    mounted.current = true;
    void read().then((next) => {
      if (next) setState(next);
    });
    return () => {
      mounted.current = false;
    };
  }, [read]);

  if (!state?.available) return null;
  const owner = state.owner;
  const eligible = eligibleOwners(members, owner);
  const chosen = eligible.find((member) => member.user_id === choice);
  const verb = owner ? "Change" : "Set HR owner";

  async function confirm() {
    if (!chosen) return;
    const expected = owner?.user_id ?? null;
    const request = moveKey(held.current, chosen.user_id, expected);
    held.current = request;
    setBusy(true);
    setRefusal("");
    try {
      const result = await hrOwnerClient(clientId).move(chosen.user_id, expected, request.key);
      if (!mounted.current) return;
      held.current = null;
      // The answer names who holds the title now, which can differ from the person picked.
      reads.current++;
      setState({ available: true, owner: result.owner });
      setNotice({ text: moveDoneText(result.mail), error: false });
      setOpen(false);
    } catch (cause) {
      if (!mounted.current) return;
      if (moveUncertain(cause)) {
        setOpen(false);
        setNotice({ text: UNCONFIRMED_TEXT, error: true });
        await load();
      } else {
        setRefusal(ownerRefusalText(cause));
        if (ownerStale(cause)) await load();
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <section className="card rounded-[14px] p-5 sm:p-6" data-testid="hr-owner-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-medium" data-testid="hr-owner-name">
          {owner ? `HR owner: ${owner.display_name}` : "No HR owner yet"}
        </p>
        <button
          type="button"
          className={secondary}
          disabled={busy || eligible.length === 0}
          onClick={() => {
            setChoice("");
            setRefusal("");
            setNotice(null);
            setOpen(true);
          }}
        >
          {verb}
        </button>
      </div>
      {owner && !owner.usable && (
        <div className="mt-4">
          <Notice error>This HR owner can&apos;t sign in or act. Choose a new one.</Notice>
        </div>
      )}
      {notice && (
        <div className="mt-4">
          <Notice error={notice.error}>{notice.text}</Notice>
        </div>
      )}
      <Modal
        isOpen={open}
        onClose={() => {
          if (!busy) setOpen(false);
        }}
        title={owner ? "Change the HR owner" : "Set the HR owner"}
      >
        <div className="space-y-4 text-sm">
          <div>
            <label htmlFor="hr-owner-choice" className="mb-2 block">
              New HR owner
            </label>
            <select
              id="hr-owner-choice"
              className={field}
              value={chosen?.user_id ?? ""}
              disabled={busy}
              onChange={(e) => {
                setChoice(e.target.value);
                setRefusal("");
              }}
            >
              <option value="">Choose a member</option>
              {eligible.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.display_name} · {member.email}
                </option>
              ))}
            </select>
          </div>
          {chosen && (
            <p data-testid="hr-owner-confirm" className="text-ink-600">
              {confirmText({
                oldOwnerName: owner?.display_name ?? null,
                isSelf: currentUserId !== undefined && chosen.user_id === currentUserId,
              })}
            </p>
          )}
          {refusal && <Notice error>{refusal}</Notice>}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={action}
              disabled={busy || !chosen}
              onClick={() => void confirm()}
            >
              {owner ? "Change HR owner" : "Set HR owner"}
            </button>
            <button
              type="button"
              className={secondary}
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      </Modal>
    </section>
  );
}
