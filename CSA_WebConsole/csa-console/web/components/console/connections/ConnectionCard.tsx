import Link from "next/link";
import type { ReactNode } from "react";
import { MANAGE_LINK } from "@/lib/connections-view";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";

/** One connection as the tab words it: the state word, its sentence and when it last did something. */
export type ConnectionView = { tone: StatusTone; word: string; sentence: string; lastActivity: string | null };

const muted = "text-sm text-[color:var(--fs-text-secondary)]";

/** A card of the Connections tab: a heading and the rows under it. */
export function ConnectionCard({ testId, title, children }: { testId: string; title: string; children: ReactNode }) {
  return <section data-testid={testId} className="card rounded-[14px] p-5 sm:p-6">
    <h3 className="mb-4 font-(family-name:--fs-font-heading) text-base font-semibold">{title}</h3>
    <div className="space-y-5">{children}</div>
  </section>;
}

/**
 * One connection in a card. A locked row (its package is off) shows only its name and the
 * "Included in ..." sentence; every other row shows the state word, its sentence, when it last
 * did something, a note under a disabled button, and the actions.
 */
export function ConnectionRow({ testId, name, view, locked, note, actions }: {
  testId: string; name: string; view: ConnectionView | null; locked?: string | null;
  note?: string | null; actions?: ReactNode;
}) {
  if (locked) {
    return <div data-testid={testId}>
      <h4 className="font-medium">{name}</h4>
      <p data-testid={`${testId}-locked`} className={`mt-1 ${muted}`}>{locked}</p>
    </div>;
  }
  return <div data-testid={testId}>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <h4 className="font-medium">{name}</h4>
      {view && <span data-testid={`${testId}-word`}><StatusLabel tone={view.tone}>{view.word}</StatusLabel></span>}
    </div>
    {view && <p data-testid={`${testId}-sentence`} className="mt-1 text-sm [overflow-wrap:anywhere]">{view.sentence}</p>}
    {view?.lastActivity && <p data-testid={`${testId}-last`} className={`mt-1 ${muted}`}>{view.lastActivity}</p>}
    {actions && <div className="mt-3 flex flex-wrap items-center gap-3">{actions}</div>}
    {note && <p data-testid={`${testId}-note`} className={`mt-2 ${muted}`}>{note}</p>}
  </div>;
}

/**
 * What an old page keeps of a connection: "{name}: {word}." and the way to the tab that owns
 * it. With no word read yet it is only the link.
 */
export function ManageInConnections({ testId, name, word, href }: {
  testId: string; name: string; word: string | null; href: string;
}) {
  return <p data-testid={testId} className="text-sm">
    {word !== null && <span data-testid={`${testId}-status`}>{name}: {word}. </span>}
    <Link className="underline" href={href} data-testid={`${testId}-link`}>{MANAGE_LINK}</Link>
  </p>;
}
