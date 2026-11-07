"use client";

import { useId, useRef, useState, type RefObject } from "react";
import { ApiError } from "@/lib/api";
import { patchItem, removeItem, uploadItem, type TrainingItem, type TrainingViewMode } from "@/lib/training-api";
import {
  afterUploadFailure,
  attemptFor,
  EMPTY_FORM,
  formFromItem,
  formProblems,
  formToPatch,
  PATCH_FAILED,
  titleFromFileName,
  uploadMayHaveLanded,
  type FormProblems,
  type ItemForm,
  type UploadAttempt,
} from "@/lib/training-form";
import { uploadProblem } from "@/lib/training-view";
import Dialog from "../fs/Dialog";
import { action, field, Notice, secondary } from "../ui";
import { codeOf, described, FormRow, muted, refusalText } from "./shared";

type Phase = "idle" | "uploading" | "saving";

/**
 * Add a PDF (`item` null) or edit one's details. Adding is two steps: the file alone, then the
 * details as a PATCH. One idempotency key belongs to the picked file and is kept for a retry of
 * it, so a retry after a lost answer returns the first item; picking another file starts a new
 * key. The page owns the key (`attempt`), so closing the dialog and adding the same file again
 * still retries. When the second step fails the PDF is already in the library under its file
 * name: the dialog says so and its button then saves only the details.
 */
