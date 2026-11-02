"use client";

import type { ChangeEvent, RefObject } from "react";
import { action, secondary } from "../ui";

export type AgentState = "loading" | "ready" | "failed";

/**
 * The first thing on the page: the upload control with the three plain steps beside it. The
 * control itself, its limits and its wording are unchanged; the page never takes it away while
 * a list reloads.
 */
export default function AddKnowledge({
  inputRef,
  agent,
  writable,
  busy,
  selection,
  onChange,
  onUpload,
  onRetryAgent,
}: {
  inputRef: RefObject<HTMLInputElement | null>;
  agent: AgentState;
  writable: boolean;
  busy: boolean;
  selection: File[] | null;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onUpload: () => void;
  onRetryAgent: () => void;
}) {
  return (
    <section data-testid="kb-add" className="card rounded-[14px] p-5 sm:p-6">
      <h2 className="mb-4 font-(family-name:--fs-font-heading) text-base font-semibold">Add knowledge</h2>
      <div className="grid gap-6 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="min-w-0">
          <label htmlFor="kb-files" className="mb-2 block text-sm">One to three PDF, DOCX or CSV files, at most 10 MB each</label>
          <input ref={inputRef} id="kb-files" type="file" multiple accept=".pdf,.docx,.csv" disabled={busy || !writable}
            className="block w-full max-w-full text-sm" onChange={onChange} />
          {selection && <p className="mt-3 text-sm [overflow-wrap:anywhere]">{selection.map((file) => file.name).join(", ")}</p>}
          <button type="button" className={`${action} mt-4`} disabled={busy || !writable || !selection} onClick={onUpload}>
            {busy ? "Working..." : "Queue upload / retry"}
          </button>
          {agent === "ready" && !writable && (
            <p data-testid="kb-add-off" className="mt-3 text-sm text-[color:var(--fs-text-secondary)]">
              Adding is switched off for your assistant right now. Ask your administrator to switch it on in Services.
            </p>
          )}
          {agent === "loading" && (
            <p className="mt-3 text-sm text-[color:var(--fs-text-secondary)]">Checking whether adding is switched on...</p>
          )}
          {agent === "failed" && (
            <p data-testid="kb-add-unknown" className="mt-3 flex flex-wrap items-center gap-2 text-sm text-[color:var(--fs-text-secondary)]">
              We could not check whether adding is switched on, so it is paused.
              <button type="button" className={secondary} data-testid="kb-retry-agents" onClick={onRetryAgent}>Try again</button>
            </p>
          )}
        </div>
        <ol className="list-decimal space-y-2 pl-5 text-sm marker:font-semibold marker:text-[color:var(--fs-primary)]">
          <li>Add a file</li>
          <li>Your assistant reads it</li>
          <li>It answers from it</li>
        </ol>
      </div>
    </section>
  );
}
