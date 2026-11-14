"use client";

import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Text, Tooltip, XAxis, YAxis } from "recharts";
import { peakRow, priceTicks, type AgentRow, type ChartRow } from "@/lib/credit-view";

/**
 * The Credit tab's two charts. Each plots one series of AI Usage Charges, so the bars wear the theme's
 * primary colour (chosen per theme, so it holds on the light and night surfaces) and need no
 * legend: the heading names what is plotted. Colour never carries meaning on its own: bar length,
 * the value axis and value labels do, and every value is also in a visually hidden table.
 */

const BAR = "var(--fs-primary)";
const LABEL = { fill: "var(--fs-text)", fontSize: 11 };
const AXIS_TICK = { fill: "var(--fs-text-secondary)", fontSize: 11 };

type TipProps = { active?: boolean; payload?: { payload?: ChartRow }[] };

/** The hovered or focused bar's exact price first, then what it is. */
function PriceTip({ active, payload }: TipProps) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="chart-tooltip min-w-[120px]">
      <p className="num font-semibold text-[color:var(--fs-text)]">{row.text}</p>
      <p className="text-[color:var(--fs-text-secondary)]">{row.label}</p>
    </div>
  );
}

type LabelProps = { x?: number | string; y?: number | string; width?: number | string; height?: number | string; value?: unknown };

/** A column's value on one line just above its cap; nothing for an empty value. */
function CapLabel({ x = 0, y = 0, width = 0, value }: LabelProps) {
  if (typeof value !== "string" || !value) return null;
  return (
    <text x={Number(x) + Number(width) / 2} y={Number(y) - 6} textAnchor="middle" {...LABEL}>{value}</text>
  );
}

/** A bar's value on one line just past its end, shown for a zero-length bar too. */
function TipLabel({ x = 0, y = 0, width = 0, height = 0, value }: LabelProps) {
  if (typeof value !== "string" || !value) return null;
  return (
    <text x={Number(x) + Number(width) + 6} y={Number(y) + Number(height) / 2} dominantBaseline="central" {...LABEL}>
      {value}
    </text>
  );
}

function axisLabels(rows: ChartRow[]) {
  const { ticks, labels } = priceTicks(rows.map((row) => row.price));
  const byTick = new Map(ticks.map((tick, index) => [tick, labels[index]]));
  return { ticks, top: ticks[ticks.length - 1], label: (value: number) => byTick.get(value) ?? "" };
}

/** The text alternative: every row's exact price, read by screen readers, hidden on screen. */
function DataTable({ caption, rows, first }: { caption: string; rows: ChartRow[]; first: string }) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead><tr><th scope="col">{first}</th><th scope="col">AI Usage Charges</th></tr></thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}><th scope="row">{row.label}</th><td>{row.text}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

function Frame({ title, summary, testId, children }: {
  title: string; summary: string; testId: string; children: React.ReactNode;
}) {
  return (
    <figure data-testid={testId} className="min-w-0 rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)] p-4 shadow-[var(--fs-shadow-card)] sm:p-5">
      <figcaption className="mb-3">
        <h3 className="font-(family-name:--fs-font-heading) text-base font-semibold">{title}</h3>
        <p className="mt-0.5 text-sm text-[color:var(--fs-text-secondary)]">{summary}</p>
      </figcaption>
      {children}
    </figure>
  );
}

/** AI Usage Charges per day across the cycle: one column per day, days with no usage at zero. */
export function DailyChart({ rows }: { rows: ChartRow[] }) {
  const peak = peakRow(rows);
  const title = "AI Usage Charges per day";
  if (!peak) {
    return (
      <Frame title={title} summary="No AI Usage Charges on any day of this cycle yet." testId="credit-daily-chart">
        <DataTable caption={title} rows={rows} first="Day" />
      </Frame>
    );
  }
  const axis = axisLabels(rows);
  // Only the highest day carries a label on its column; the axis and the table carry the rest.
  const data = rows.map((row) => ({ ...row, peak: row.key === peak.key ? row.text : "" }));
  return (
    <Frame title={title} summary={`Highest day: ${peak.label}, ${peak.text}.`} testId="credit-daily-chart">
      <div className="h-64">
        <ResponsiveContainer>
          <BarChart data={data} barCategoryGap="20%" title={title}
            desc="Each column is one day of the cycle. The exact charges are in the table that follows.">
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tick={AXIS_TICK} minTickGap={12} />
            <YAxis type="number" domain={[0, axis.top]} ticks={axis.ticks} tickFormatter={axis.label}
              tickLine={false} axisLine={false} width={84} tick={AXIS_TICK} padding={{ top: 20 }} />
            <Tooltip content={<PriceTip />} cursor={{ radius: 4 }} />
            <Bar dataKey="value" name={title} fill={BAR} maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false}>
              <LabelList dataKey="peak" content={<CapLabel />} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable caption={title} rows={rows} first="Day" />
    </Frame>
  );
}

type TickProps = { x?: number | string; y?: number | string; payload?: { value?: string } };

/** An agent's title beside its bar, wrapped onto two lines when it is long. */
function AgentTick({ x = 0, y = 0, payload }: TickProps) {
  return (
    <Text x={Number(x) - 6} y={Number(y)} width={112} textAnchor="end" verticalAnchor="middle" {...AXIS_TICK}>
      {payload?.value ?? ""}
    </Text>
  );
}

/** AI Usage Charges by AI agent: one bar per agent, each labelled with its exact charges. */
export function AgentChart({ rows }: { rows: AgentRow[] }) {
  const peak = peakRow(rows);
  const title = "AI Usage Charges by AI agent";
  if (!peak) {
    return (
      <Frame title={title} summary="No AI Usage Charges for any AI agent in this cycle yet." testId="credit-agent-chart">
        <DataTable caption={title} rows={rows} first="AI agent" />
      </Frame>
    );
  }
  const axis = axisLabels(rows);
  // Room for the value label past the longest bar.
  const height = Math.max(120, rows.length * 52 + 40);
  return (
    <Frame title={title} summary={`Highest: ${peak.label}, ${peak.text}.`} testId="credit-agent-chart">
      <div style={{ height }}>
        <ResponsiveContainer>
          <BarChart data={rows} layout="vertical" barCategoryGap="30%" title={title}
            desc="Each bar is one AI agent. The exact charges are in the table that follows.">
            <CartesianGrid horizontal={false} />
            <XAxis type="number" domain={[0, axis.top]} ticks={axis.ticks} tickFormatter={axis.label}
              tickLine={false} axisLine={false} tick={AXIS_TICK} padding={{ right: 96 }} />
            <YAxis type="category" dataKey="label" tickLine={false} axisLine={false} width={120} tick={<AgentTick />} />
            <Tooltip content={<PriceTip />} cursor={{ radius: 4 }} />
            <Bar dataKey="value" name={title} fill={BAR} maxBarSize={24} radius={[0, 4, 4, 0]} isAnimationActive={false}>
              <LabelList dataKey="text" content={<TipLabel />} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable caption={title} rows={rows} first="AI agent" />
    </Frame>
  );
}
