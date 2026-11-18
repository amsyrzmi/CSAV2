"use client";

import Dialog from "@/components/console/fs/Dialog";
import { SHORTCUT_HELP } from "@/lib/inbox-shortcuts";

/**
 * The "?" help: every Inbox shortcut in words. A native dialog (`fs/Dialog`), so Escape and Close
 * shut it and focus goes back to where it was when it opened (the "Keyboard shortcuts" button, or
 * the row or page the "?" was pressed on). A key shown as a sign also has its name for screen readers.
 */
export function ShortcutHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <Dialog open={open} onClose={onClose} title="Keyboard shortcuts" testId="inbox-shortcut-help">
    <p className="mb-3 text-sm text-[color:var(--fs-text-secondary)]">
      They work when you are not typing in a box and no menu or dialog is open. Each one is also a
      button on the screen.
    </p>
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3 gap-y-2 text-sm">
      {SHORTCUT_HELP.map((entry) => <div key={entry.key} className="contents">
        <dt className="min-w-0">
          <kbd className="inline-flex min-w-7 justify-center rounded-[var(--fs-radius-control)] border border-[color:var(--fs-border-strong)] bg-[var(--fs-tile)] px-1.5 py-0.5 text-xs font-semibold text-[color:var(--fs-text)]">
            {entry.key}</kbd>
          {entry.name !== entry.key && <span className="sr-only"> ({entry.name})</span>}
        </dt>
        <dd className="min-w-0 text-[color:var(--fs-text)] [overflow-wrap:anywhere]">{entry.text}</dd>
      </div>)}
    </dl>
  </Dialog>;
}
