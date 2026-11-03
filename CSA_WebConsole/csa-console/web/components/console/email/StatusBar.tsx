"use client";

import { useEffect, useRef } from "react";
import type { EmailContext } from "@/lib/email-api";
import { formatWhen } from "@/lib/email-recipients";
import StatusLabel from "../fs/StatusLabel";
import { DetailsRow } from "../kit";
import { action } from "../ui";
import ConfirmAction from "./ConfirmAction";

/**
 * The top of the page: whether the mailbox is connected, when it last synced and the Sync action.
 * The raw "Mailbox: address · status · last synced" line, the privacy text and the rarely used
 * switches (switch off for the company, disconnect) sit behind the arrow, word for word.
 */
export default function StatusBar({
  context,
  lastSync,
  busy,
  writable,
  onSync,
  onConnect,
  onSetEnabled,
  onDisconnect,
  wide,
}: {
  context: EmailContext;
  /** The last sync the thread list reports, when the mailbox has none of its own. */
  lastSync: string | null;
  busy: boolean;
  writable: boolean;
  onSync: () => void;
  onConnect: () => void;
  onSetEnabled: (enabled: boolean) => void;
  onDisconnect: () => void;
  /** Spans both columns of the page grid (when nothing sits beside it). */
  wide: boolean;
}) {
  const { mailbox } = context;
  const root = useRef<HTMLElement>(null);
  const switchOn = useRef<HTMLButtonElement>(null);
  const connect = useRef<HTMLButtonElement>(null);
  // After Switch off or Disconnect the button that was pressed is gone and focus would fall to the
  // page. Once the request has finished, move it to the control that replaced it.
  const refocus = useRef<"switch-on" | "disconnected" | null>(null);
  useEffect(() => {
    if (!refocus.current || busy) return;
    const target = refocus.current === "switch-on"
      ? switchOn.current
      : connect.current ?? root.current?.querySelector<HTMLElement>('[data-testid="email-mailbox-toggle"]');
    refocus.current = null;
    target?.focus();
  }, [busy, context.enabled, context.mailbox?.status]);
  const connected = mailbox?.status === "connected";
  const synced = mailbox?.last_sync_at ?? lastSync;
  let tone: "success" | "neutral" | "attention" | "error" = "neutral";
  let word = "Not connected";
  if (!context.enabled) word = "Switched off";
  else if (mailbox?.status === "connected") { tone = "success"; word = "Connected"; }
  else if (mailbox?.status === "pending") { tone = "attention"; word = "Not finished"; }
  else if (mailbox?.status === "revoked") { tone = "error"; word = "Access removed"; }
  return (
    <section ref={root} data-testid="email-status" className={`card space-y-3 rounded-[14px] p-4 sm:p-5 ${wide ? "lg:col-span-2" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="min-w-0 space-y-1">
          <h2 className="font-(family-name:--fs-font-heading) text-base font-semibold">Mailbox</h2>
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <span data-testid="email-status-word"><StatusLabel tone={tone}>{word}</StatusLabel></span>
            {!context.enabled && <span>The email assistant is switched off for your company.</span>}
            {context.enabled && !mailbox && <span>No mailbox connected for your account.</span>}
            {context.enabled && mailbox && <span className="break-all font-medium">{mailbox.address ?? "(pending)"}</span>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {context.can_manage && !context.enabled && (
            <button ref={switchOn} type="button" data-testid="email-switch-on" className={action} disabled={busy} onClick={() => onSetEnabled(true)}>
              Switch on for my company
            </button>
          )}
          {context.can_connect && !connected && (
            <button ref={connect} type="button" data-testid="email-connect" className={action} disabled={busy} onClick={onConnect}>Connect Gmail</button>
          )}
          {context.enabled && connected && writable && (
            <button type="button" className={action} disabled={busy} onClick={onSync}>Check for new email</button>
          )}
        </div>
      </div>
      {!context.provider_configured && (
        <p className="text-xs text-[color:var(--fs-warning)]">Connecting Gmail isn&apos;t available yet. Contact fourstacks support.</p>
      )}
      <DetailsRow
        testId="email-mailbox"
        toggleLabel="the mailbox"
        summary={context.enabled && mailbox
          ? <span data-testid="email-last-sync" className="text-sm text-[color:var(--fs-text-secondary)]">
            {mailbox.status === "pending" && "Gmail has not finished connecting. "}
            {mailbox.status === "revoked" && "Gmail access was removed, so new email will not arrive. "}
            {synced ? `Last checked ${formatWhen(synced)}.` : "Not checked yet."}
          </span>
          : <span className="text-sm text-[color:var(--fs-text-secondary)]">Privacy and mailbox settings</span>}
      >
        <div className="space-y-3 pt-3 text-sm">
          <p className="font-medium">Privacy and mailbox settings</p>
          {context.enabled && mailbox && (
            <p data-testid="email-mailbox-line">
              {`Mailbox: ${mailbox.address ?? "(pending)"} · ${mailbox.status}` +
                (mailbox.last_sync_at ? ` · last synced ${formatWhen(mailbox.last_sync_at)}` : "")}
            </p>
          )}
          <p data-testid="email-disclosure" className="text-xs text-[color:var(--fs-text-secondary)]">{context.disclosure}</p>
          <div className="flex flex-wrap items-start gap-2">
            {context.can_manage && context.enabled && (
              <ConfirmAction
                testId="email-switch-off"
                label="Switch off for my company"
                question="Switch the email assistant off for this company? Nobody here can use it until someone switches it on again."
                disabled={busy}
                onConfirm={() => { refocus.current = "switch-on"; onSetEnabled(false); }}
              />
            )}
            {context.enabled && connected && writable && (
              <ConfirmAction
                testId="email-disconnect"
                label="Disconnect"
                question="Disconnect Gmail? Emails already loaded stay readable. Connect Gmail again to sync or send."
                disabled={busy}
                onConfirm={() => { refocus.current = "disconnected"; onDisconnect(); }}
              />
            )}
          </div>
        </div>
      </DetailsRow>
    </section>
  );
}
