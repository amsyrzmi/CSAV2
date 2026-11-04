import { ArrowDown } from "lucide-react";

/** A pill over the end of the log, offered when the reader has scrolled up by more than a screen. */
export function JumpToLatest({ onJump }: { onJump: () => void }) {
  return <button type="button" data-testid="chat-jump-latest" onClick={onJump}
    className="absolute bottom-3 left-1/2 inline-flex min-h-10 -translate-x-1/2 items-center gap-2 rounded-[var(--fs-radius-round)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] px-4 text-sm font-medium text-[color:var(--fs-text)] shadow-[var(--fs-shadow-card)] transition-colors duration-150 hover:border-[color:var(--fs-primary)] hover:bg-[var(--fs-surface-blue)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:min-h-11">
    <ArrowDown size={16} aria-hidden />Jump to latest</button>;
}
