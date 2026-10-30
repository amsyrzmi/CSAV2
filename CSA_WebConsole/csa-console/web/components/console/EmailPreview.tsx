import type { Draft } from "@/lib/email-api";
import { isReply, OUTSIDE_LABEL, recipientGroups } from "@/lib/email-recipients";

/** Exactly what will go out: To first and largest, since a reply's To can differ from the sender. */
export function EmailPreview({ draft, recipientsTestId, bodyTestId }: {
  draft: Draft; recipientsTestId?: string; bodyTestId?: string;
}) {
  return (
    <div className="space-y-3 rounded-md border border-ink-200 p-3 text-sm">
      <div data-testid={recipientsTestId} className="space-y-1">
        {recipientGroups(draft).map((group) => (
          <div
            key={group.label}
            className={`flex flex-wrap items-baseline gap-x-2 gap-y-1 ${group.label === "To" ? "text-base font-semibold" : ""}`}
          >
            <span className="w-10 shrink-0 text-xs font-normal text-ink-500">{group.label}</span>
            {group.people.map((person) => (
              <span key={`${group.label}-${person.address}`} className="break-all">
                {person.address}
                {person.outside && (
                  <span className="ml-1 rounded bg-amber-100 px-1 text-xs font-medium text-amber-900">
                    {OUTSIDE_LABEL}
                  </span>
                )}
              </span>
            ))}
          </div>
        ))}
        {isReply(draft) && (
          <p className="text-xs text-ink-500">
            Replies go to the address the email asked for, which may not be the person who wrote it. Check the To address.
          </p>
        )}
      </div>
      <div>
        <div className="text-xs text-ink-500">Subject</div>
        <div className="break-words font-medium">{draft.subject}</div>
      </div>
      <pre data-testid={bodyTestId} className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans text-sm">
        {draft.body}
      </pre>
    </div>
  );
}
