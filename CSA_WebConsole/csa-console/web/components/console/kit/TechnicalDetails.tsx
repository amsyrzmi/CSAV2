"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { secondary } from "../ui";
import DetailsRow from "./DetailsRow";

type TechState = { all: boolean; revision: number; setAll: (next: boolean) => void };

const TechContext = createContext<TechState | null>(null);

/**
 * Holds the page-wide "Show technical details" choice. Every `useTechOpen` row starts at `all`
 * and snaps back to it whenever the choice is pressed (`revision`), so one press opens or
 * closes every row, including rows that load later; a single row can still be toggled after.
 */
export function TechDetailsProvider({ children }: { children: ReactNode }) {
  const [all, setAllState] = useState(false);
  const [revision, setRevision] = useState(0);
  const value = useMemo<TechState>(
    () => ({ all, revision, setAll: (next) => { setAllState(next); setRevision((n) => n + 1); } }),
    [all, revision],
  );
  return <TechContext.Provider value={value}>{children}</TechContext.Provider>;
}

/** The page switch: same words pressed or not; the state is `aria-pressed` and a check mark. */
export function TechSwitch({ testId }: { testId: string }) {
  const tech = useContext(TechContext);
  const on = tech?.all ?? false;
  return (
    <button
      type="button"
      className={secondary}
      aria-pressed={on}
      data-testid={testId}
      onClick={() => tech?.setAll(!on)}
    >
      {on ? (
        <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
          strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
          <path d="m5 12 5 5 9-10" />
        </svg>
      ) : null}
      Show technical details
    </button>
  );
}

/** Open state for one row: starts at the page switch, resets when it is pressed; local without a provider. */
export function useTechOpen(): [boolean, (open: boolean) => void] {
  const tech = useContext(TechContext);
  const all = tech?.all ?? false;
  const revision = tech?.revision ?? 0;
  const [open, setOpen] = useState(all);
  const [seen, setSeen] = useState(revision);
  if (seen !== revision) {
    setSeen(revision);
    setOpen(all);
  }
  return [open, setOpen];
}

/** The second level of a record row: a nested "Technical details" arrow holding the exact lines. */
export function TechnicalDetails({ testId, children }: { testId: string; children: ReactNode }) {
  const [open, setOpen] = useTechOpen();
  return (
    <div className="mt-3 border-t border-[color:var(--fs-border)] pt-2">
      <DetailsRow
        testId={testId}
        toggleLabel="technical details"
        summary={<p className="font-medium">Technical details</p>}
        open={open}
        onOpenChange={setOpen}
      >
        {children}
      </DetailsRow>
    </div>
  );
}
