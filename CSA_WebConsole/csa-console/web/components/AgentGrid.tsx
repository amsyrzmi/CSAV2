"use client";
import clsx from "clsx";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Bot, CheckCircle2, Lock, MessagesSquare, PauseCircle, PowerOff, UserRoundCheck } from "lucide-react";
import { api } from "@/lib/api";
import { ago, useNow } from "@/lib/time";
import { StackedBars, Legend } from "@/components/ui/chart";
import { Banner, Card, EmptyState, Skeleton, StatCard, StateChip, Switch } from "@/components/ui";

type Agent = {
  key: string; label: string; description: string; badge: string | null; state: string;
  runs_7d: number; success_pct: number | null; p50_ms: number | null; last_run_at: string | null;
  can_toggle: boolean; required: boolean;
};
type Resp = {
  as_of: string;
  totals: { runs_7d: number; success_pct: number | null; handoffs_7d: number };
  trend: { day: string; runs: number; ok: number }[];
  agents: Agent[];
  scope: { project_id: string | null; channels: number | null };
};

const secs = (ms: number | null) => (ms === null ? "-" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

const SERIES = [
  { key: "ok", name: "Handled", color: "#10b981" },
  { key: "issues", name: "Needed attention", color: "#fbbf24" },
];

export default function AgentGrid({ projectId, allowToggle = false }: { projectId?: string; allowToggle?: boolean }) {
  const now = useNow(1000);
  const qc = useQueryClient();
  const [switchError, setSwitchError] = useState("");
  const toggle = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      api(`/api/client/agents/${key}/enabled`, { method: "PUT", body: JSON.stringify({ enabled }) }),
    onMutate: () => setSwitchError(""),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["client-agents"] }),
    onError: (e) => setSwitchError((e as Error).message),
  });
  const q = useQuery({
    queryKey: ["client-agents", projectId ?? "all"],
    queryFn: () => api<Resp>(`/api/client/agents${projectId ? `?project_id=${projectId}` : ""}`),
    refetchInterval: 10_000,
  });
  if (q.isLoading) {
    return (
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-[118px] rounded-[14px]" />)}</div>
        <Skeleton className="h-[290px] rounded-[14px]" />
      </div>
    );
  }
  if (!q.data) return <Banner tone="error">{(q.error as Error)?.message}</Banner>;
  const d = q.data;
  const noChannel = d.scope.channels === 0;
  const pending = toggle.isPending ? toggle.variables?.key : undefined;

  return (
    <div className="space-y-4">
      {q.isError && <Banner tone="error">Could not refresh. Showing data from {ago(d.as_of, now)}.</Banner>}
      {switchError && <Banner tone="error" onClose={() => setSwitchError("")}>{switchError}</Banner>}
      {noChannel && <Banner tone="warn">This project has no channel yet, so there is no activity to show. Connect a channel in the project settings.</Banner>}

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard index={0} label="Conversations handled (7 days)" value={d.totals.runs_7d.toLocaleString()} icon={MessagesSquare} tone="brand" />
        <StatCard index={1} label="Success rate" value={d.totals.success_pct === null ? "-" : `${d.totals.success_pct}%`} icon={CheckCircle2} tone="good" />
        <StatCard index={2} label="Handed to your team" value={d.totals.handoffs_7d.toLocaleString()} icon={UserRoundCheck} tone="warn" />
      </div>

      <Card title="Activity per day" description="Last 14 days" actions={<Legend series={SERIES} />}>
        <StackedBars data={d.trend.map((t) => ({ day: t.day.slice(5, 10), ok: t.ok, issues: t.runs - t.ok }))} series={SERIES} height={200} />
      </Card>

      {d.agents.length === 0 ? <div className="card"><EmptyState icon={Bot} title="No agents yet" description="Your account manager will add assistants to your plan." /></div> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {d.agents.map((a, i) => {
            const locked = a.state === "locked";
            const off = a.state === "off";
            const paused = a.state === "paused";
            const showSwitch = allowToggle && a.can_toggle;
            const dim = locked || off || paused;
            return (
              <article key={a.key} style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
                className={clsx("card group relative flex flex-col overflow-hidden p-5 animate-fade-up",
                  locked ? "border-dashed bg-ink-50/60" : "hover:border-[var(--hairline-strong)] hover:shadow-[var(--shadow-md)]")}>
                <div className="flex items-start gap-3">
                  <span className={clsx("flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]",
                    dim ? "bg-ink-100 text-ink-400" : "bg-ink-900 text-white shadow-[0_4px_12px_-4px_rgb(11_18_32/0.45)]")}>
                    {locked ? <Lock size={16} /> : off ? <PowerOff size={16} /> : paused ? <PauseCircle size={16} /> : <Bot size={16} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <h3 className={clsx("truncate text-[14px] font-semibold tracking-tight", dim ? "text-ink-500" : "text-ink-900")}>{a.label}</h3>
                    <div className="mt-1"><StateChip state={a.state} /></div>
                  </div>
                  {showSwitch && (
                    <Switch on={!off} busy={pending === a.key} label={`${a.label} on or off`} onClick={() => {
                      if (!off && !confirm(`Switch off "${a.label}"? Your customers will no longer be able to use it. You can switch it back on at any time.`)) return;
                      toggle.mutate({ key: a.key, enabled: off });
                    }} />
                  )}
                </div>
                <p className={clsx("mt-3 text-[13px] leading-5", dim ? "text-ink-400" : "text-ink-600")}>{a.description}</p>
                {a.required && allowToggle && <p className="mt-2 text-xs text-ink-500">Always on. It is how customers reach your team.</p>}

                <div className="mt-auto pt-4">
                  {locked ? <p className="rounded-lg bg-white px-3 py-2 text-xs text-ink-500 ring-1 ring-ink-200">Not in your plan. Ask your account manager to add it.</p>
                    : off ? <p className="rounded-lg bg-ink-50 px-3 py-2 text-xs text-ink-600">Switched off. Customers cannot use this until it is switched back on.</p>
                    : paused ? <p className="rounded-lg bg-ink-50 px-3 py-2 text-xs text-ink-600">Paused by your provider. Contact your account manager.</p>
                    : (
                      <>
                        <dl className="grid grid-cols-3 divide-x divide-ink-100 rounded-lg border border-ink-100 bg-ink-50/50 py-2.5 text-center">
                          <div><dt className="text-[11px] text-ink-500">Runs (7d)</dt><dd className="num mt-0.5 text-[14px] font-semibold text-ink-900">{a.runs_7d}</dd></div>
                          <div><dt className="text-[11px] text-ink-500">Success</dt><dd className="num mt-0.5 text-[14px] font-semibold text-ink-900">{a.success_pct === null ? "-" : `${a.success_pct}%`}</dd></div>
                          <div><dt className="text-[11px] text-ink-500">Speed</dt><dd className="num mt-0.5 text-[14px] font-semibold text-ink-900">{secs(a.p50_ms)}</dd></div>
                        </dl>
                        <p className="mt-2.5 text-[11.5px] text-ink-500"><span className="capitalize">{a.badge}</span> agent · last active {a.last_run_at ? ago(a.last_run_at, now) : "never"}</p>
                      </>
                    )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
