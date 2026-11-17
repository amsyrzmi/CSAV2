"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Approval, Conversation } from "@/lib/inbox-api";
import { createProposal, type ProposalOut } from "@/lib/workspace-api";

type Result = {
  outcome: "applied" | "already_handled" | "expired" | "not_found";
  queued: boolean; warnings: string[]; status: string | null;
};
export type Pending = {
  path: string; payload: Record<string, unknown>; onSuccess?: () => void;
  /** Queue requests only: the plain notice for the result, in place of the reply wording. */
  notice?: (result: unknown) => string;
  /** Queue requests only: the plain sentence for a failure. `status` is null when no answer came. */
  failure?: (cause: unknown, status: number | null) => string;
  /**
   * Queue requests only: whether a failure is settled (dropped, no Retry). Without it a 4xx is
   * settled and anything else (no answer, 5xx) is kept for Retry with the same request key.
   */
  definitive?: (status: number | null, cause: unknown) => boolean;
  /**
   * Called with the answer of a successful request, first attempt or Retry alike (Retry sends this
   * same object), so a bulk action's results reach the screen however it got through.
   */
  onResult?: (result: unknown) => void;
};
/** What `submit` settled with. `definitive` is false when the outcome is uncertain and the request is kept. */
export type Submitted =
  | { ok: true; result: unknown }
  | { ok: false; status: number | null; cause: unknown; definitive: boolean };

/**
 * The inbox's write paths, unchanged from the previous InboxWorkspace: request keys, one
 * explicit retry of an uncertain request with the same key, and approve/reject through a
 * shared proposal bound to the conversation version. `revision` bumps after every outcome
 * so the polled reads refresh at once.
 */
export function useInboxActions(query: string, tenantId: string | null) {
  const [revision, setRevision] = useState(0);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [proposal, setProposal] = useState<ProposalOut | null>(null);

  /** Sends one request and settles with what happened; `submit` is the same without the result. */
  async function execute(request: Pending): Promise<Submitted> {
    setPending(request); setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<Result>(request.path, {
        method: "POST", body: JSON.stringify(request.payload), signal: AbortSignal.timeout(15_000),
      });
      setPending(null);
      request.onSuccess?.();
      request.onResult?.(result);
      if (request.notice) {
        setNotice(request.notice(result));
        setRevision((value) => value + 1);
        return { ok: true, result };
      }
      setNotice(result.outcome === "already_handled" ? "Already handled. Refreshed the current state."
        : result.outcome === "expired" ? `Approval expired.${result.queued ? " Follow-up reply queued." : " No reply queued."}`
        : result.queued ? "Reply queued. Delivery status appears in the conversation."
        : result.status ? `Reply ${result.status}. No new delivery queued.` : "Conversation updated.");
      if (result.warnings.length) setNotice((value) => `${value} Edit warning: ${result.warnings.join(", ")}`);
      setRevision((value) => value + 1);
      return { ok: true, result };
    } catch (cause) {
      const status = cause instanceof ApiError ? cause.status : null;
      const definitive = request.definitive ? request.definitive(status, cause)
        : cause instanceof ApiError && cause.status >= 400 && cause.status < 500;
      if (definitive) setPending(null);
      setError(request.failure ? request.failure(cause, status)
        : `${(cause as Error).message}${definitive ? ". Refreshed; review before acting again." : ". Outcome uncertain. Retry keeps the same request key. After reload, inspect history before sending again."}`);
      setRevision((value) => value + 1);
      return { ok: false, status, cause, definitive };
    } finally { setBusy(false); }
  }

  async function submit(request: Pending): Promise<void> {
    await execute(request);
  }

  /** Approve and reject are confirmed through a shared proposal bound to the conversation version. */
  async function propose(approval: Approval, decision: "approve" | "reject") {
    setBusy(true); setError(""); setNotice("");
    try {
      const current = await api<Conversation>(
        `/console/v1/inbox/conversations/${approval.conversation_id}?${query}`,
        { signal: AbortSignal.timeout(15_000) },
      );
      setProposal(await createProposal("inbox_decision", {
        action: "inbox_decision", conversation_id: approval.conversation_id, approval_id: approval.id,
        decision, expected_version: current.inbox_version,
      }, undefined, AbortSignal.timeout(15_000), tenantId));
    } catch (cause) {
      setError((cause as Error).message);
      setRevision((value) => value + 1);
    } finally { setBusy(false); }
  }

  /** The edited reply is queued directly; the caller has already asked for confirmation. */
  function edit(approval: Approval, text: string) {
    return submit({ path: `/console/v1/inbox/approvals/${approval.id}/decide?${query}`,
      payload: { request_key: crypto.randomUUID(), decision: "edit", text } });
  }

  /** Say what a control outside the request path did (a saved view chosen or saved), in the notice. */
  function announce(text: string) {
    setNotice(text);
  }

  /** Forget the last outcome when the reader moves to another conversation. */
  function clearFeedback() {
    setError(""); setNotice(""); setProposal(null);
  }

  return {
    tenantId, revision, refresh: () => setRevision((value) => value + 1),
    pending, busy, blocked: busy || pending !== null, error, notice, proposal,
    submit, execute, propose, edit, clearFeedback, announce,
  };
}

export type InboxActions = ReturnType<typeof useInboxActions>;
