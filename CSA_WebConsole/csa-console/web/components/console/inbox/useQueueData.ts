"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import {
  assigneesPath, settingsBody, settingsPath, viewsPath,
  type InboxAssignee, type InboxSettings, type InboxSettingsValues, type SavedView,
} from "@/lib/inbox-api";
import type { ConversationPage } from "@/lib/inbox-api";
import {
  anyConversationQuery, assigneesFailure, settingsErrorText, viewErrorText, type ViewFilters,
} from "@/lib/inbox-queue";

const READ_TIMEOUT_MS = 15_000;
/** How long a "none" answer rests before the probe asks again. */
const PROBE_REST_MS = 30_000;

/** The status and plain-text detail of a failed call; null when no answer came. */
function describe(cause: unknown): { status: number | null; code: string | null } {
  return cause instanceof ApiError ? { status: cause.status, code: cause.code } : { status: null, code: null };
}

// ---- any conversation at all ----

/**
 * Whether the company has any conversation: one read of the All list for one row, asked while `ask`
 * (a tab came back empty and the counts could not tell) and looked at again each time `tick`
 * changes (each list answer) until one is found; then never again for this company. One read at a
 * time: a list answer never cancels or repeats a read that is out (only leaving the company does),
 * a failed read is asked again at the next list answer, and a "none" rests for PROBE_REST_MS before
 * the next. Null while unknown: not asked yet, the first read still out, or the last read failed,
 * so the caller never shows "Connect WhatsApp" on a guess. A later "none" keeps the last answer
 * while its re-read is out.
 */
export function useHasConversations(query: string, ask: boolean, tick: string | null): boolean | null {
  const [state, setState] = useState<{ query: string; value: boolean | null }>({ query, value: null });
  const current = state.query === query ? state.value : null;
  const found = current === true;
  // The read that is out, and when the last "none" came back; both change only outside render.
  const out = useRef<AbortController | null>(null);
  const noneAt = useRef<{ query: string; at: number } | null>(null);
  useEffect(() => () => {
    out.current?.abort();
    out.current = null;
  }, [query]);
  useEffect(() => {
    if (!ask || found || out.current !== null) return;
    if (noneAt.current?.query === query && Date.now() - noneAt.current.at < PROBE_REST_MS) return;
    const controller = new AbortController();
    out.current = controller;
    async function read() {
      try {
        const page = await api<ConversationPage>(anyConversationQuery(query), {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(READ_TIMEOUT_MS)]),
        });
        if (controller.signal.aborted) return;
        const any = page.items.length > 0;
        noneAt.current = any ? null : { query, at: Date.now() };
        setState({ query, value: any });
      } catch {
        if (!controller.signal.aborted) setState({ query, value: null });
      } finally {
        if (out.current === controller) out.current = null;
      }
    }
    void read();
  }, [query, ask, found, tick]);
  return current;
}

// ---- assignees ----

type AssigneesState = {
  path: string; people: InboxAssignee[] | null; unavailable: boolean; failed: boolean; loaded: boolean;
};

/**
 * The people an item can be assigned to, read once when `enabled` first turns true (never before).
 * A 503 means the member directory is down: `unavailable`, and never an empty list. Any other failure
 * (network, timeout, 5xx, 403) sets `failed` with `people` null and `loaded` false, so a caller can tell
 * it from "still loading". `retry` clears both flags and asks again.
 */
export function useAssignees(query: string, enabled: boolean) {
  const path = assigneesPath(query);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<AssigneesState>({
    path, people: null, unavailable: false, failed: false, loaded: false,
  });
  const current: AssigneesState = state.path === path
    ? state : { path, people: null, unavailable: false, failed: false, loaded: false };
  const done = current.loaded;
  useEffect(() => {
    if (!enabled || done) return;
    const controller = new AbortController();
    async function load() {
      try {
        const people = await api<InboxAssignee[]>(path, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(READ_TIMEOUT_MS)]),
        });
        if (!controller.signal.aborted) setState({ path, people, unavailable: false, failed: false, loaded: true });
      } catch (cause) {
        if (controller.signal.aborted) return;
        setState({ path, people: null, ...assigneesFailure(describe(cause).status), loaded: false });
      }
    }
    void load();
    return () => controller.abort();
  }, [enabled, done, path, attempt]);
  return {
    people: current.people, unavailable: current.unavailable, failed: current.failed, loaded: current.loaded,
    retry: () => {
      setState({ path, people: null, unavailable: false, failed: false, loaded: false });
      setAttempt((value) => value + 1);
    },
  };
}

// ---- saved views ----

