"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Approval, Conversation } from "@/lib/inbox-api";
import { createProposal, type ProposalOut } from "@/lib/workspace-api";

type Result = {
  outcome: "applied" | "already_handled" | "expired" | "not_found";
  queued: boolean; warnings: string[]; status: string | null;
};
export type Pending = { path: string; payload: Record<string, unknown>; onSuccess?: () => void };

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

  async function submit(request: Pending) {
    setPending(request); setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<Result>(request.path, {
        method: "POST", body: JSON.stringify(request.payload), signal: AbortSignal.timeout(15_000),
      });
      setPending(null);
      request.onSuccess?.();
      setNotice(result.outcome === "already_handled" ? "Already handled. Refreshed the current state."
        : result.outcome === "expired" ? `Approval expired.${result.queued ? " Follow-up reply queued." : " No reply queued."}`
        : result.queued ? "Reply queued. Delivery status appears in the conversation."
        : result.status ? `Reply ${result.status}. No new delivery queued.` : "Conversation updated.");
      if (result.warnings.length) setNotice((value) => `${value} Edit warning: ${result.warnings.join(", ")}`);
      setRevision((value) => value + 1);
    } catch (cause) {
      const definitive = cause instanceof ApiError && cause.status >= 400 && cause.status < 500;
      if (definitive) setPending(null);
      setError(`${(cause as Error).message}${definitive ? ". Refreshed; review before acting again." : ". Outcome uncertain. Retry keeps the same request key. After reload, inspect history before sending again."}`);
      setRevision((value) => value + 1);
    } finally { setBusy(false); }
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

  /** Forget the last outcome when the reader moves to another conversation. */
  function clearFeedback() {
    setError(""); setNotice(""); setProposal(null);
  }

  return {
    tenantId, revision, refresh: () => setRevision((value) => value + 1),
    pending, busy, blocked: busy || pending !== null, error, notice, proposal,
    submit, propose, edit, clearFeedback,
  };
}

export type InboxActions = ReturnType<typeof useInboxActions>;
