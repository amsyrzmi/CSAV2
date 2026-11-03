"use client";

import { UploadCloud } from "lucide-react";
import { useState, type ChangeEvent, type DragEvent, type RefObject } from "react";
import { formatBytes } from "@/lib/knowledge-view";
import { secondary } from "../ui";

/**
 * The upload area: a dashed box to drop files on, a "Choose files" button, and the list of what
 * is chosen. The real file input stays in the page (visually hidden, never removed) so the
 * picker, its label and its limits are unchanged; a drop goes through the same `onFiles` as the
 * picker, so the page applies the same rules to both.
 */
export default function UploadDrop({
  inputRef,
  disabled,
  selection,
  onFiles,
}: {
  inputRef: RefObject<HTMLInputElement | null>;
  disabled: boolean;
  selection: File[] | null;
  onFiles: (files: File[]) => void;
}) {
  const [dragging, setDragging] = useState(false);
  // Always cancel the browser's own handling, or a file dropped on a disabled area would open in
  // the browser and take the person out of the console; only an enabled area takes the files.
  const over = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!disabled) setDragging(true);
  };
  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    onFiles(Array.from(event.dataTransfer.files));
  };
  const pick = (event: ChangeEvent<HTMLInputElement>) => onFiles(Array.from(event.target.files ?? []));
  return (
    <div className="min-w-0">
      <label htmlFor="kb-files" className="mb-2 block text-sm">One to three PDF, DOCX or CSV files, at most 10 MB each</label>
      <div
        data-testid="kb-drop"
        data-dragging={dragging ? "true" : "false"}
        onDragOver={over}
        onDragEnter={over}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
        className="flex flex-col items-center gap-3 rounded-[var(--fs-radius-panel)] border-2 border-dashed border-[color:var(--fs-border-strong)] bg-[var(--fs-surface)] p-5 text-center text-sm data-[dragging=true]:border-[color:var(--fs-primary)] data-[dragging=true]:bg-[var(--fs-surface-blue)]"
      >
        <UploadCloud aria-hidden="true" size={32} className="text-[color:var(--fs-primary)]" />
        <p>Drag files here, or</p>
        <button type="button" className={secondary} disabled={disabled} onClick={() => inputRef.current?.click()}>
          Choose files
        </button>
        <input ref={inputRef} id="kb-files" type="file" multiple accept=".pdf,.docx,.csv" disabled={disabled}
          tabIndex={-1} className="sr-only" onChange={pick} />
      </div>
      {selection && selection.length > 0 ? (
        <ul data-testid="kb-selection" className="mt-3 space-y-1 text-sm [overflow-wrap:anywhere]">
          {selection.map((file) => <li key={`${file.name}-${file.size}`}>{file.name} <span className="text-[color:var(--fs-text-secondary)]">({formatBytes(file.size)})</span></li>)}
        </ul>
      ) : <p data-testid="kb-selection" className="mt-3 text-sm text-[color:var(--fs-text-secondary)]">No file chosen yet</p>}
    </div>
  );
}