/** The sentence for a failed views read: a refusal says so, anything else asks to try again. */
export const VIEWS_READ_FAILED = "Could not load your saved views. Try again.";
const VIEW_SAVE_FAILED = "Could not save the view. Try again.";
const VIEW_DELETE_FAILED = "Could not delete the view. Try again.";

type ViewsState = { path: string; views: SavedView[] | null; failed: boolean; error: string };
/** A change's outcome; `gone` when the view was not there any more (404) and has left the list. */
export type ViewChange = { ok: boolean; error?: string; view?: SavedView; gone?: boolean };

/**
 * The person's saved views, read when `enabled` first turns true (never before). A failed read sets
 * `failed` and `error` (one plain sentence, never the server's words) and keeps the last list; `retry`
 * clears both and reads again. Each change calls its route, puts its answer in the list at once,
 * re-reads the list whatever the outcome and says what went wrong in one plain sentence (its own
 * for a failed delete). A view the server no longer has leaves the list at once.
 */
export function useSavedViews(query: string, enabled = true) {
  const path = viewsPath(query);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<ViewsState>({ path, views: null, failed: false, error: "" });
  const current: ViewsState = state.path === path ? state : { path, views: null, failed: false, error: "" };
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    async function load() {
      try {
        const views = await api<SavedView[]>(path, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(READ_TIMEOUT_MS)]),
        });
        if (!controller.signal.aborted) setState({ path, views, failed: false, error: "" });
      } catch (cause) {
        if (controller.signal.aborted) return;
        const { status } = describe(cause);
        const error = status === 403 ? viewErrorText(403, null, VIEWS_READ_FAILED) : VIEWS_READ_FAILED;
        setState((previous) => ({
          path, views: previous.path === path ? previous.views : null, failed: true, error,
        }));
      }
    }
    void load();
    return () => controller.abort();
  }, [enabled, path, revision]);

  const edit = (update: (views: SavedView[]) => SavedView[]) => setState((previous) =>
    previous.path === path && previous.views ? { ...previous, views: update(previous.views) } : previous);

  async function change(
    call: () => Promise<SavedView | undefined>, update: (views: SavedView[], view?: SavedView) => SavedView[],
    fallback: string, id?: string,
  ): Promise<ViewChange> {
    try {
      const view = await call();
      edit((views) => update(views, view));
      setRevision((value) => value + 1);
      return { ok: true, view };
    } catch (cause) {
      const { status, code } = describe(cause);
      const gone = status === 404 && id !== undefined;
      if (gone) edit((views) => views.filter((item) => item.id !== id));
      setRevision((value) => value + 1);
      return { ok: false, gone, error: viewErrorText(status, code, fallback) };
    }
  }
  const write = (target: string, method: "POST" | "PUT" | "DELETE", body?: unknown) =>
    api<SavedView | undefined>(target, {
      method, body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(READ_TIMEOUT_MS),
    });
  const swap = (views: SavedView[], view?: SavedView) =>
    view ? views.map((item) => (item.id === view.id ? view : item)) : views;

  return {
    views: current.views, loaded: current.views !== null, failed: current.failed, error: current.error,
    retry: () => {
      setState({ path, views: current.views, failed: false, error: "" });
      setRevision((value) => value + 1);
    },
    create: (name: string, filters: ViewFilters) => change(
      () => write(path, "POST", { name, filters }), (views, view) => (view ? [view, ...views] : views),
      VIEW_SAVE_FAILED),
    rename: (id: string, name: string) =>
      change(() => write(viewsPath(query, id), "PUT", { name }), swap, VIEW_SAVE_FAILED, id),
    replace: (id: string, filters: ViewFilters) =>
      change(() => write(viewsPath(query, id), "PUT", { filters }), swap, VIEW_SAVE_FAILED, id),
    remove: (id: string) => change(
      () => write(viewsPath(query, id), "DELETE"), (views) => views.filter((item) => item.id !== id),
      VIEW_DELETE_FAILED, id),
  };
}

// ---- settings ----

/** The fixed sentences of the settings read: loading, and a failure that is not a refusal. */
export const SETTINGS_LOADING = "Loading the response times…";
export const SETTINGS_READ_FAILED = "Could not load the response times. Try again.";
const SETTINGS_SAVE_FAILED = "Could not save the response times. Try again.";

type SettingsState = { path: string; settings: InboxSettings | null; failed: boolean; error: string };
/** A save's outcome: `conflict` when someone else saved first and the latest values were read anew. */
export type SettingsSave = { ok: boolean; conflict?: boolean; error?: string };
/** The sentence of the last failed save, for the company it was made for. */
type SaveFailure = { path: string; error: string; conflict: boolean };

