"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode, type RefObject } from "react";
import { Bookmark } from "lucide-react";
import Dialog from "@/components/console/fs/Dialog";
import { action, field, Notice, secondary } from "@/components/console/ui";
import type { SavedView } from "@/lib/inbox-api";
import {
  filtersToView, viewChoice, viewKey, type QueueFilters, type QueueOrder, type ViewChoice,
} from "@/lib/inbox-queue";
import { Menu, type Entry } from "./Menu";
import { useSavedViews } from "./useQueueData";

export const VIEWS_LOADING = "Loading your saved views…";
export const VIEWS_EMPTY =
  "You have no saved views yet. Set up the filters you want, then choose Save this view.";
export const VIEW_UNSUPPORTED = "This view uses a filter this screen does not have yet.";
const NAME_MAX = 60;
const NAME_REQUIRED = "Enter a name for the view.";
const MENU_WIDTH = 288; // 18rem
const ring = "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";
/** Red is kept for the one destructive step: the confirmed delete. */
const destructive = `inline-flex min-h-10 items-center justify-center rounded-[var(--fs-radius-control)] border border-[color:var(--fs-danger)] bg-[var(--fs-danger-bg)] px-4 py-2 text-sm font-medium text-[color:var(--fs-danger)] hover:bg-[var(--fs-surface)] pointer-coarse:min-h-11 ${ring}`;
const errorText = "text-sm font-medium text-[color:var(--fs-danger)] [overflow-wrap:anywhere]";

/** The view the screen was set to (chosen or just saved), and the filters it set, as `viewKey`. */
export type ActiveView = { id: string; name: string; key: string };
export type ChosenView = Extract<ViewChoice, { supported: true }>;
type Views = ReturnType<typeof useSavedViews>;

const quoted = (name: string) => `“${name}”`;

/**
 * The queue bar's Views menu (design 14.5): the person's saved views, Save this view and Manage
 * views. The list is read the first time the menu opens; until it has loaded the menu holds one
 * loading line (never "no views"), and a failed read says so with Try again. A view the screen cannot
 * show is listed disabled, with why. The button names the active view while the screen still shows
 * exactly its filters and order. Choosing a view is the parent's (`onApply`); a save says what it
 * did through `onSaved`. Every dialog gives focus back to the button.
 */