export default function ItemDialog({
  tenantId,
  item,
  categories,
  onClose,
  onSaved,
  onChanged,
  attempt: pageAttempt,
}: {
  tenantId: string | null;
  item: TrainingItem | null;
  /** The company's categories, offered as the person types. */
  categories: string[];
  onClose: () => void;
  /** The details are saved; `added` when this dialog added the PDF. */
  onSaved: (saved: TrainingItem, added: boolean) => void;
  /** The library changed (a PDF was added) and the list should be read again. */
  onChanged: () => void;
  /** The page's upload attempt, kept while the dialog is closed; the dialog's own when absent. */
  attempt?: RefObject<UploadAttempt | null>;
}) {
  const ids = useId();
  const id = (name: string) => `${ids}-${name}`;
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState<ItemForm>(item ? formFromItem(item) : EMPTY_FORM);
  const [problems, setProblems] = useState<FormProblems>({});
  const [problem, setProblem] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [uploaded, setUploaded] = useState<TrainingItem | null>(null);
  const ownAttempt = useRef<UploadAttempt | null>(null);
  const attempt = pageAttempt ?? ownAttempt;
  const running = useRef(false);
  const busy = phase !== "idle";
  const adding = item === null;
  // What a PATCH applies to: the item being edited, or the PDF this dialog has added.
  const target = item ?? uploaded;

  const change = <K extends keyof ItemForm>(key: K, value: ItemForm[K]) => setForm((before) => ({ ...before, [key]: value }));

  function pick(next: File | null) {
    // The title follows the file until the person writes their own.
    setForm((before) =>
      !before.title.trim() || (file && before.title === titleFromFileName(file.name))
        ? { ...before, title: next ? titleFromFileName(next.name) : "" }
        : before,
    );
    setFile(next);
    setProblem(next ? (uploadProblem(next) ?? "") : "");
  }

  async function submit() {
    if (running.current) return;
    const found = formProblems(form);
    setProblems(found);
    if (!target) {
      const fileProblem = file ? uploadProblem(file) : "Choose a PDF file.";
      if (fileProblem) {
        setProblem(fileProblem);
        return;
      }
    }
    if (Object.keys(found).length) {
      setProblem("");
      return;
    }
    running.current = true;
    setProblem("");
    try {
      let saved = target;
      if (!saved && file) {
        const current = attemptFor(attempt.current, file, () => crypto.randomUUID());
        attempt.current = current;
        setPhase("uploading");
        try {
          saved = await uploadItem(tenantId, file, current.key);
        } catch (cause) {
          attempt.current = afterUploadFailure(current, codeOf(cause));
          setProblem(refusalText(cause));
          // The answer may be lost after the PDF was stored: show it in the list.
          if (uploadMayHaveLanded(codeOf(cause), cause instanceof ApiError ? cause.status : null)) onChanged();
          return;
        }
        attempt.current = null;
        setUploaded(saved);
        // "Show as" shows the server's default for this PDF, unless the person already chose.
        const mode = saved.view_mode;
        setForm((before) => (before.view_mode ? before : { ...before, view_mode: mode }));
        onChanged();
      }
      if (!saved) return;
      const body = formToPatch(form, saved);
      if (!Object.keys(body).length) {
        onSaved(saved, adding);
        return;
      }
      setPhase("saving");
      try {
        onSaved(await patchItem(tenantId, saved.id, body), adding);
      } catch (cause) {
        setProblem(adding ? PATCH_FAILED : refusalText(cause));
      }
    } finally {
      running.current = false;
      setPhase("idle");
    }
  }

  const modes: { value: TrainingViewMode | ""; label: string }[] = [
    ...(target ? [] : [{ value: "" as const, label: "Choose from the first page" }]),
    { value: "slides", label: "Slides" },
    { value: "document", label: "Document" },
  ];
  const title = item ? "Edit details" : uploaded ? "Add the details" : "Add a PDF";

  return (
    <Dialog open onClose={() => !busy && onClose()} title={title} testId="training-dialog">
      <form
        noValidate
        className="space-y-4"
        aria-busy={busy}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {problem && <Notice error testId="training-dialog-error">{problem}</Notice>}
        <fieldset disabled={busy} className="min-w-0 space-y-4">
          {adding && !uploaded && (
            <FormRow id={id("file")} label="PDF file" hint="One PDF, up to 25 MB and 300 pages.">
              <input
                {...described(id("file"), true)}
                type="file"
                accept="application/pdf,.pdf"
                data-testid="training-file"
                className={`${muted} block w-full min-w-0 file:mr-3 file:min-h-10 file:rounded-[var(--fs-radius-control)] file:border file:border-[color:var(--fs-border-control)] file:bg-[var(--fs-surface)] file:px-3 file:text-[color:var(--fs-text)]`}
                onChange={(event) => pick(event.target.files?.[0] ?? null)}
              />
            </FormRow>
          )}
          {uploaded && <p className={muted} data-testid="training-uploaded">The PDF is in the library. Its details are not saved yet.</p>}
          <FormRow id={id("title")} label="Title" problem={problems.title}>
            <input
              {...described(id("title"), false, problems.title)}
              className={`${field} min-w-0`}
              data-testid="training-title"
              value={form.title}
              onChange={(event) => change("title", event.target.value)}
            />
          </FormRow>
          <FormRow id={id("description")} label="Description (optional)" problem={problems.description}>
            <textarea
              {...described(id("description"), false, problems.description)}
              className={`${field} h-auto min-h-24 min-w-0 py-2`}
              rows={3}
              data-testid="training-description"
              value={form.description}
              onChange={(event) => change("description", event.target.value)}
            />
          </FormRow>
          <FormRow
            id={id("category")}
            label="Category (optional)"
            hint="Choose one your company already uses, or type a new one."
            problem={problems.category}
          >
            <input
              {...described(id("category"), true, problems.category)}
              className={`${field} min-w-0`}
              data-testid="training-category"
              list={id("categories")}
              autoComplete="off"
              value={form.category}
              onChange={(event) => change("category", event.target.value)}
            />
            <datalist id={id("categories")}>
              {categories.map((name) => <option key={name} value={name} />)}
            </datalist>
          </FormRow>
          <FormRow
            id={id("mode")}
            label="Show as"
            hint="Slides show one page at a time. Document shows every page in one scroll. Each reader can switch."
          >
            <select
              {...described(id("mode"), true)}
              className={field}
              data-testid="training-view-mode"
              value={form.view_mode ?? ""}
              onChange={(event) => change("view_mode", (event.target.value || null) as TrainingViewMode | null)}
            >
              {modes.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
            </select>
          </FormRow>
        </fieldset>
        <div role="status" aria-live="polite" data-testid="training-progress" className="empty:hidden">
          {phase === "uploading" && (
            <span className="flex flex-wrap items-center gap-2 text-sm">
              <progress aria-label="Uploading the PDF" className="h-2 w-32 accent-[var(--fs-primary)]" />
              Uploading…
            </span>
          )}
          {phase === "saving" && <span className="text-sm">Saving the details…</span>}
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={action} disabled={busy} data-testid="training-dialog-save">
            {target ? "Save details" : "Add PDF"}
          </button>
          <button type="button" className={secondary} disabled={busy} onClick={onClose}>
            {uploaded ? "Close" : "Cancel"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Asks before a PDF is removed for everyone. An item that is already gone counts as removed:
 * `onRemoved(false)` says so, so the page can tell the person and refresh.
 */
export function RemoveDialog({
  tenantId,
  item,
  onClose,
  onRemoved,
}: {
  tenantId: string | null;
  item: TrainingItem;
  onClose: () => void;
  /** `true` when this removed it, `false` when it was no longer in the library. */
  onRemoved: (removedNow: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const running = useRef(false);

  async function remove() {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setProblem("");
    try {
      await removeItem(tenantId, item.id);
      onRemoved(true);
    } catch (cause) {
      if (codeOf(cause) === "training_not_found") onRemoved(false);
      else setProblem(refusalText(cause));
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={() => !busy && onClose()} title="Remove this PDF?" testId="training-remove-dialog">
      <div className="space-y-4" aria-busy={busy}>
        {problem && <Notice error testId="training-remove-error">{problem}</Notice>}
        <p className="font-medium [overflow-wrap:anywhere]">{item.title}</p>
        <p className="text-sm">This removes the PDF for everyone in your company.</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={action} disabled={busy} data-testid="training-remove-confirm"
            onClick={() => void remove()}>
            Remove PDF
          </button>
          <button type="button" className={secondary} disabled={busy} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </Dialog>
  );
}
