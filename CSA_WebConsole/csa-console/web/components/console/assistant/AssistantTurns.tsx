"use client";

import Link from "next/link";
import type { Ref } from "react";
import { languageOf, panelText, PANEL, type Language } from "@/lib/assistant-copy";
import type { AssistantConversation } from "@/lib/assistant-conversation";
import {
  candidateAnswer,
  isAnswerable,
  navigateText,
  optionAnswer,
  orderCardText,
  originFor,
  resultView,
  tabLabel,
  watchHref,
  type Outgoing,
} from "@/lib/assistant-view";
import type { Me } from "@/lib/identity-contracts";
import type { PackageState } from "@/lib/services-api";
import type { ProposalOut, WorkspaceResult } from "@/lib/workspace-api";
import { CopyReply } from "../chat/CopyReply";
import { starterLook } from "../chat/ChatEmptyState";
import AgentAvatar from "../fs/AgentAvatar";
import { ProposalCard } from "../ProposalCard";
import { Notice, secondary } from "../ui";

/**
 * The turns of the shared conversation: each request with its result, the working row, and the
 * resume and error notices. It has no box of its own beyond the scroll region; the dock and the
 * Overview page give it a width and a height, and `listRef` reaches the scroll element.
 *
 * The scroll region is `flex-1 overflow-y-auto`: it scrolls only inside a flex-column parent
 * whose height is bounded (the dock's max-height panel, the Overview page's full-height column).
 * Elsewhere it grows with its turns and the page scrolls instead.
 */
export function AssistantTurns({
  conversation,
  listRef,
}: {
  conversation: AssistantConversation;
  listRef?: Ref<HTMLDivElement>;
}) {
  const {
    tenant,
    me,
    packages,
    loaded,
    turns,
    outcomes,
    origins,
    busy,
    error,
    resumeTab,
    language,
    ask,
    settle,
  } = conversation;
  const text = PANEL[language];
  return (
    <div
      ref={listRef}
      data-testid="assistant-scroll"
      className="flex-1 space-y-4 overflow-y-auto px-4 py-3"
      aria-live="polite"
    >
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
  );
}

/** One turn's result: the card, answer, notice, choice or proposal it stands for. */
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
