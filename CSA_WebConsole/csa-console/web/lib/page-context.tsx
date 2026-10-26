"use client";

import {
  createContext,
  useContext,
  useEffect,
  useId,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import {
  initialPageContextState,
  pageContextReducer,
  type PageContextAction,
} from "@/lib/page-context-state";
import type { PageContext } from "@/lib/workspace-api";

const ValueContext = createContext<PageContext | null>(null);
const DispatchContext = createContext<Dispatch<PageContextAction> | null>(null);

export function PageContextProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    pageContextReducer,
    initialPageContextState,
  );
  return (
    <DispatchContext.Provider value={dispatch}>
      <ValueContext.Provider value={state.context}>
        {children}
      </ValueContext.Provider>
    </DispatchContext.Provider>
  );
}

/** Publish what this tab is showing; cleared when the tab unmounts. */
export function usePublishContext(context: PageContext | null): void {
  const dispatch = useContext(DispatchContext);
  const owner = useId();
  // Key on content so callers may pass a fresh object every render.
  const key = JSON.stringify(context);
  useEffect(() => {
    if (!dispatch) return;
    dispatch({
      type: "publish",
      owner,
      context: JSON.parse(key) as PageContext | null,
    });
  }, [dispatch, owner, key]);
  useEffect(() => {
    if (!dispatch) return;
    return () => dispatch({ type: "clear", owner });
  }, [dispatch, owner]);
}

export function usePageContext(): PageContext | null {
  return useContext(ValueContext);
}
