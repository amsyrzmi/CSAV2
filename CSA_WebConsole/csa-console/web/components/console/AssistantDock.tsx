"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { ArrowUp, MessageSquare, X } from "lucide-react";
import { languageOf, panelText, PANEL, type Language } from "@/lib/assistant-copy";
import { useAssistantConversation, type AssistantConversation } from "@/lib/assistant-conversation";
import {
  candidateAnswer,
  isAnswerable,
  navigateText,
  optionAnswer,
  orderCardText,
  originFor,
  resultView,
  tabLabel,
  typedMessage,
  watchHref,
  type Outgoing,
} from "@/lib/assistant-view";
import type { Me } from "@/lib/identity-contracts";
import type { PackageState } from "@/lib/services-api";
import type { ProposalOut, WorkspaceResult } from "@/lib/workspace-api";
import { CopyReply } from "./chat/CopyReply";
import { starterLook } from "./chat/ChatEmptyState";
import AgentAvatar from "./fs/AgentAvatar";
import { ProposalCard } from "./ProposalCard";
import { action, Notice, secondary } from "./ui";

/** The corner the launcher and the panel share: 20 px in, or clear of a notch or home bar. */
const corner = "bottom-[max(1.25rem,env(safe-area-inset-bottom))] right-[max(1.25rem,env(safe-area-inset-right))]";
const sendLook =
  "inline-flex h-10 min-w-10 shrink-0 items-center justify-center gap-2 rounded-[var(--fs-radius-round)] bg-[var(--fs-primary)] text-sm font-medium text-[color:var(--fs-on-primary)] transition-colors duration-150 hover:bg-[var(--fs-primary-hover)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] active:scale-[0.97] disabled:cursor-not-allowed disabled:bg-[var(--fs-disabled-bg)] disabled:text-[color:var(--fs-disabled-text)] pointer-coarse:h-11";
const headerButton =
  "inline-flex min-h-9 items-center rounded-[var(--fs-radius-round)] border border-[color:var(--fs-border-control)] px-2.5 text-[13px] font-medium text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11";

/** The floating view of the shared conversation; it renders nothing where none is offered. */
export function AssistantDock() {
  const conversation = useAssistantConversation();
  if (!conversation) return null;
  // Keyed by company, so a change of company also starts the panel closed.
  return <Dock key={conversation.tenant} conversation={conversation} />;
}

