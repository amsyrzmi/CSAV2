import { Volume2 } from "lucide-react";
import type { SandboxTurn } from "@/lib/sandbox-chat";
import { sandboxDelayed, sandboxStatus } from "@/lib/sandbox-chat";
import AgentAvatar from "../fs/AgentAvatar";
import { Notice } from "../ui";

/**
 * The conversation log: each question right-aligned in a tinted bubble with its recorded status
 * under it, each reply on the left beside the assistant. Status words are the sandbox's own.
 */
export function ChatMessages({ turns, now, onReadAloud, readDisabled }: {
  turns: SandboxTurn[];
  now: number;
  onReadAloud: (body: string) => void;
  readDisabled: boolean;
}) {
  return <div className="space-y-6" role="log" aria-live="polite" aria-label="Chat messages">
    {turns.map((turn) => <article key={turn.id} className="space-y-4">
      <div className="flex flex-col items-end gap-1.5 pl-8 sm:pl-16">
        <p className="max-w-full whitespace-pre-wrap break-words rounded-[var(--fs-radius-bubble)] rounded-br-[var(--fs-radius-control)] bg-[var(--fs-surface-blue)] px-4 py-2.5 text-[color:var(--fs-text)]">
          <span className="sr-only">You: </span>{turn.body}</p>
        <p className="text-right text-xs text-[color:var(--fs-text-secondary)]">{sandboxStatus[turn.status]}</p>
      </div>
      {sandboxDelayed(turn, now) && <Notice>This is taking longer than usual. It may still arrive. Do not send it again yet; use Check for new messages.</Notice>}
      {turn.replies.map((reply) => <div key={reply.id} className="flex items-start gap-3">
        <AgentAvatar agentId="assistant" size={32} state="rest" label="" />
        <div className="min-w-0 flex-1">
          <p className="whitespace-pre-wrap break-words pt-1 leading-relaxed text-[color:var(--fs-text)]">
            <span className="sr-only">Assistant: </span>{reply.body}</p>
          <button type="button" aria-label="Read reply aloud" title="Read reply aloud" disabled={readDisabled}
            onClick={() => onReadAloud(reply.body)}
            className="mt-1 -ml-2 inline-grid size-9 place-items-center rounded-[var(--fs-radius-control)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)] pointer-coarse:size-11">
            <Volume2 size={16} aria-hidden /></button>
        </div>
      </div>)}
    </article>)}
  </div>;
}
