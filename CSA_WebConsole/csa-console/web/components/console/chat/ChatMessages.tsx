"use client";

import { useState } from "react";
import { Volume2 } from "lucide-react";
import type { SandboxTurn } from "@/lib/sandbox-chat";
import { followUpLabel, isWorking, sandboxDelayed, sandboxStatus } from "@/lib/sandbox-chat";
import AgentAvatar from "../fs/AgentAvatar";
import { useMotionAllowed } from "../fs/useMotionAllowed";
import { Notice } from "../ui";
import { starterLook } from "./ChatEmptyState";
import { CopyReply } from "./CopyReply";
import { WorkingRow } from "./WorkingRow";

const smallButton =
  "inline-grid size-9 place-items-center rounded-[var(--fs-radius-control)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)] pointer-coarse:size-11";

/**
 * The conversation log: each question right-aligned in a tinted bubble with its recorded status
 * under it, each reply on the left beside the assistant with Read aloud and Copy. While the server
 * is working on a turn (`isWorking`) the assistant shows its working pose beside "Working on it".
 * What arrives after the conversation first showed eases in (not under reduced motion or the pause
 * preference). Follow-up starters sit under the log, after the latest reply, and only fill the
 * composer. Status words are the sandbox's own.
 */
export function ChatMessages({ turns, now, onReadAloud, readDisabled, followUps, onFollowUp }: {
  turns: SandboxTurn[];
  now: number;
  onReadAloud: (body: string) => void;
  readDisabled: boolean;
  /** Starters to offer under the latest reply; null when none should show. */
  followUps: string[] | null;
  onFollowUp: (starter: string) => void;
}) {
  const motion = useMotionAllowed();
  // Everything on screen when the log first mounted is not "new"; whatever shows up later is.
  const [initial] = useState(() => new Set(turns.flatMap((turn) => [`t${turn.id}`, ...turn.replies.map((reply) => `r${reply.id}`)])));
  const arrives = (key: string) => (initial.has(key) ? "" : motion ? " fs-message-in" : "");
  const workingId = turns.findLast((turn) => isWorking(turn.status))?.id;
  return <>
    <div className="space-y-6" role="log" aria-live="polite" aria-label="Chat messages">
      {turns.map((turn) => <article key={turn.id} data-arrived={initial.has(`t${turn.id}`) ? undefined : "true"}
        className={`space-y-4${arrives(`t${turn.id}`)}`}>
        <div className="flex flex-col items-end gap-1.5 pl-8 sm:pl-16">
          <p className="max-w-full whitespace-pre-wrap break-words rounded-[var(--fs-radius-bubble)] rounded-br-[var(--fs-radius-control)] bg-[var(--fs-surface-blue)] px-4 py-2.5 text-[color:var(--fs-text)]">
            <span className="sr-only">You: </span>{turn.body}</p>
          <p className="text-right text-xs text-[color:var(--fs-text-secondary)]">{sandboxStatus[turn.status]}</p>
        </div>
        {sandboxDelayed(turn, now) && <Notice>This is taking longer than usual. It may still arrive. Do not send it again yet; use Check for new messages.</Notice>}
        {turn.id === workingId && <WorkingRow />}
        {turn.replies.map((reply) => <div key={reply.id} className={`flex items-start gap-3${arrives(`r${reply.id}`)}`}>
          <AgentAvatar agentId="assistant" size={32} state="rest" label="" />
          <div className="min-w-0 flex-1">
            <p className="whitespace-pre-wrap break-words pt-1 leading-relaxed text-[color:var(--fs-text)]">
              <span className="sr-only">Assistant: </span>{reply.body}</p>
            <div className="mt-1 -ml-2 flex items-center gap-1">
              <button type="button" aria-label="Read reply aloud" title="Read reply aloud" disabled={readDisabled}
                onClick={() => onReadAloud(reply.body)} className={smallButton}>
                <Volume2 size={16} aria-hidden /></button>
              <CopyReply body={reply.body} />
            </div>
          </div>
        </div>)}
      </article>)}
    </div>
    {followUps && <ul data-testid="chat-followups" aria-label="Follow-up suggestions" className="flex flex-wrap gap-2 sm:pl-11">
      {followUps.map((starter) => <li key={starter}>
        <button type="button" className={starterLook} onClick={() => onFollowUp(starter)}>{followUpLabel(starter)}</button>
      </li>)}
    </ul>}
  </>;
}