function Dock({ conversation }: { conversation: AssistantConversation }) {
  const {
    tenant,
    me,
    packages,
    loaded,
    ensureLoaded,
    turns,
    outcomes,
    origins,
    body,
    setBody,
    busy,
    error,
    retry,
    resumeTab,
    context,
    language,
    ask,
    retrySame,
    dismissRetry,
    settle,
    newConversation,
  } = conversation;
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const openButton = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef(false);

  // The thread and its turns are read the first time the panel opens.
  useEffect(() => {
    if (open) ensureLoaded();
  }, [open, ensureLoaded]);

  useEffect(() => {
    if (open) input.current?.focus();
    else if (returnFocus.current) openButton.current?.focus();
  }, [open]);

  // The message box grows with what is typed (up to about six lines) and shrinks back after a send.
  useEffect(() => {
    const box = input.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, 152)}px`;
  }, [body, open]);

  // The newest reply scrolls into view; an open question stays visible while the assistant works.
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const box = list.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [open, turns.length, busy]);

  function close() {
    returnFocus.current = true;
    setOpen(false);
  }

  // Panel-wide lines follow the newest turn; each turn's own lines follow that turn.
  const text = PANEL[language];

  if (!open)
    return (
      <button
        ref={openButton}
        type="button"
        data-testid="assistant-open"
        aria-expanded={false}
        aria-controls={panelId}
        onClick={() => setOpen(true)}
        className={`${action} fixed ${corner} z-40 rounded-full shadow-[var(--fs-shadow-popover)]`}
      >
        <MessageSquare size={18} aria-hidden />
        {text.dock_title}
      </button>
    );

  return (
    <section
      id={panelId}
      data-testid="assistant-panel"
      aria-labelledby={`${panelId}-title`}
      onKeyDown={(event) => {
        if (event.key === "Escape") close();
      }}
      className={`fixed ${corner} z-40 flex max-h-[min(80dvh,40rem)] w-[min(28rem,calc(100vw-2.5rem))] flex-col rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border)] bg-[var(--fs-surface-raised)] text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)]`}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-[color:var(--fs-border)] px-3.5 py-3 sm:flex-nowrap">
        <AgentAvatar agentId="assistant" size={32} state="rest" label="" />
        <h2 id={`${panelId}-title`} className="text-base font-semibold">
          {text.dock_title}
        </h2>
        <span
          data-testid="assistant-tab"
          className="min-w-0 truncate rounded-full bg-[var(--fs-surface-blue)] px-2 py-0.5 text-xs text-[color:var(--fs-text)]"
        >
          {context
            ? panelText(language, "on_tab", { tab: tabLabel(context.tab, me, packages) })
            : text.no_tab_context}
        </span>
        <button
          type="button"
          className={`ml-auto shrink-0 ${headerButton}`}
          disabled={busy}
          onClick={newConversation}
        >
          {text.new_conversation}
        </button>
        <button
          type="button"
          aria-label={text.close_assistant}
          onClick={close}
          className="inline-grid size-9 shrink-0 place-items-center rounded-[var(--fs-radius-round)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:size-11"
        >
          <X size={18} aria-hidden />
        </button>
      </header>
      <div ref={list} className="flex-1 space-y-4 overflow-y-auto px-4 py-3" aria-live="polite">
        {!loaded && <p className="text-sm text-[color:var(--fs-text-secondary)]">{PANEL.en.loading}</p>}
        {loaded && turns.length === 0 && (
          <p className="text-sm text-[color:var(--fs-text-secondary)]">{text.empty_hint}</p>
        )}
        {turns.map((turn) => (
          <article key={turn.id} data-testid="assistant-turn" className="space-y-2">
            <div className="flex justify-end pl-8">
              <p className="max-w-full whitespace-pre-wrap break-words rounded-[var(--fs-radius-bubble)] rounded-br-[var(--fs-radius-control)] bg-[var(--fs-surface-blue)] px-4 py-2.5 text-sm text-[color:var(--fs-text)]">{turn.body}</p>
            </div>
            <div className="flex items-start gap-2">
              <AgentAvatar agentId="assistant" size={32} state="rest" label="" />
              <div className="min-w-0 flex-1">
                <TurnResult
                  result={turn.result}
                  language={languageOf(turn)}
                  origin={originFor(turn, origins)}
                  me={me}
                  packages={packages}
                  tenant={tenant}
                  outcomes={outcomes}
                  disabled={busy || resumeTab !== null}
                  answerable={isAnswerable(turns, turn.id)}
                  onSettled={settle}
                  onAsk={(outgoing) => ask(outgoing)}
                />
              </div>
            </div>
          </article>
        ))}
        {busy && (
          <div
            data-testid="assistant-working"
            className="flex items-center gap-2 text-sm text-[color:var(--fs-text-secondary)]"
          >
            <AgentAvatar agentId="assistant" size={32} state="working" label="" />
            <p>{text.button_sending}</p>
          </div>
        )}
        {resumeTab !== null && (
          <Notice>{panelText(language, "resume_opening", { tab: tabLabel(resumeTab, me, packages) })}</Notice>
        )}
        {error && <Notice error>{error}</Notice>}
      </div>
      <form
        className="space-y-2 border-t border-[color:var(--fs-border)] px-4 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (body.trim()) ask(typedMessage(body, turns, origins), true);
        }}
      >
        <label htmlFor={`${panelId}-input`} className="block text-xs font-medium text-[color:var(--fs-text-secondary)]">
          {text.message_label}
        </label>
        <div className="rounded-[var(--fs-radius-composer)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] shadow-[var(--fs-shadow-card)] transition-[border-color,box-shadow] duration-200 ease-(--fs-ease-out) motion-reduce:transition-none has-[textarea:focus-visible]:border-[color:var(--fs-primary)] has-[textarea:focus-visible]:shadow-[var(--fs-shadow-popover)] has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-[color:var(--fs-focus)] has-[textarea:disabled]:bg-[var(--fs-disabled-bg)]">
          <div className="flex items-end gap-2 py-2 pl-4 pr-2">
            <textarea
              id={`${panelId}-input`}
              ref={input}
              data-testid="assistant-input"
              rows={1}
              className="block max-h-[9.5rem] min-h-10 min-w-0 flex-1 resize-none bg-transparent py-2 text-sm leading-6 text-[color:var(--fs-text)] outline-none focus-visible:shadow-none focus-visible:outline-none! disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)]"
              value={body}
              maxLength={4000}
              disabled={busy || retry}
              onChange={(event) => setBody(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (body.trim()) ask(typedMessage(body, turns, origins), true);
                }
              }}
            />
            {!retry && (
              <button
                type="submit"
                data-testid="assistant-send"
                className={`${sendLook} px-4`}
                disabled={busy || resumeTab !== null || !body.trim() || !loaded}
              >
                {busy ? text.button_sending : text.button_send}
                {!busy && <ArrowUp size={16} aria-hidden />}
              </button>
            )}
          </div>
          {retry && (
            <div className="flex flex-wrap items-center justify-end gap-2 px-2.5 pb-2.5">
              <button type="button" className={`${sendLook} px-4`} onClick={retrySame}>
                {text.retry_same}
              </button>
              <button type="button" className={headerButton} onClick={dismissRetry}>
                {text.start_new}
              </button>
            </div>
          )}
        </div>
      </form>
    </section>
  );
}

function TurnResult({
  result,
  language,
  origin,
  me,
  packages,
  tenant,
  outcomes,
  disabled,
  answerable,
  onSettled,
  onAsk,
}: {
  result: WorkspaceResult;
  /** The turn's language: every line written around its result follows it. */
  language: Language;
  /** The request that produced this result; follow-ups repeat it for the planner. */
  origin: string;
  me: Me;
  packages: PackageState[];
  tenant: string;
  outcomes: Record<string, ProposalOut>;
  disabled: boolean;
  /** False for every card but the newest: older choices are history, not live questions. */
  answerable: boolean;
  onSettled: (proposal: ProposalOut) => void;
  onAsk: (outgoing: Outgoing) => void;
}) {
  const view = resultView(result, language);
  const text = PANEL[language];
  const box =
    "rounded-[var(--fs-radius-bubble)] rounded-tl-[var(--fs-radius-control)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3 text-sm text-[color:var(--fs-text)]";
  const copy = (body: string) => (
    <div className="mt-1 -ml-2 flex items-center gap-1">
      <CopyReply body={body} />
    </div>
  );
  switch (view.kind) {
    case "notice":
      return <Notice error={view.error}>{view.text}</Notice>;
    case "guidance":
      return (
        <>
          <div className={box}>
            <p>{view.text}</p>
            <p className="mt-2 text-xs text-[color:var(--fs-text-secondary)]">
              {text.order_source}: {view.source} · {text.order_as_of} {new Date(view.asOf).toLocaleString()}
            </p>
          </div>
          {copy(view.text)}
        </>
      );
    case "order": {
      const { order } = view.result;
      const shown = orderCardText(view.result, language);
      return (
        <div className={box}>
          <p className="font-semibold">{shown.title}</p>
          <p>
            {order.status} · {text.order_payment}: {order.payment_status} · {order.currency} {order.total}
          </p>
          <p>
            {text.order_source}: {order.channel} · {text.order_tracking}:{" "}
            {order.tracking_no ?? text.order_not_recorded} · {text.order_carrier}:{" "}
            {order.carrier ?? text.order_not_recorded}
          </p>
          <p className="mt-2 text-xs text-[color:var(--fs-text-secondary)]">
            {text.order_source}: {view.result.source} · {text.order_marketplace_sync}:{" "}
            {shown.sync} · {text.order_as_of}{" "}
            {new Date(view.result.as_of).toLocaleString()}
          </p>
        </div>
      );
    }
    case "stock":
      // A TikTok Shop stock read: exact counts per variant, read-only.
      return (
        <div className={box} data-testid="assistant-stock">
          <p className="font-semibold break-words">{view.title}</p>
          <ul className="mt-1 space-y-0.5">
            {view.rows.map((row, index) => (
              <li key={index} className="flex justify-between gap-3">
                <span className="break-words">{row.label}</span>
                <span className="font-medium tabular-nums">{row.count}</span>
              </li>
            ))}
          </ul>
          {view.total && <p className="mt-1 font-medium">{view.total}</p>}
          {view.offSale && <p className="mt-1">{view.offSale}</p>}
          <p className="mt-2 text-xs text-[color:var(--fs-text-secondary)]">
            {view.note} · {text.order_as_of} {new Date(view.asOf).toLocaleString()}
          </p>
        </div>
      );
    case "proposal":
      return (
        <ProposalCard
          key={`${view.proposal.id}-${outcomes[view.proposal.id]?.status ?? ""}`}
          proposal={outcomes[view.proposal.id] ?? view.proposal}
          tenantId={tenant}
          onSettled={onSettled}
          onOpened={onSettled}
          outcomes={outcomes}
        />
      );
    case "answer":
      return (
        <>
          <p className={`${box} whitespace-pre-wrap break-words`}>{view.text}</p>
          {copy(view.text)}
        </>
      );
    case "training_answer":
      // An answer from a video's transcript, with a link to each moment it came from.
      return (
        <>
          <div className={box} data-testid="assistant-training-answer">
            <p className="whitespace-pre-wrap break-words">{view.text}</p>
            {view.sources.length > 0 && (
              <ul className="mt-3 flex flex-wrap gap-2">
                {view.sources.map((source, index) => (
                  <li key={`${source.itemId}-${source.seconds}-${index}`} className="min-w-0">
                    <Link
                      href={watchHref(source.itemId, source.seconds, me, tenant)}
                      data-testid="assistant-watch-link"
                      aria-label={`${source.label}: ${source.title}`}
                      className={`${starterLook} max-w-full break-words text-left no-underline`}
                    >
                      {source.label}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {copy(view.text)}
        </>
      );
    case "navigate": {
      const shown = navigateText(view.tab, view.resume, me, packages, language);
      return shown.error ? (
        <Notice error>{shown.text}</Notice>
      ) : (
        <p className="pt-1 text-sm text-[color:var(--fs-text-secondary)]">{shown.text}</p>
      );
    }
    case "directory":
      return (
        <div className={box}>
          <p className="mb-2">{text.choose_colleague}</p>
          <ul className="space-y-2">
            {view.candidates.map((person) => (
              <li key={person.employee_id}>
                <button
                  type="button"
                  data-testid="assistant-candidate"
                  className={`${secondary} w-full justify-start text-left`}
                  disabled={disabled || !answerable}
                  onClick={() => onAsk(candidateAnswer(person, origin))}
                >
                  <span>
                    <span className="font-medium">{person.display_name}</span>
                    {(person.job_title || person.work_email) && (
                      <span className="block text-xs text-[color:var(--fs-text-secondary)]">
                        {[person.job_title, person.work_email].filter(Boolean).join(" · ")}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      );
    case "clarification":
      return (
        <div className={box}>
          <p>{view.question}</p>
          {view.options.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {view.options.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`${starterLook} max-w-full break-words text-left`}
                  disabled={disabled || !answerable}
                  onClick={() => onAsk(optionAnswer(option, view.question, origin))}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </div>
      );
  }
}
