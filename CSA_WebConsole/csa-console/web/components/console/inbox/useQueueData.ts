"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import {
  assigneesPath, settingsPath, viewsPath,
  type InboxAssignee, type InboxSettings, type InboxSettingsValues, type SavedView,
} from "@/lib/inbox-api";
import { assigneesFailure, settingsErrorText, viewErrorText, type ViewFilters } from "@/lib/inbox-queue";

const READ_TIMEOUT_MS = 15_000;

/** The status and plain-text detail of a failed call; null when no answer came. */
function describe(cause: unknown): { status: number | null; code: string | null } {
  return cause instanceof ApiError ? { status: cause.status, code: cause.code } : { status: null, code: null };
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

type ViewsState = { path: string; views: SavedView[] | null; failed: boolean; error: string };
export type ViewChange = { ok: boolean; error?: string; view?: SavedView };

/**
 * The person's saved views, read when `enabled` first turns true (never before). A failed read sets
 * `failed` and `error` (one plain sentence, never the server's words) and keeps the last list; `retry`
 * clears both and reads again. Each change calls its route, puts its answer in the list at once,
 * re-reads the list whatever the outcome and says what went wrong in one plain sentence.
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

  async function change(
    call: () => Promise<SavedView | undefined>, update: (views: SavedView[], view?: SavedView) => SavedView[],
  ): Promise<ViewChange> {
    try {
      const view = await call();
      setState((previous) => previous.path === path && previous.views
        ? { ...previous, views: update(previous.views, view) } : previous);
      setRevision((value) => value + 1);
      return { ok: true, view };
    } catch (cause) {
      setRevision((value) => value + 1);
      const { status, code } = describe(cause);
      return { ok: false, error: viewErrorText(status, code, "Could not save the view. Try again.") };
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
      () => write(path, "POST", { name, filters }), (views, view) => (view ? [view, ...views] : views)),
    rename: (id: string, name: string) => change(() => write(viewsPath(query, id), "PUT", { name }), swap),
    replace: (id: string, filters: ViewFilters) =>
      change(() => write(viewsPath(query, id), "PUT", { filters }), swap),
    remove: (id: string) => change(
      () => write(viewsPath(query, id), "DELETE"), (views) => views.filter((item) => item.id !== id)),
  };
}

// ---- settings ----

type SettingsState = { path: string; settings: InboxSettings | null; error: string };
export type SettingsSave = { ok: boolean; error?: string };

/** The nine fields and nothing else: the server rejects an unknown one. */
function settingsBody(values: InboxSettingsValues, version: number): InboxSettings {
  return {
    target_urgent_min: values.target_urgent_min,
    target_high_min: values.target_high_min,
    target_normal_min: values.target_normal_min,
    target_low_min: values.target_low_min,
    repeat_minutes: values.repeat_minutes,
    max_repeats: values.max_repeats,
    follow_up_hours: values.follow_up_hours,
    stale_claim_hours: values.stale_claim_hours,
    handback_prompt_hours: values.handback_prompt_hours,
    version,
  };
}

/**
 * The company's Inbox settings. `save` sends every field with the version that was read; on a
 * version conflict the settings are read again and the person is told to check and save again.
 */
export function useInboxSettings(query: string) {
  const path = settingsPath(query);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<SettingsState>({ path, settings: null, error: "" });
  const current = state.path === path ? state : { path, settings: null, error: "" };
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const settings = await api<InboxSettings>(path, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(READ_TIMEOUT_MS)]),
        });
        if (!controller.signal.aborted) setState({ path, settings, error: "" });
      } catch (cause) {
        if (controller.signal.aborted) return;
        setState((previous) => ({
          path, settings: previous.path === path ? previous.settings : null, error: (cause as Error).message,
        }));
      }
    }
    void load();
    return () => controller.abort();
  }, [path, revision]);

  async function save(values: InboxSettingsValues): Promise<SettingsSave> {
    if (current.settings === null) return { ok: false, error: "The settings are not loaded yet. Try again in a moment." };
    try {
      const saved = await api<InboxSettings>(path, {
        method: "PUT", body: JSON.stringify(settingsBody(values, current.settings.version)),
        signal: AbortSignal.timeout(READ_TIMEOUT_MS),
      });
      // The stored record carries the new version, so a second save does not conflict with the first.
      setState({ path, settings: saved, error: "" });
      setRevision((value) => value + 1);
      return { ok: true };
    } catch (cause) {
      const { status, code } = describe(cause);
      if (status === 409) setRevision((value) => value + 1);
      return { ok: false, error: settingsErrorText(status, code, "Could not save the settings. Try again.") };
    }
  }
  return {
    settings: current.settings, loaded: current.settings !== null, error: current.error, save,
    reload: () => setRevision((value) => value + 1),
  };
}
