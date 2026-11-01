import { PANEL, type Language } from "@/lib/assistant-copy";
import type { Draft } from "@/lib/email-api";
import { isReply, OUTSIDE_LABELS, recipientGroups } from "@/lib/email-recipients";

/** Exactly what will go out: To first and largest, since a reply's To can differ from the sender. */
export function EmailPreview({ draft, recipientsTestId, bodyTestId, language = "en" }: {
  draft: Draft; recipientsTestId?: string; bodyTestId?: string; language?: Language;
}) {
  return (
    <div className="space-y-3 rounded-md border border-ink-200 p-3 text-sm">
      <div data-testid={recipientsTestId} className="space-y-1">
        {recipientGroups(draft, language).map((group) => (
          <div
            key={group.key}
            className={`flex flex-wrap items-baseline gap-x-2 gap-y-1 ${group.key === "to" ? "text-base font-semibold" : ""}`}
          >
            <span className="w-10 shrink-0 text-xs font-normal text-ink-500">{group.label}</span>
            {group.people.map((person) => (
              <span key={`${group.key}-${person.address}`} className="break-all">
                {person.address}
                {person.outside && (
                  <span className="ml-1 rounded bg-amber-100 px-1 text-xs font-medium text-amber-900">
                    {OUTSIDE_LABELS[language]}
                  </span>
                )}
              </span>
            ))}
          </div>
        ))}
        {isReply(draft) && (
          <p className="text-xs text-ink-500">
            {PANEL[language].email_reply_note}
          </p>
        )}
      </div>
      <div>
        <div className="text-xs text-ink-500">{PANEL[language].email_subject}</div>
        <div className="break-words font-medium">{draft.subject}</div>
      </div>
      <pre data-testid={bodyTestId} className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans text-sm">
        {draft.body}
      </pre>
    </div>
  );
}
