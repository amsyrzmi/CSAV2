"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { AlertTriangle, CheckCircle2, Inbox } from "lucide-react";
import { api } from "@/lib/api";
import { ago, useNow } from "@/lib/time";
import { Card, EmptyState, Skeleton, btnGhost } from "@/components/ui";

type Resp = { items: { agent: string; outcome: string; response_ms: number | null; at: string }[]; next_cursor: string | null };

export default function ActivityFeed({ projectId }: { projectId?: string }) {
  const now = useNow(5000);
  const [cursor, setCursor] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["client-activity", projectId ?? "all", cursor],
    queryFn: () => api<Resp>(`/api/client/activity?limit=20${projectId ? `&project_id=${projectId}` : ""}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
    refetchInterval: cursor ? false : 10_000,
  });
  return (
    <Card title="Recent activity" description="Latest conversations" bodyClassName="p-0">
      <div className="table-frame rounded-none border-0 shadow-none">
        <table className="min-w-[560px]">
          <thead><tr><th>When</th><th>What handled it</th><th>Result</th><th>Speed</th></tr></thead>
          <tbody>
            {q.isLoading && Array.from({ length: 5 }).map((_, i) => <tr key={i}><td colSpan={4}><Skeleton className="h-5" /></td></tr>)}
            {q.data?.items.map((r, i) => (
              <tr key={i}>
                <td className="whitespace-nowrap">
                  <span className="text-ink-800">{ago(r.at, now)}</span>
                  <div className="num text-[11.5px] text-ink-400">{new Date(r.at).toLocaleString()}</div>
                </td>
                <td className="font-medium text-ink-900">{r.agent}</td>
                <td>
                  {r.outcome === "handled"
                    ? <span className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-emerald-700"><CheckCircle2 size={14} /> Handled</span>
                    : <span className="inline-flex items-center gap-1.5 text-[12.5px] font-medium text-amber-700"><AlertTriangle size={14} /> Needed attention</span>}
                </td>
                <td className="num text-ink-600">{r.response_ms === null ? "-" : `${(r.response_ms / 1000).toFixed(1)} s`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {q.data && q.data.items.length === 0 && <EmptyState icon={Inbox} title="No activity yet" description="Conversations will appear here as customers reach you." />}
      {(cursor || q.data?.next_cursor) && (
        <div className="flex justify-end gap-2 border-t border-ink-100 p-3">
          {cursor && <button className={btnGhost} onClick={() => setCursor(null)}>Back to newest</button>}
          {q.data?.next_cursor && <button className={btnGhost} onClick={() => setCursor(q.data!.next_cursor)}>Older</button>}
        </div>
      )}
    </Card>
  );
}
