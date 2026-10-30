"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useEffectEvent, useId, useRef, useState } from "react";
import { MessageSquare, X } from "lucide-react";
import {
  candidateAnswer,
  dockTenant,
  effectiveContext,
  isAnswerable,
  openProposals,
  optionAnswer,
  originFor,
  resultView,
  resumeContext,
  tabForPath,
  tabHref,
  tabLabel,
  typedMessage,
  type Outgoing,
} from "@/lib/assistant-view";
import type { Me } from "@/lib/identity-contracts";
import { usePageContext } from "@/lib/page-context";
import type { PackageState } from "@/lib/services-api";
import {
  cancelProposal,
  createWorkspaceThread,
  createWorkspaceTurn,
  listWorkspaceThreads,
  listWorkspaceTurns,
  type PageContext,
  type ProposalOut,
  type TabKey,
  type WorkspaceResult,
  type WorkspaceTurn,
} from "@/lib/workspace-api";
import { ProposalCard } from "./ProposalCard";
import { action, field, Notice, secondary } from "./ui";

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

export function AssistantDock({ me, packages }: { me: Me; packages: PackageState[] }) {
  const tenant = dockTenant(me, useSearchParams().get("client_id"));
  if (!tenant) return null;
  return <Dock key={tenant} tenant={tenant} me={me} packages={packages} />;
}

function mergeTurns(current: WorkspaceTurn[], added: WorkspaceTurn[]): WorkspaceTurn[] {
  const unique = new Map([...current, ...added].map((turn) => [turn.id, turn]));
  return [...unique.values()].sort((left, right) => left.id - right.id);
}

