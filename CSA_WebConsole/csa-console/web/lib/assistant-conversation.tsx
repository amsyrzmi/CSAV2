"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  createContext,
  memo,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { panelText, type Language } from "@/lib/assistant-copy";
import {
  dockTenant,
  effectiveContext,
  latestLanguage,
  mergeTurns,
  openProposals,
  resumeContext,
  tabForPath,
  tabHref,
  turnErrorText,
  type Outgoing,
} from "@/lib/assistant-view";
import { isHrPagePath, navigateLandingPath } from "@/lib/hr-paths";
import type { Me } from "@/lib/identity-contracts";
import { usePageContext } from "@/lib/page-context";
import type { PackageState } from "@/lib/services-api";
import {
  cancelProposal,
  createWorkspaceThread,
  createWorkspaceTurn,
  listWorkspaceThreads,
  listWorkspaceTurns,
  turnPageContext,
  type PageContext,
  type ProposalOut,
  type TabKey,
  type WorkspaceResult,
  type WorkspaceTurn,
} from "@/lib/workspace-api";

export type { Outgoing } from "@/lib/assistant-view";

/**
 * The assistant conversation for the company in view: one per console page load, shared by the
 * dock and any other view of it, so moving between pages keeps the thread, the turns and the
 * unsent text. Views own only how it looks (open or closed, focus, scroll).
 */
export interface AssistantConversation {
  tenant: string;
  me: Me;
  packages: PackageState[];
  loaded: boolean;
  /** First load of the thread and its turns; calling it again does nothing. */
  ensureLoaded(): void;
  turns: WorkspaceTurn[];
  outcomes: Record<string, ProposalOut>;
  /** What this console sent, by request key: stored bodies come back with addresses redacted. */
  origins: Record<string, string>;
  body: string;
  setBody(value: string): void;
  busy: boolean;
  error: string;
  /** A failed send can be repeated as it was ("retry_same") or dropped ("start_new"). */
  retry: boolean;
  /** The tab a navigate result is opening before it re-sends the request there. */
  resumeTab: TabKey | null;
  context: PageContext | null;
  /** Panel-wide lines follow the newest turn. */
  language: Language;
  ask(outgoing: Outgoing, fromInput?: boolean): void;
  retrySame(): void;
  dismissRetry(): void;
  settle(proposal: ProposalOut): void;
  newConversation(): void;
}

const ConversationContext = createContext<AssistantConversation | null>(null);

/**
 * The conversation for the company in view, or null. Null is NOT a stable "assistant off"
 * signal: it also means "not ready yet" during the server render and the first client render
 * of every page load, and for one pre-paint render on every company switch. Read it as "not
 * ready or not offered": keep any loading branch ahead of an assistant-or-home choice, and
 * hang no effects off the null branch.
 */
export function useAssistantConversation(): AssistantConversation | null {
  return useContext(ConversationContext);
}

/** How long a resumed turn waits for the new tab to publish its context. */
const RESUME_GRACE_MS = 1000;
/** How long a resume waits for the route to reach its target before giving up. */
const RESUME_TIMEOUT_MS = 10000;

interface Attempt {
  key: string;
  threadKey: string;
  text: string;
  /** The request this turn continues, unredacted; kept in memory for the next follow-up. */
  origin: string;
  context: PageContext | null;
  /** A resumed turn never resumes again, so a navigate loop cannot form. */
  resumed: boolean;
  fromInput: boolean;
}

interface Resume {
  text: string;
  origin: string;
  tab: TabKey;
  section: string | null;
  path: string;
}

type Publish = Dispatch<SetStateAction<AssistantConversation | null>>;

/**
 * Mounted once in the shell around the page and the dock. The conversation itself lives in a
 * keyed sibling of the page, so a change of company discards it (withdrawing its open cards)
 * without remounting the page; it hands each new state up to this provider's context.
 * Until the company is known and that state has arrived, the context holds null (see
 * useAssistantConversation): null means not ready or not offered, never only "off".
 */
export function AssistantProvider({
  me,
  packages,
  children,
}: {
  me: Me;
  packages: PackageState[];
  children: ReactNode;
}) {
  const [tenant, setTenant] = useState<string | null>(null);
  const [conversation, setConversation] = useState<AssistantConversation | null>(null);
  // Null while the company is unknown (server render, first client render), while the new
  // company's state has not been published yet (one pre-paint render on a switch), or when
  // the assistant is not offered.
  const value = tenant !== null && conversation?.tenant === tenant ? conversation : null;
  return (
    <>
      {/* The company comes from ?client_id=, so it is read client-side under Suspense. */}
      <Suspense fallback={null}>
        <TenantBridge me={me} onTenant={setTenant} />
      </Suspense>
      {tenant && (
        <Conversation key={tenant} tenant={tenant} me={me} packages={packages} publish={setConversation} />
      )}
      <ConversationContext.Provider value={value}>{children}</ConversationContext.Provider>
    </>
  );
}