export function ViewsMenu({ query, filters, order, active, onApply, onSaved }: {
  query: string; filters: QueueFilters; order: QueueOrder; active: ActiveView | null;
  onApply: (view: SavedView, choice: ChosenView) => void;
  /** A view was saved from the current screen; `key` is the screen's own `viewKey`. */
  onSaved: (view: SavedView | undefined, name: string, key: string) => void;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [asked, setAsked] = useState(false);
  const views = useSavedViews(query, asked);
  const [dialog, setDialog] = useState<"save" | "manage" | null>(null);
  const screenKey = viewKey(filters, order);
  // A rename shows at once; a deleted view, or a screen changed since, shows plain "Views".
  const listed = active ? views.views?.find((view) => view.id === active.id) : undefined;
  const activeName = active && active.key === screenKey && (listed || views.views === null)
    ? (listed?.name ?? active.name) : null;

  const nothing = () => {};
  let entries: Entry[];
  if (views.views === null) {
    entries = views.failed
      ? [
        { key: "failed", label: views.error, disabled: true, onSelect: nothing },
        { key: "retry", label: "Try again", keepOpen: true, onSelect: views.retry },
      ]
      : [{ key: "loading", label: VIEWS_LOADING, disabled: true, onSelect: nothing }];
  } else {
    entries = views.views.map((view): Entry => {
      const choice = viewChoice(view);
      return choice.supported
        ? { key: view.id, label: view.name, checked: activeName !== null && active?.id === view.id,
          onSelect: () => onApply(view, choice) }
        : { key: view.id, label: view.name, checked: false, disabled: true, note: VIEW_UNSUPPORTED, onSelect: nothing };
    });
    if (entries.length === 0) entries.push({ key: "empty", label: VIEWS_EMPTY, disabled: true, onSelect: nothing });
    entries.push({ key: "save", label: "Save this view…", separated: true, onSelect: () => setDialog("save") });
    if (views.views.length > 0) {
      entries.push({ key: "manage", label: "Manage views…", onSelect: () => setDialog("manage") });
    }
  }

  async function save(name: string): Promise<string | null> {
    const result = await views.create(name, filtersToView(filters, order));
    if (!result.ok) return result.error ?? "Could not save the view. Try again.";
    // Only its own dialog closes: the person may have left it (Escape) and opened another meanwhile.
    setDialog((open) => (open === "save" ? null : open));
    onSaved(result.view, name, screenKey);
    return null;
  }

  return <>
    <Menu testId="inbox-views-menu" menuLabel="Saved views" disabled={false} entries={entries}
      triggerRef={triggerRef} width={MENU_WIDTH} onOpen={() => setAsked(true)} buttonClass="min-w-0 max-w-full"
      buttonName={activeName ? `Views, showing ${activeName}` : undefined}
      label={<>
        <Bookmark aria-hidden size={16} className="shrink-0" />
        <span className="min-w-0 truncate md:max-w-56">{activeName ?? "Views"}</span>
      </>} />
    <Dialog open={dialog === "save"} onClose={() => setDialog(null)} title="Save this view"
      testId="inbox-save-view-dialog" returnFocusTo={triggerRef} widthClass="max-w-md">
      <SaveViewBody onCancel={() => setDialog(null)} onSave={save} />
    </Dialog>
    <Dialog open={dialog === "manage"} onClose={() => setDialog(null)} title="Manage views" testId="inbox-manage-views-dialog" returnFocusTo={triggerRef} widthClass="max-w-lg">
      <ManageViewsBody views={views} />
    </Dialog>
  </>;
}

/** Focus a field once the native dialog has finished its own focus move (after showModal). */
function useFocusAfterOpen(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const frame = requestAnimationFrame(() => ref.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [ref]);
}

function SaveViewBody({ onCancel, onSave }: { onCancel: () => void; onSave: (name: string) => Promise<string | null> }) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useFocusAfterOpen(input);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setError(NAME_REQUIRED);
      input.current?.focus();
      return;
    }
    setBusy(true);
    setError("");
    const failure = await onSave(trimmed);
    if (failure !== null) {
      setError(failure);
      setBusy(false);
    }
  }

  return <form noValidate className="space-y-4" onSubmit={submit}>
    <div>
      <label htmlFor={`${id}-name`} className="block text-sm font-medium">Name</label>
      <p id={`${id}-hint`} className="mt-0.5 text-xs text-ink-600">
        Up to {NAME_MAX} characters. Saves the filters and the sort you see now, not the search.</p>
      <input ref={input} id={`${id}-name`} className={`${field} mt-1`} value={name} maxLength={NAME_MAX}
        autoComplete="off" aria-required="true" aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-hint ${id}-error` : `${id}-hint`}
        onChange={(event) => { setName(event.target.value); setError(""); }} />
      {error && <p id={`${id}-error`} role="alert" className={`mt-1 ${errorText}`}>{error}</p>}
    </div>
    <div className="flex flex-wrap justify-end gap-2">
      <button type="button" className={secondary} onClick={onCancel}>Cancel</button>
      <button type="submit" className={action} aria-disabled={busy || undefined}>
        {busy ? "Saving…" : "Save view"}</button>
    </div>
  </form>;
}

/**
 * Rename (an inline field: Enter saves, Escape cancels) and delete (a second "Delete …? Yes, delete /
 * Keep it" step) for each view. Each result is said in a notice at the top of the dialog (the page's
 * own notice is behind the modal dialog), an error next to its view; focus stays on the view's row.
 * A view that is not there any more (deleted elsewhere) leaves the list: that is said in the notice
 * and focus goes to the next view, or to the empty line when none is left.
 * One change at a time: while one is pending, only its own button says so ("Saving…", "Deleting…")
 * and every control in the list is `aria-disabled` and does nothing, so no other row can open,
 * close or take over an editor or a confirm step before the answer lands on its own row.
 */
function ManageViewsBody({ views }: { views: Views }) {
  const list = views.views ?? [];
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [rowError, setRowError] = useState<{ id: string; text: string } | null>(null);
  const [pending, setPending] = useState<{ id: string; kind: "rename" | "delete" } | null>(null);
  const locked = pending !== null;
  const inert = locked || undefined;
  const root = useRef<HTMLDivElement>(null);
  const focusNext = useRef<string | null>(null);
  const firstButton = useRef<HTMLElement | null>(null);
  useFocusAfterOpen(firstButton);

  // Focus moves after the row it names has rendered.
  useEffect(() => {
    if (focusNext.current === null) return;
    const target = root.current?.querySelector<HTMLElement>(`[data-view-focus="${focusNext.current}"]`);
    focusNext.current = null;
    target?.focus();
  });
  function stopEditing() {
    if (locked) return;
    if (editing) focusNext.current = `rename-${editing}`;
    setEditing(null);
    setRowError(null);
  }
  function startEditing(view: SavedView) {
    if (locked) return;
    setConfirming(null);
    setRowError(null);
    setEditing(view.id);
    setDraft(view.name);
    focusNext.current = "edit";
  }
  /** Where focus goes when `view` leaves the list: the next view, else the one before, else the empty line. */
  function after(view: SavedView): string {
    const index = list.findIndex((item) => item.id === view.id);
    const neighbour = list[index + 1] ?? list[index - 1];
    return neighbour ? `rename-${neighbour.id}` : "empty";
  }
  /** The view went elsewhere and has left the list: said at the top, focus on its neighbour. */
  function gone(text: string, focus: string) {
    setStatus(text);
    setEditing(null);
    setRowError(null);
    focusNext.current = focus;
  }
  async function rename(view: SavedView) {
    if (locked) return;
    const name = draft.trim();
    if (!name) { setRowError({ id: view.id, text: NAME_REQUIRED }); return; }
    if (name === view.name) { stopEditing(); return; }
    const focus = after(view);
    setPending({ id: view.id, kind: "rename" });
    const result = await views.rename(view.id, name);
    setPending(null);
    if (result.gone) { gone(result.error ?? "", focus); return; }
    if (!result.ok) {
      setRowError({ id: view.id, text: result.error ?? "Could not save the view. Try again." });
      focusNext.current = "edit";
      return;
    }
    setStatus(`Renamed to ${quoted(name)}.`);
    setEditing(null);
    setRowError(null);
    focusNext.current = `rename-${view.id}`;
  }
  async function remove(view: SavedView) {
    if (locked) return;
    const focus = after(view);
    setPending({ id: view.id, kind: "delete" });
    const result = await views.remove(view.id);
    setPending(null);
    setConfirming(null);
    if (result.gone) { gone(result.error ?? "", focus); return; }
    if (!result.ok) {
      setRowError({ id: view.id, text: result.error ?? "Could not delete the view. Try again." });
      focusNext.current = `delete-${view.id}`;
      return;
    }
    setStatus(`Deleted the view ${quoted(view.name)}.`);
    setRowError(null);
    focusNext.current = focus;
  }

  return <div ref={root} className="space-y-3">
    {status && <Notice>{status}</Notice>}
    {list.length === 0
      ? <p tabIndex={-1} data-view-focus="empty" className="text-sm">{VIEWS_EMPTY}</p>
      : <ul className="divide-y divide-[color:var(--fs-border)]">
        {list.map((view, index) => {
          const unsupported = !viewChoice(view).supported;
          const error = rowError?.id === view.id ? rowError.text : "";
          const errorId = `view-error-${view.id}`;
          const saving = pending?.id === view.id && pending.kind === "rename";
          const deleting = pending?.id === view.id && pending.kind === "delete";
          let body: ReactNode;
          if (editing === view.id) {
            body = <form noValidate className="flex flex-wrap items-center gap-2"
              onSubmit={(event) => { event.preventDefault(); void rename(view); }}>
              <input data-view-focus="edit"
                className={`${field} min-w-0 flex-[1_1_12rem]`} value={draft} maxLength={NAME_MAX} autoComplete="off"
                readOnly={saving}
                aria-label={`New name for ${view.name}`} aria-invalid={error ? true : undefined}
                aria-describedby={error ? errorId : undefined}
                onChange={(event) => { setDraft(event.target.value); setRowError(null); }}
                onKeyDown={(event) => {
                  // Handled here, so the native dialog does not take it as its own Escape.
                  if (event.key !== "Escape") return;
                  event.preventDefault();
                  event.stopPropagation();
                  stopEditing();
                }} />
              <button type="submit" className={action} aria-disabled={inert}>{saving ? "Saving…" : "Save name"}</button>
              <button type="button" className={secondary} aria-disabled={inert} onClick={stopEditing}>Cancel</button>
            </form>;
          } else if (confirming === view.id) {
            body = <div className="space-y-2">
              <p className="text-sm font-medium [overflow-wrap:anywhere]">{`Delete ${quoted(view.name)}?`}</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={destructive} aria-disabled={inert} onClick={() => void remove(view)}>
                  {deleting ? "Deleting…" : "Yes, delete"}</button>
                <button type="button" className={secondary} data-view-focus="keep" aria-disabled={inert} onClick={() => {
                  if (locked) return;
                  setConfirming(null);
                  focusNext.current = `delete-${view.id}`;
                }}>Keep it</button>
              </div>
            </div>;
          } else {
            body = <div className="flex flex-wrap items-center gap-2">
              <div className="min-w-0 flex-[1_1_10rem]">
                <p className="text-sm font-medium [overflow-wrap:anywhere]">{view.name}</p>
                {unsupported && <p className="text-xs text-[color:var(--fs-text-secondary)]">{VIEW_UNSUPPORTED}</p>}
              </div>
              <button type="button" className={secondary} data-view-focus={`rename-${view.id}`}
                ref={index === 0 ? (element) => { firstButton.current = element; } : undefined}
                aria-label={`Rename ${view.name}`} aria-disabled={inert} onClick={() => startEditing(view)}>Rename</button>
              <button type="button" className={secondary} data-view-focus={`delete-${view.id}`}
                aria-label={`Delete ${view.name}`} aria-disabled={inert} onClick={() => {
                  if (locked) return;
                  setEditing(null);
                  setRowError(null);
                  setConfirming(view.id);
                  focusNext.current = "keep";
                }}>Delete</button>
            </div>;
          }
          return <li key={view.id} className="min-w-0 space-y-1 py-2">
            {body}
            {error && <p id={errorId} role="alert" className={errorText}>{error}</p>}
          </li>;
        })}
      </ul>}
  </div>;
}