function Dock({ tenant, me, packages }: { tenant: string; me: Me; packages: PackageState[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const published = usePageContext();
  const pathTab = tabForPath(pathname, me, packages);
  const context = effectiveContext(published, pathTab);
  const panelId = useId();
  const [open, setOpen] = useState(false);
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
  const openButton = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef(false);
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

  useEffect(() => {
    if (!open || loaded) return;
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
  }, [open, loaded, tenant]);

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

  useEffect(() => {
    if (open) input.current?.focus();
    else if (returnFocus.current) openButton.current?.focus();
  }, [open]);

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
    const href = tabHref(result.tab, me, packages, tenant, result.section);
    if (!href) {
      setError(`${tabLabel(result.tab, me, packages)} is not available to you.`);
      return;
    }
    // A requested card must not survive the page it was proposed on; cancel before leaving.
    await withdrawOpen();
    if (result.resume && !attempt.resumed)
      setResume({
        text: attempt.text,
        origin: attempt.origin,
        tab: result.tab,
        section: result.section,
        path: href.split("?")[0],
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
        attempt.context,
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
        setError(cause instanceof Error ? cause.message : "The assistant request failed.");
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
      setError("The tab did not open, so the request was not continued. Send it again.");
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

  function close() {
    returnFocus.current = true;
    setOpen(false);
  }

  if (!open)
    return (
      <button
        ref={openButton}
        type="button"
        data-testid="assistant-open"
        aria-expanded={false}
        aria-controls={panelId}
        onClick={() => setOpen(true)}
        className={`${action} fixed bottom-5 right-5 z-40 rounded-full shadow-lg`}
      >
        <MessageSquare size={18} aria-hidden />
        Assistant
      </button>
    );

  const retry = pending !== null && error !== "" && !busy;
  return (
    <section
      id={panelId}
      data-testid="assistant-panel"
      aria-labelledby={`${panelId}-title`}
      onKeyDown={(event) => {
        if (event.key === "Escape") close();
      }}
      className="fixed bottom-5 right-5 z-40 flex max-h-[80vh] w-[min(28rem,calc(100vw-2.5rem))] flex-col rounded-lg border border-ink-200 bg-white shadow-xl"
    >
      <header className="flex items-center gap-2 border-b border-ink-200 px-4 py-3">
        <h2 id={`${panelId}-title`} className="text-base font-semibold">
          Assistant
        </h2>
        <span
          data-testid="assistant-tab"
          className="rounded-full bg-brand-50 px-2 py-0.5 text-xs text-ink-700"
        >
          {context ? `On ${tabLabel(context.tab, me, packages)}` : "No tab context"}
        </span>
        <button
          type="button"
          className="ml-auto text-sm text-ink-600 underline hover:text-brand-600 disabled:opacity-50"
          disabled={busy}
          onClick={newConversation}
        >
          New conversation
        </button>
        <button
          type="button"
          aria-label="Close assistant"
          onClick={close}
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md hover:bg-ink-50"
        >
          <X size={18} aria-hidden />
        </button>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-3" aria-live="polite">
        {!loaded && <p className="text-sm text-ink-500">Loading your conversation…</p>}
        {loaded && turns.length === 0 && (
          <p className="text-sm text-ink-500">
            Ask about this tab, find a colleague, or prepare an action. Nothing changes until
            you confirm it.
          </p>
        )}
        {turns.map((turn) => (
          <article key={turn.id} data-testid="assistant-turn" className="space-y-2">
            <p className="ml-8 whitespace-pre-wrap rounded-md bg-ink-50 p-3 text-sm">{turn.body}</p>
            <TurnResult
              result={turn.result}
              origin={originFor(turn, origins)}
              me={me}
              packages={packages}
              tenant={tenant}
              outcomes={outcomes}
              disabled={busy || resume !== null}
              answerable={isAnswerable(turns, turn.id)}
              onSettled={settle}
              onAsk={(outgoing) => ask(outgoing)}
            />
          </article>
        ))}
        {resume && <Notice>Opening {tabLabel(resume.tab, me, packages)} and continuing…</Notice>}
        {error && <Notice error>{error}</Notice>}
      </div>
      <form
        className="space-y-2 border-t border-ink-200 px-4 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (body.trim()) ask(typedMessage(body, turns, origins), true);
        }}
      >
        <label className="block text-sm">
          Message
          <textarea
            ref={input}
            data-testid="assistant-input"
            className={`${field} min-h-20 py-2`}
            value={body}
            maxLength={4000}
            disabled={busy || retry}
            onChange={(event) => setBody(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (body.trim()) ask(typedMessage(body, turns, origins), true);
              }
            }}
          />
        </label>
        <div className="flex flex-wrap gap-2">
          {retry ? (
            <>
              <button type="button" className={action} onClick={() => pending && send(pending)}>
                Retry same request
              </button>
              <button
                type="button"
                className={secondary}
                onClick={() => {
                  setPending(null);
                  setError("");
                }}
              >
                Start a new request
              </button>
            </>
          ) : (
            <button
              type="submit"
              data-testid="assistant-send"
              className={action}
              disabled={busy || resume !== null || !body.trim() || !loaded}
            >
              {busy ? "Sending…" : "Send"}
            </button>
          )}
        </div>
      </form>
    </section>
  );
}

function TurnResult({
  result,
  origin,
  me,
  packages,
  tenant,
  outcomes,
  disabled,
  answerable,
  onSettled,
  onAsk,
}: {
  result: WorkspaceResult;
  /** The request that produced this result; follow-ups repeat it for the planner. */
  origin: string;
  me: Me;
  packages: PackageState[];
  tenant: string;
  outcomes: Record<string, ProposalOut>;
  disabled: boolean;
  /** False for every card but the newest: older choices are history, not live questions. */
  answerable: boolean;
  onSettled: (proposal: ProposalOut) => void;
  onAsk: (outgoing: Outgoing) => void;
}) {
  const view = resultView(result);
  const box = "rounded-md bg-brand-50 p-3 text-sm";
  switch (view.kind) {
    case "notice":
      return <Notice error={view.error}>{view.text}</Notice>;
    case "guidance":
      return (
        <div className={box}>
          <p>{view.text}</p>
          <p className="mt-2 text-xs text-ink-500">
            Source: {view.source} · As of {new Date(view.asOf).toLocaleString()}
          </p>
        </div>
      );
    case "order": {
      const { order } = view.result;
      return (
        <div className={box}>
          <p className="font-semibold">Local order {order.resource.order_id}</p>
          <p>
            {order.status} · Payment: {order.payment_status} · {order.currency} {order.total}
          </p>
          <p>
            Source: {order.channel} · Tracking: {order.tracking_no ?? "Not recorded"} · Carrier:{" "}
            {order.carrier ?? "Not recorded"}
          </p>
          <p className="mt-2 text-xs text-ink-500">
            Source: {view.result.source} · Marketplace sync: {view.result.marketplace_sync} · As
            of {new Date(view.result.as_of).toLocaleString()}
          </p>
        </div>
      );
    }
    case "proposal":
      return (
        <ProposalCard
          key={`${view.proposal.id}-${outcomes[view.proposal.id]?.status ?? ""}`}
          proposal={outcomes[view.proposal.id] ?? view.proposal}
          tenantId={tenant}
          onSettled={onSettled}
        />
      );
    case "answer":
      return <p className={`${box} whitespace-pre-wrap`}>{view.text}</p>;
    case "navigate":
      return (
        <p className="text-sm text-ink-600">
          Opened {tabLabel(view.tab, me, packages)}
          {view.resume ? " and continued there." : "."}
        </p>
      );
    case "directory":
      return (
        <div className={box}>
          <p className="mb-2">Choose a colleague:</p>
          <ul className="space-y-2">
            {view.candidates.map((person) => (
              <li key={person.employee_id}>
                <button
                  type="button"
                  data-testid="assistant-candidate"
                  className={`${secondary} w-full justify-start text-left`}
                  disabled={disabled || !answerable}
                  onClick={() => onAsk(candidateAnswer(person, origin))}
                >
                  <span>
                    <span className="font-medium">{person.display_name}</span>
                    {(person.job_title || person.work_email) && (
                      <span className="block text-xs text-ink-500">
                        {[person.job_title, person.work_email].filter(Boolean).join(" · ")}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      );
    case "clarification":
      return (
        <div className={box}>
          <p>{view.question}</p>
          {view.options.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {view.options.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={secondary}
                  disabled={disabled || !answerable}
                  onClick={() => onAsk(optionAnswer(option, view.question, origin))}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </div>
      );
  }
}
