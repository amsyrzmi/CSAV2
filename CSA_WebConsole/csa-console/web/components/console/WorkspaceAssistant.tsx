"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import {
  createWorkspaceThread,
  createWorkspaceTurn,
  listWorkspaceThreads,
  listWorkspaceTurns,
  type WorkspacePageHint,
  type WorkspaceResult,
  type WorkspaceThread,
  type WorkspaceTurn,
} from "@/lib/workspace-api";
import { guidanceText } from "@/lib/workspace-paths.mjs";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

export function WorkspaceAssistant({ me }: { me: Me }) {
  const platform = me.role === "platform_admin";
  const permitted =
    platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  const [clients, setClients] = useState<Client[]>([]);
  const [tenantId, setTenantId] = useState(
    platform ? "" : (me.active_client_id ?? ""),
  );
  const [error, setError] = useState("");

  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [platform]);

  if (!permitted)
    return (
      <Notice error>
        The internal workspace requires a platform administrator or tenant staff
        membership.
      </Notice>
    );

  return (
    <>
      <Heading
        title="Staff workspace"
        description="Private internal guidance and authorized local order reads. This is separate from the customer sandbox and does not use customer-channel identity or model actions."
      />
      {platform && (
        <label className="mb-5 block">
          Tenant
          <select
            className={field}
            value={tenantId}
            onChange={(event) => setTenantId(event.target.value)}
          >
            <option value="">Select a tenant</option>
            {clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <Notice error>{error}</Notice>}
      {!tenantId ? (
        <Notice>Select a tenant to open its private workspace.</Notice>
      ) : (
        <TenantWorkspace key={tenantId} tenantId={tenantId} />
      )}
    </>
  );
}

function TenantWorkspace({ tenantId }: { tenantId: string }) {
  const [threads, setThreads] = useState<WorkspaceThread[]>([]);
  const [threadId, setThreadId] = useState("");
  const [turns, setTurns] = useState<WorkspaceTurn[]>([]);
  const [body, setBody] = useState("");
  const [page, setPage] = useState<"workspace_home" | "business_orders" | "business_order">(
    "workspace_home",
  );
  const [orderId, setOrderId] = useState("");
  const [pendingThreadKey, setPendingThreadKey] = useState<string | null>(null);
  const [pendingTurnKey, setPendingTurnKey] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const mutationController = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      mutationController.current?.abort();
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    listWorkspaceThreads(tenantId, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setThreads(result.items);
        setThreadId((current) =>
          current && result.items.some((item) => item.id === current)
            ? current
            : (result.items[0]?.id ?? ""),
        );
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [tenantId]);

  useEffect(() => {
    if (!threadId) return;
    const controller = new AbortController();
    listWorkspaceTurns(tenantId, threadId, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setTurns((current) => {
          const unique = new Map(
            [...current, ...result.items].map((turn) => [turn.id, turn]),
          );
          return [...unique.values()].sort((left, right) => left.id - right.id);
        });
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [tenantId, threadId]);

  function newConversation() {
    setThreadId("");
    setTurns([]);
    setPendingThreadKey(null);
    setPendingTurnKey(null);
    setBody("");
    setError("");
  }

  function selectConversation(id: string) {
    setThreadId(id);
    setTurns([]);
    setPendingThreadKey(null);
    setPendingTurnKey(null);
    setError("");
  }

  async function send(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!body.trim() || busy || (page === "business_order" && !orderId.trim()))
      return;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    mutationController.current = controller;
    try {
      let activeThread = threadId;
      if (!activeThread) {
        const threadKey = pendingThreadKey ?? crypto.randomUUID();
        setPendingThreadKey(threadKey);
        const created = await createWorkspaceThread(
          tenantId,
          threadKey,
          controller.signal,
        );
        activeThread = created.id;
        setThreadId(activeThread);
        setPendingThreadKey(null);
        setThreads((current) => [
          created,
          ...current.filter((item) => item.id !== created.id),
        ]);
      }
      const requestKey = pendingTurnKey ?? crypto.randomUUID();
      setPendingTurnKey(requestKey);
      const pageHint: WorkspacePageHint =
        page === "business_order"
          ? {
              page,
              entity: { kind: "local_order", order_id: orderId.trim() },
            }
          : { page };
      const result = await createWorkspaceTurn(
        tenantId,
        activeThread,
        body,
        pageHint,
        requestKey,
        controller.signal,
      );
      setTurns((current) => {
        const unique = new Map(
          [...current, result].map((turn) => [turn.id, turn]),
        );
        return [...unique.values()].sort((left, right) => left.id - right.id);
      });
      setBody("");
      setPendingTurnKey(null);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError((cause as Error).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      if (mutationController.current === controller)
        mutationController.current = null;
    }
  }

  const pending = pendingTurnKey !== null || pendingThreadKey !== null;
  return (
    <div className="space-y-5">
      <Panel title="Private conversations">
        <div className="mb-4 flex flex-wrap gap-3">
          <label className="min-w-64 flex-1">
            Conversation
            <select
              className={field}
              value={threadId}
              disabled={busy}
              onChange={(event) => selectConversation(event.target.value)}
            >
              <option value="">New conversation</option>
              {threads.map((thread) => (
                <option key={thread.id} value={thread.id}>
                  Conversation {thread.sequence}
                </option>
              ))}
            </select>
          </label>
          <button
            className={secondary}
            type="button"
            disabled={busy}
            onClick={newConversation}
          >
            New conversation
          </button>
        </div>
        {loading && <p>Loading private conversations…</p>}
        {error && <Notice error>{error}</Notice>}
        <div className="mb-5 space-y-4" aria-live="polite">
          {turns.map((turn) => (
            <article
              key={turn.id}
              className="rounded-md border border-ink-200 p-4"
            >
              <p className="whitespace-pre-wrap text-sm">{turn.body}</p>
              <Result result={turn.result} />
              <p className="mt-2 text-xs text-ink-500">
                {new Date(turn.created_at).toLocaleString()}
                {turn.duplicate ? " · Retried safely" : ""}
              </p>
            </article>
          ))}
          {threadId && !loading && turns.length === 0 && (
            <p className="text-sm">No messages in this conversation yet.</p>
          )}
        </div>
        {turns.length >= 50 && (
          <p className="mb-4 text-sm text-ink-500">
            Showing the latest 50 messages.
          </p>
        )}
        <form onSubmit={send} className="space-y-3">
          <label className="block">
            Page context
            <select
              className={field}
              value={page}
              disabled={busy || pending}
              onChange={(event) => {
                const value = event.target.value;
                if (
                  value === "workspace_home" ||
                  value === "business_orders" ||
                  value === "business_order"
                )
                  setPage(value);
                setError("");
              }}
            >
              <option value="workspace_home">Workspace guidance</option>
              <option value="business_orders">Business orders guidance</option>
              <option value="business_order">Read one local order</option>
            </select>
          </label>
          {page === "business_order" && (
            <label className="block">
              Local order ID
              <input
                className={field}
                value={orderId}
                maxLength={100}
                disabled={busy || pending}
                onChange={(event) => setOrderId(event.target.value)}
                required
              />
            </label>
          )}
          <label className="block">
            Message
            <textarea
              className={`${field} min-h-24 py-3`}
              value={body}
              maxLength={4000}
              disabled={busy || pending}
              onChange={(event) => setBody(event.target.value)}
              required
            />
          </label>
          {pending && error && (
            <button
              type="button"
              className={secondary}
              disabled={busy}
              onClick={() => {
                setPendingThreadKey(null);
                setPendingTurnKey(null);
                setError("");
              }}
            >
              Start a new request
            </button>
          )}
          <button className={action} type="submit" disabled={busy || !body.trim()}>
            {busy ? "Sending…" : pending ? "Retry same request" : "Send"}
          </button>
        </form>
      </Panel>
      <Panel title="Scope and data source">
        <p className="text-sm">
          Guidance is deterministic application text. Order reads use local
          tenant records only and include a source and as-of time. No marketplace,
          customer-channel, HR, payroll, order mutation, or model-tool action is
          available here.
        </p>
      </Panel>
    </div>
  );
}

function Result({ result }: { result: WorkspaceResult }) {
  if (result.status === "denied")
    return (
      <Notice error>
        {result.code === "resource_unavailable"
          ? "The requested local order is unavailable in this tenant."
          : "This request could not be authorized."}
      </Notice>
    );
  if (result.status === "unavailable")
    return <Notice error>The workspace could not complete this request.</Notice>;
  if (result.action === "page_guidance")
    return (
      <div className="mt-3 rounded-md bg-brand-50 p-3 text-sm">
        <p>{guidanceText(result.instruction_key)}</p>
        <p className="mt-2 text-xs text-ink-500">
          Source: {result.source} · As of{" "}
          {new Date(result.as_of).toLocaleString()}
        </p>
      </div>
    );
  return (
    <div className="mt-3 rounded-md bg-brand-50 p-3 text-sm">
      <p className="font-semibold">
        Local order {result.order.resource.order_id}
      </p>
      <p>
        {result.order.status} · Payment: {result.order.payment_status} ·{" "}
        {result.order.currency} {result.order.total}
      </p>
      <p>
        Source: {result.order.channel} · Tracking:{" "}
        {result.order.tracking_no ?? "Not recorded"} · Carrier:{" "}
        {result.order.carrier ?? "Not recorded"}
      </p>
      <p className="mt-2 text-xs text-ink-500">
        Source: {result.source} · Marketplace sync: {result.marketplace_sync} ·
        As of {new Date(result.as_of).toLocaleString()}
      </p>
    </div>
  );
}
