"use client";

import type { Agent } from "@/lib/identity-contracts";
import { DetailsRow } from "../kit";
import { Notice, secondary } from "../ui";
import type { AgentState } from "./AddKnowledge";

const box =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

/** A quiet, closed-by-default row at the bottom of the page. */
function Quiet({ testId, title, children }: { testId: string; title: string; children: React.ReactNode }) {
  return (
    <div className={box}>
      <DetailsRow testId={testId} toggleLabel={title} summary={<p className="font-medium">{title}</p>}>
        {children}
      </DetailsRow>
    </div>
  );
}

/**
 * Maintenance: the technical text the page used to open with. Every sentence and control is
 * unchanged; it is only folded away. The one correction: the page no longer polls all the time,
 * so the polling sentence says when it does.
 */
export function Maintenance({
  agent,
  agentState,
  canWrite,
  writable,
  busy,
  onReconcile,
  onRetryAgent,
}: {
  agent: Agent | null;
  agentState: AgentState;
  canWrite: boolean;
  writable: boolean;
  busy: boolean;
  onReconcile: () => void;
  onRetryAgent: () => void;
}) {
  return (
    <Quiet testId="kb-maintenance" title="Maintenance">
      <div className="space-y-4">
        <Notice>Polling every 5 seconds while a file is being read. Lists show the latest 50 documents/gaps, 20 jobs and 100 sources.
          Uploads need configured storage and an ingestion worker. A queued job is not proof the worker is running.</Notice>
        <div>
          <h3 className="mb-2 font-semibold">Embedding compatibility</h3>
          {agent ? (
            <>
              <p>{agent.detail}</p>
              <p className="mt-2 text-sm">Model: {agent.embedding_model ?? "Not configured"}.
                Compatible chunks: {agent.compatible_chunks ?? "Unknown"}.
                Incompatible chunks: {agent.incompatible_chunks ?? "Unknown"}.
                No legacy FAQ files are imported automatically.</p>
              {!writable && <p className="mt-3 text-sm">{canWrite ? "Writes require both KB subscription and tenant switch on." : "Read-only access: upload, deletion and gap resolution are unavailable."}</p>}
            </>
          ) : agentState === "failed" ? (
            <p className="flex flex-wrap items-center gap-2">
              We could not load the assistant details.
              <button type="button" className={secondary} data-testid="kb-retry-agents-maintenance" onClick={onRetryAgent}>Try again</button>
            </p>
          ) : <p>Loading...</p>}
        </div>
        {canWrite && (
          <div>
            <button type="button" className={secondary} disabled={busy} onClick={onReconcile}>Reconcile orphaned storage</button>
          </div>
        )}
      </div>
    </Quiet>
  );
}

/** Settings: whether the knowledge assistant is on, and where to change it. */
export function Settings({
  agent,
  agentState,
  servicesHref,
  onRetryAgent,
}: {
  agent: Agent | null;
  agentState: AgentState;
  servicesHref: string;
  onRetryAgent: () => void;
}) {
  const on = agent?.capability?.active === true;
  return (
    <Quiet testId="kb-settings" title="Settings">
      {agent ? (
        <div className="space-y-2">
          <p>The knowledge assistant is {on ? "on" : "off"}.</p>
          <p className="text-[color:var(--fs-text-secondary)]">
            It answers customers from the files above only while it is on. You can switch it on or off in Services.
          </p>
          <a className={secondary} href={servicesHref}>Open Services</a>
        </div>
      ) : agentState === "failed" ? (
        <p className="flex flex-wrap items-center gap-2">
          We could not load the assistant settings.
          <button type="button" className={secondary} data-testid="kb-retry-agents-settings" onClick={onRetryAgent}>Try again</button>
        </p>
      ) : <p>Loading...</p>}
    </Quiet>
  );
}
