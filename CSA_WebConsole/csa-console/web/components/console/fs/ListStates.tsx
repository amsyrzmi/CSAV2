"use client";

import type { ReactNode } from "react";
import { action, secondary } from "@/components/console/ui";
import AgentAvatar from "./AgentAvatar";
import { RowSkeleton } from "./Skeleton";

export type ListStateKind =
  | "loading"
  | "empty"
  | "unavailable"
  | "partial"
  | "forbidden";

const COPY = {
  loading: "Loading conversations",
  emptyTitle: "No conversations yet",
  empty:
    "Conversations from your customers will appear here. Connect WhatsApp to get started.",
  askAdmin: "Ask your administrator to connect WhatsApp.",
  unavailable: "We could not refresh your conversations.",
  partial: "Showing the latest conversations only.",
  forbidden:
    "You do not have access to the customer inbox. Ask your administrator to give you staff access.",
} as const;

/**
 * The five distinct presentations of the conversation list (UI guide 7). `block` fills the
 * list area; `banner` is the compact notice for "unavailable" and "partial" above rows that
 * are already on screen.
 */
export function ListState({
  kind,
  variant = "block",
  onRetry,
  onLoadOlder,
  connectHref,
  message: override,
}: {
  kind: ListStateKind;
  variant?: "block" | "banner";
  onRetry?: () => void;
  onLoadOlder?: () => void;
  /** Where "Connect WhatsApp" goes. Null or missing for roles that cannot connect a number. */
  connectHref?: string | null;
  /**
   * Forbidden only: the server's own plain-words refusal (for example an inactive service
   * package) in place of the default no-access copy. Still no retry.
   */
  message?: string;
}) {
  const testId = `inbox-list-state-${kind}`;
  const text = (k: Exclude<ListStateKind, "loading">) => (k === "forbidden" && override ? override : message(k));

  if (kind === "loading") {
    return (
      <div role="status" aria-busy="true" data-testid={testId}>
        <span className="sr-only">{COPY.loading}</span>
        <RowSkeleton />
        <RowSkeleton />
        <RowSkeleton />
      </div>
    );
  }

  const role = kind === "unavailable" || kind === "forbidden" ? "alert" : "status";

  if (variant === "banner") {
    const failed = kind === "unavailable";
    return (
      <div
        role={role}
        data-testid={testId}
        className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border p-3 text-sm"
        style={{
          borderColor: failed ? "var(--fs-danger)" : "var(--fs-border)",
          background: failed ? "var(--fs-danger-bg)" : "var(--fs-info-bg)",
          color: failed ? "var(--fs-danger)" : "var(--fs-text)",
        }}
      >
        <p>{text(kind)}</p>
        {actionFor(kind, secondary, onRetry, onLoadOlder)}
      </div>
    );
  }

  return (
    <div
      role={role}
      data-testid={testId}
      className="flex flex-col items-center gap-3 px-4 py-8 text-center"
    >
      {kind === "empty" ? (
        <>
          <AgentAvatar agentId="support" size={64} />
          <h2 className="font-(family-name:--fs-font-heading) text-base font-semibold">
            {COPY.emptyTitle}
          </h2>
        </>
      ) : null}
      <p
        className="max-w-xs text-sm"
        style={{ color: kind === "forbidden" ? "var(--fs-text)" : "var(--fs-text-secondary)" }}
      >
        {text(kind)}
      </p>
      {kind === "empty" ? (
        typeof connectHref === "string" ? (
          <a href={connectHref} className={action} data-testid="inbox-connect-whatsapp">
            Connect WhatsApp
          </a>
        ) : (
          <p className="max-w-xs text-sm">{COPY.askAdmin}</p>
        )
      ) : (
        actionFor(kind, secondary, onRetry, onLoadOlder)
      )}
    </div>
  );
}

function message(kind: Exclude<ListStateKind, "loading">): string {
  return COPY[kind];
}

/** The recovery button, if the state has one and the caller gave it a handler. */
function actionFor(
  kind: ListStateKind,
  className: string,
  onRetry?: () => void,
  onLoadOlder?: () => void,
): ReactNode {
  if (kind === "unavailable" && onRetry)
    return (
      <button type="button" className={className} onClick={onRetry}>
        Try again
      </button>
    );
  if (kind === "partial" && onLoadOlder)
    return (
      <button type="button" className={className} onClick={onLoadOlder}>
        Load older conversations
      </button>
    );
  return null;
}

export default ListState;
