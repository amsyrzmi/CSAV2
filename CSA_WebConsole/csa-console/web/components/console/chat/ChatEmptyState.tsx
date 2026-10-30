import AgentAvatar from "../fs/AgentAvatar";
import { SandboxLimits } from "./ChatDisclaimer";

/** The assistant in a rounded tile, above a line of copy (and, inside a tenant, more below). */
export function AssistantTile() {
  return <span className="grid size-14 shrink-0 place-items-center rounded-2xl bg-[var(--fs-surface-blue)]">
    <AgentAvatar agentId="assistant" size={48} label="" />
  </span>;
}

/**
 * An empty conversation: the assistant, "How can I help today?", the full limits notice and the
 * prompt chips. A chip only fills the composer; nothing is sent by a click.
 */
export function ChatEmptyState({ chips, disabled, onChip }: {
  chips: string[];
  disabled: boolean;
  onChip: (chip: string) => void;
}) {
  return <div className="my-auto flex flex-col items-center gap-5 py-4 text-center sm:py-8">
    <AssistantTile />
    <h2 className="font-(family-name:--fs-font-heading) text-xl font-semibold text-[color:var(--fs-text)] sm:text-2xl">
      How can I help today?</h2>
    <div className="w-full max-w-xl text-left"><SandboxLimits /></div>
    <ul data-testid="sandbox-chips" aria-label="Suggested questions" className="flex flex-wrap justify-center gap-2">
      {chips.map((chip) => <li key={chip}>
        <button type="button" disabled={disabled} onClick={() => onChip(chip)}
          className="min-h-10 rounded-full border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] px-4 py-2 text-sm text-[color:var(--fs-text)] transition-colors duration-150 hover:border-[color:var(--fs-primary)] hover:bg-[var(--fs-surface-blue)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:border-[color:var(--fs-border)] disabled:bg-[var(--fs-disabled-bg)] disabled:text-[color:var(--fs-disabled-text)] pointer-coarse:min-h-11">
          {chip}</button>
      </li>)}
    </ul>
  </div>;
}
