"use client";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Series = { key: string; name: string; color: string };

type TipProps = { active?: boolean; label?: string; payload?: { name?: string; value?: number; color?: string }[] };

function ChartTooltip({ active, label, payload }: TipProps) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((s, p) => s + (Number(p.value) || 0), 0);
  return (
    <div className="chart-tooltip min-w-[150px]">
      <p className="mb-1.5 font-semibold text-ink-900">{label}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center justify-between gap-4 py-0.5 text-ink-600">
          <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />{p.name}</span>
          <span className="num font-semibold text-ink-900">{p.value}</span>
        </div>
      ))}
      {payload.length > 1 && (
        <div className="mt-1.5 flex justify-between border-t border-ink-100 pt-1.5 text-ink-500">
          <span>Total</span><span className="num font-semibold text-ink-900">{total}</span>
        </div>
      )}
    </div>
  );
}

/** Stacked daily bars. `data` rows must contain `day` plus each series key. */
export function StackedBars({ data, series, height = 220 }: { data: Record<string, string | number>[]; series: Series[]; height?: number }) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 4, left: -18, bottom: 0 }} barCategoryGap="28%">
          <CartesianGrid vertical={false} />
          <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={8} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={40} />
          <Tooltip content={<ChartTooltip />} cursor={{ radius: 6 }} />
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} stackId="a" fill={s.color} maxBarSize={28}
                 radius={i === series.length - 1 ? [5, 5, 0, 0] : [0, 0, 0, 0]} animationDuration={600} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Legend({ series }: { series: Series[] }) {
  return (
    <div className="flex items-center gap-4 text-xs text-ink-500">
      {series.map((s) => (
        <span key={s.key} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: s.color }} />{s.name}</span>
      ))}
    </div>
  );
}
