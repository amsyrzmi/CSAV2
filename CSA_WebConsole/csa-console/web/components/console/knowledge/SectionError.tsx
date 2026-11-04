import { secondary } from "../ui";

/**
 * The inline notice for one list that could not be read. With rows already on screen it says
 * they may be out of date; without, it says the list could not be loaded. Either way it carries
 * the server's own sentence and a retry that reads only this list.
 */
export default function SectionError({
  name,
  what,
  message,
  stale = false,
  onRetry,
  testId,
}: {
  name: string;
  what: string;
  message: string;
  stale?: boolean;
  onRetry: () => void;
  testId?: string;
}) {
  return (
    <div
      role="alert"
      data-testid={testId ?? `kb-error-${name}`}
      className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--fs-radius-control)] border border-[var(--fs-danger)]/30 bg-[var(--fs-danger-bg)] p-3 text-sm text-[var(--fs-danger)]"
    >
      <p className="min-w-0 flex-1 basis-56 [overflow-wrap:anywhere]">
        {stale ? `We could not refresh ${what}, so this may be out of date.` : `We could not load ${what}.`} {message}
      </p>
      <button type="button" className={secondary} data-testid={`kb-retry-${name}`} onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}