function TenantBridge({ me, onTenant }: { me: Me; onTenant: (tenant: string | null) => void }) {
  const tenant = dockTenant(me, useSearchParams().get("client_id"));
  // A layout effect, so a changed company's conversation is swapped before the next paint.
  useLayoutEffect(() => {
    onTenant(tenant);
  }, [tenant, onTenant]);
  return null;
}

// Memoised: the provider re-renders on every published state and must not re-render this.
// The no-deps publish effect below only avoids an infinite render loop because of this memo
// and because every prop is stable (state setter, shell props): an inline prop added later
// (a new object or function each render) would loop.
const Conversation = memo(function Conversation({
  tenant,
  me,
  packages,
  publish,
}: {
  tenant: string;
  me: Me;
  packages: PackageState[];
  publish: Publish;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const published = usePageContext();
  const pathTab = tabForPath(pathname, me, packages);
  const context = effectiveContext(published, pathTab);
  const [wanted, setWanted] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [threadId, setThreadId] = useState("");
  const [turns, setTurns] = useState<WorkspaceTurn[]>([]);
  const [outcomes, setOutcomes] = useState<Record<string, ProposalOut>>({});
  // What this console sent, by request key: stored bodies come back with addresses redacted.
  const [origins, setOrigins] = useState<Record<string, string>>({});
  const [body, setBody] = useState("");
  const [pending, setPending] = useState<Attempt | null>(null);
  const [resume, setResume] = useState<Resume | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mutation = useRef<AbortController | null>(null);
  const latest = useRef({ turns, outcomes });

  useEffect(() => {
    latest.current = { turns, outcomes };
  }, [turns, outcomes]);

  // Leaving this tenant (or losing the dock) withdraws proposals still awaiting a decision.
  useEffect(
    () => () => {
      mutation.current?.abort();
      const { turns: known, outcomes: settled } = latest.current;
      for (const proposal of openProposals(known, settled))
        cancelProposal(proposal.id, undefined, tenant).catch(() => undefined);
    },
    [tenant],
  );

  const ensureLoaded = useCallback(() => setWanted(true), []);

  useEffect(() => {
    if (!wanted || loaded) return;
    const controller = new AbortController();
    listWorkspaceThreads(tenant, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setThreadId((current) => current || (result.items[0]?.id ?? ""));
        setLoaded(true);
      })
      .catch((cause: Error) => {
        if (controller.signal.aborted) return;
        setError(cause.message);
        setLoaded(true);
      });
    return () => controller.abort();
  }, [wanted, loaded, tenant]);

  useEffect(() => {
    if (!threadId) return;
    const controller = new AbortController();
    listWorkspaceTurns(tenant, threadId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setTurns((current) => mergeTurns(current, result.items));
      })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [tenant, threadId]);

  function settle(proposal: ProposalOut) {
    setOutcomes((current) => ({ ...current, [proposal.id]: proposal }));
  }

  /** Cancel every card still awaiting a decision; a failed cancel leaves it to the server. */
  function withdrawOpen() {
    return Promise.all(
      openProposals(turns, outcomes).map((proposal) =>
        cancelProposal(proposal.id, undefined, tenant).then(settle, () => undefined),
      ),
    );
  }

  // Any tab change (sidebar link, back button, a navigate result) withdraws requested cards:
  // a card must not stay confirmable once the user has left the page it was proposed on.
  const withdrawOnTabChange = useEffectEvent(() => {
    void withdrawOpen();
  });
  const shownTab = useRef(pathTab);
  useEffect(() => {
    if (shownTab.current === pathTab) return;
    shownTab.current = pathTab;
    withdrawOnTabChange();
  }, [pathTab]);

  async function navigate(
    result: Extract<WorkspaceResult, { action: "navigate" }>,
    attempt: Attempt,
  ) {
    const tabPage = tabHref(result.tab, me, packages, tenant, result.section);
    if (!tabPage) {
      // The turn itself shows the notice (navigateText); nothing is printed twice.
      return;
    }
    // A person's profile: the server builds the path; only an HR page path is ever opened.
    const href = result.path && isHrPagePath(result.path.split("?", 1)[0]) ? result.path : tabPage;
    // A requested card must not survive the page it was proposed on; cancel before leaving.
    await withdrawOpen();
    if (result.resume && !attempt.resumed)
      setResume({
        text: attempt.text,
        origin: attempt.origin,
        tab: result.tab,
        section: result.section,
        // The HR pages redirect a legacy `?section=` to its own path; wait for that one.
        path: navigateLandingPath(href),
      });
    router.push(href);
  }

  async function send(attempt: Attempt) {
    if (mutation.current) return;
    const controller = new AbortController();
    mutation.current = controller;
    setPending(attempt);
    setBusy(true);
    setError("");
    try {
      let thread = threadId;
      if (!thread) {
        thread = (await createWorkspaceThread(tenant, attempt.threadKey, controller.signal)).id;
        setThreadId(thread);
      }
      const turn = await createWorkspaceTurn(
        tenant,
        thread,
        attempt.text,
        null,
        attempt.key,
        controller.signal,
        turnPageContext(attempt.context),
      );
      if (controller.signal.aborted) return;
      setOrigins((current) => ({ ...current, [turn.request_key]: attempt.origin }));
      setTurns((current) => mergeTurns(current, [turn]));
      setPending(null);
      if (attempt.fromInput) setBody("");
      if ("action" in turn.result && turn.result.action === "navigate")
        await navigate(turn.result, attempt);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(turnErrorText(cause, latestLanguage(latest.current.turns)));
    } finally {
      if (mutation.current === controller) mutation.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function ask({ text, origin }: Outgoing, fromInput = false) {
    if (!text.trim() || busy || resume) return;
    void send({
      key: crypto.randomUUID(),
      threadKey: crypto.randomUUID(),
      text,
      origin,
      context,
      resumed: false,
      fromInput,
    });
  }

  const fireResume = useEffectEvent((fallback: boolean) => {
    if (!resume) return;
    const ready = resumeContext(published, resume, fallback);
    if (!ready) return;
    setResume(null);
    void send({
      key: crypto.randomUUID(),
      threadKey: crypto.randomUUID(),
      text: resume.text,
      origin: resume.origin,
      context: ready,
      resumed: true,
      fromInput: false,
    });
  });

  // Resume after the route change: send as soon as the new tab publishes its context, or
  // after RESUME_GRACE_MS with the bare target tab when that tab publishes nothing.
  const targetReached = resume !== null && pathname === resume.path;
  const targetPublished = published?.tab === resume?.tab;
  useEffect(() => {
    if (!targetReached || busy) return;
    const timer = setTimeout(() => fireResume(!targetPublished), targetPublished ? 0 : RESUME_GRACE_MS);
    return () => clearTimeout(timer);
  }, [targetReached, targetPublished, busy]);

  // A route that never reaches the resume target must not leave the input locked.
  const waitingForRoute = resume !== null && !targetReached;
  useEffect(() => {
    if (!waitingForRoute) return;
    const timer = setTimeout(() => {
      setResume(null);
      setError(panelText(latestLanguage(latest.current.turns), "resume_timeout"));
    }, RESUME_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [waitingForRoute]);

  function newConversation() {
    void withdrawOpen();
    mutation.current?.abort();
    mutation.current = null;
    setThreadId("");
    setTurns([]);
    setOrigins({});
    setPending(null);
    setResume(null);
    setBusy(false);
    setError("");
  }

  const conversation: AssistantConversation = {
    tenant,
    me,
    packages,
    loaded,
    ensureLoaded,
    turns,
    outcomes,
    origins,
    body,
    setBody,
    busy,
    error,
    retry: pending !== null && error !== "" && !busy,
    resumeTab: resume?.tab ?? null,
    context,
    language: latestLanguage(turns),
    ask,
    retrySame: () => {
      if (pending) void send(pending);
    },
    dismissRetry: () => {
      setPending(null);
      setError("");
    },
    settle,
    newConversation,
  };

  // Every render hands the provider the current state (before paint, so views never lag).
  // No deps on purpose; safe only while Conversation is memoised with stable props (see above),
  // otherwise publish -> provider render -> Conversation render -> publish loops forever.
  useLayoutEffect(() => {
    publish(conversation);
  });
  // A discarded conversation (company changed or the assistant gone) is taken back.
  useLayoutEffect(
    () => () => publish((current) => (current?.tenant === tenant ? null : current)),
    [publish, tenant],
  );
  return null;
});
