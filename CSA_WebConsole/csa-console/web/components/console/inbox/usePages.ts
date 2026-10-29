"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { mergeById, type Page } from "@/lib/inbox-api";

type Extra<P> = Omit<P, "items" | "next_cursor">;

type State<T, P> = {
  /** The path these values belong to; values for any other path are ignored. */
  path: string;
  items: T[];
  next_cursor: string | number | null;
  extra: Extra<P> | null;
  loaded: boolean;
  error: string;
  status: number | null;
  /** The server's raw `detail` code of the last failure (ApiError.code), or null. */
  code: string | null;
};

function empty<T, P>(path: string): State<T, P> {
  return { path, items: [], next_cursor: null, extra: null, loaded: false, error: "", status: null, code: null };
}

/** The server answers 403 for this when the tenant's service package is not active. */
const PACKAGE_INACTIVE = "package_inactive";

/**
 * Polls a cursor-paged endpoint every 3 seconds and loads `pages` pages per refresh.
 * A failed refresh keeps the previous items and sets `error`; `loaded` turns true after
 * the first success for this path, so callers can tell "empty" from "never loaded".
 * `extra` carries the first page's other fields (for example `needs_review_count`).
 * `status` and `code` describe the last failure, or are null.
 */
export function usePages<T extends { id: string | number }, P extends Page<T> = Page<T>>(
  path: string, pages: number, revision: number,
) {
  const [state, setState] = useState<State<T, P>>(() => empty<T, P>(path));
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const collected: T[] = [];
        let extra: Extra<P> | null = null;
        let cursor: string | number | null = null;
        for (let index = 0; index < pages; index++) {
          const separator: string = path.includes("?") ? "&" : "?";
          const page: P = await api(
            `${path}${cursor !== null ? `${separator}before=${encodeURIComponent(String(cursor))}` : ""}`,
            { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]) },
          );
          if (index === 0) {
            const { items: _items, next_cursor: _next, ...rest } = page;
            void _items; void _next;
            extra = rest as Extra<P>;
          }
          collected.push(...page.items); cursor = page.next_cursor;
          if (cursor === null) break;
        }
        if (!controller.signal.aborted) {
          // Pages can repeat a row when new activity reorders the list between loads.
          setState({
            path, items: mergeById(collected), next_cursor: cursor, extra, loaded: true, error: "", status: null,
            code: null,
          });
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          const status = cause instanceof ApiError ? cause.status : null;
          const code = cause instanceof ApiError ? cause.code : null;
          setState((previous) => ({
            ...(previous.path === path ? previous : empty<T, P>(path)),
            error: (cause as Error).message, status, code,
          }));
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 3000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, pages, revision]);
  const current = state.path === path ? state : empty<T, P>(path);
  return {
    items: current.items, next_cursor: current.next_cursor, extra: current.extra,
    loaded: current.loaded, error: current.error, status: current.status, code: current.code,
    /** A 403 for the reader's role: retrying will not help. */
    forbidden: current.status === 403 && current.code !== PACKAGE_INACTIVE,
    /** A 403 because the service package is inactive; `error` carries the plain-words reason. */
    packageInactive: current.status === 403 && current.code === PACKAGE_INACTIVE,
  };
}
