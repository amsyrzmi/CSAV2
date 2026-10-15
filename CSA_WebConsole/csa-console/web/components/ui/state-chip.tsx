import clsx from "clsx";

const STATE: Record<string, { chip: string; dot: string; pulse?: boolean }> = {
  healthy: { chip: "bg-emerald-50 text-emerald-700 ring-emerald-600/15", dot: "bg-emerald-500" },
  recovering: { chip: "bg-sky-50 text-sky-700 ring-sky-600/15", dot: "bg-sky-500", pulse: true },
  degraded: { chip: "bg-amber-50 text-amber-700 ring-amber-600/20", dot: "bg-amber-500" },
  failing: { chip: "bg-red-50 text-red-700 ring-red-600/15", dot: "bg-red-500", pulse: true },
  workflow_off: { chip: "bg-orange-50 text-orange-700 ring-orange-600/15", dot: "bg-orange-500" },
  circuit_open: { chip: "bg-red-50 text-red-800 ring-red-600/25", dot: "bg-red-600", pulse: true },
  disabled: { chip: "bg-ink-100 text-ink-600 ring-ink-500/10", dot: "bg-ink-400" },
  idle: { chip: "bg-ink-50 text-ink-500 ring-ink-500/10", dot: "bg-ink-300" },
  working: { chip: "bg-emerald-50 text-emerald-700 ring-emerald-600/15", dot: "bg-emerald-500" },
  needs_attention: { chip: "bg-amber-50 text-amber-700 ring-amber-600/20", dot: "bg-amber-500" },
  quiet: { chip: "bg-ink-50 text-ink-500 ring-ink-500/10", dot: "bg-ink-300" },
  paused: { chip: "bg-ink-100 text-ink-600 ring-ink-500/10", dot: "bg-ink-400" },
  off: { chip: "bg-ink-100 text-ink-600 ring-ink-500/10", dot: "bg-ink-400" },
  client_off: { chip: "bg-ink-100 text-ink-600 ring-ink-500/10", dot: "bg-ink-400" },
  locked: { chip: "bg-ink-50 text-ink-500 ring-ink-500/10", dot: "bg-ink-300" },
};

const LABEL: Record<string, string> = {
  off: "switched off",
  client_off: "switched off by client",
  paused: "paused by provider",
};

export function StateChip({ state }: { state: string }) {
  const s = STATE[state] ?? { chip: "bg-ink-100 text-ink-600 ring-ink-500/10", dot: "bg-ink-400" };
  return (
    <span className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full py-[3px] pl-2 pr-2.5 text-[11.5px] font-semibold capitalize ring-1 ring-inset", s.chip)}>
      <span className="relative flex h-1.5 w-1.5">
        {s.pulse && <span className={clsx("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", s.dot)} />}
        <span className={clsx("relative inline-flex h-1.5 w-1.5 rounded-full", s.dot)} />
      </span>
      {LABEL[state] ?? state.replaceAll("_", " ")}
    </span>
  );
}
