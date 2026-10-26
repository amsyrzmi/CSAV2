import type { PageContext } from "@/lib/workspace-api";

export interface PageContextState {
  owner: string | null;
  context: PageContext | null;
}

export type PageContextAction =
  | { type: "publish"; owner: string; context: PageContext | null }
  | { type: "clear"; owner: string };

export const initialPageContextState: PageContextState = {
  owner: null,
  context: null,
};

/**
 * Only the tab that published the current context may clear it, so a stale
 * cleanup from the previous tab cannot erase its successor's context.
 */
export function pageContextReducer(
  state: PageContextState,
  action: PageContextAction,
): PageContextState {
  if (action.type === "publish" && action.context !== null)
    return { owner: action.owner, context: action.context };
  return state.owner === action.owner ? initialPageContextState : state;
}