const readFailure = (cause: unknown) =>
  describe(cause).status === 403 ? viewErrorText(403, null, SETTINGS_READ_FAILED) : SETTINGS_READ_FAILED;

/**
 * The company's Inbox settings, read when `enabled` first turns true (never before), and for each
 * company the query names. A failed read sets `failed` and `error` (one plain sentence) and keeps
 * what was read before; `retry` reads again. `save` sends every field with the version that was read,
 * one save at a time per company (`saving`); a version conflict reads the settings anew, so the
 * form shows the latest values. A failed save's sentence is kept here (`failure`), not in the
 * form, so a save that fails after the panel closed is still said when it opens again; `forget`
 * drops it once it has been seen (a new save or an edit drops it too). Stale answers never win: every read or save answer takes a ticket,
 * and a read that started before a newer answer arrived is dropped, as is any answer for a company
 * the screen has left.
 */
export function useInboxSettings(query: string, enabled: boolean) {
  const path = settingsPath(query);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<SettingsState>({ path, settings: null, failed: false, error: "" });
  const [saving, setSaving] = useState<string | null>(null);
  const [failure, setFailure] = useState<SaveFailure | null>(null);
  const current: SettingsState = state.path === path ? state : { path, settings: null, failed: false, error: "" };
  // The company on screen and the newest ticket; both change only outside render.
  const live = useRef(path);
  const ticket = useRef(0);
  // The company whose save is out, set before the first await so a second press in the same
  // render cannot send another.
  const inFlight = useRef<string | null>(null);
  useEffect(() => { live.current = path; }, [path]);

  const read = (target: string, signal: AbortSignal) => api<InboxSettings>(target, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(READ_TIMEOUT_MS)]),
  });

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const mine = ++ticket.current;
    const fresh = () => !controller.signal.aborted && ticket.current === mine;
    async function load() {
      try {
        const settings = await read(path, controller.signal);
        if (fresh()) setState({ path, settings, failed: false, error: "" });
      } catch (cause) {
        if (!fresh()) return;
        setState((previous) => ({
          path, settings: previous.path === path ? previous.settings : null, failed: true, error: readFailure(cause),
        }));
      }
    }
    void load();
    return () => controller.abort();
  }, [enabled, path, revision]);

  async function save(values: InboxSettingsValues): Promise<SettingsSave> {
    const target = path;
    const before = current.settings;
    if (before === null || inFlight.current === target) return { ok: false };
    inFlight.current = target;
    setSaving(target);
    setFailure(null);
    const fail = (error: string, conflict: boolean) => setFailure({ path: target, error, conflict });
    try {
      const saved = await api<InboxSettings>(target, {
        method: "PUT", body: JSON.stringify(settingsBody(values, before.version)),
        signal: AbortSignal.timeout(READ_TIMEOUT_MS),
      });
      // The answer is the stored record with its new version, so the next save is made on it.
      if (live.current === target) {
        ticket.current += 1;
        setState({ path: target, settings: saved, failed: false, error: "" });
      }
      return { ok: true };
    } catch (cause) {
      const { status, code } = describe(cause);
      if (status !== 409 || code !== "version_conflict") {
        const error = settingsErrorText(status, code, SETTINGS_SAVE_FAILED);
        fail(error, false);
        return { ok: false, error };
      }
      // Someone else saved first: read what is stored now before the person tries again.
      const mine = ++ticket.current;
      const fresh = () => ticket.current === mine && live.current === target;
      try {
        const latest = await read(target, new AbortController().signal);
        if (fresh()) setState({ path: target, settings: latest, failed: false, error: "" });
        const error = settingsErrorText(status, code, SETTINGS_SAVE_FAILED);
        fail(error, true);
        return { ok: false, conflict: true, error };
      } catch (failure) {
        // Never leave the older values editable: the panel shows the failed read and Try again.
        if (fresh()) setState({ path: target, settings: null, failed: true, error: readFailure(failure) });
        return { ok: false, conflict: true };
      }
    } finally {
      if (inFlight.current === target) inFlight.current = null;
      setSaving((value) => (value === target ? null : value));
    }
  }

  return {
    path, settings: current.settings, loaded: current.settings !== null, failed: current.failed,
    error: current.error, saving: saving === path, save,
    failure: failure?.path === path ? failure : null,
    forget: () => setFailure(null),
    retry: () => {
      setState({ path, settings: current.settings, failed: false, error: "" });
      setRevision((value) => value + 1);
    },
  };
}
export type InboxSettingsData = ReturnType<typeof useInboxSettings>;
