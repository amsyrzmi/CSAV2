"use client";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { AlertTriangle, Check, CheckCircle2, Copy, Info, X, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";

/* ---------- page header ---------- */
export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow?: string; title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4 animate-fade-up">
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow mb-2">{eyebrow}</p>}
        <h1 className="page-title">{title}</h1>
        {description && <p className="page-description mt-1.5 max-w-2xl">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ---------- button ---------- */
export function Button({ 
  children, variant = "primary", size = "md", className, disabled, type = "button", ...props 
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { 
  variant?: "primary" | "secondary" | "danger" | "ghost"; 
  size?: "sm" | "md" | "lg";
}) {
  const baseCls = "inline-flex items-center justify-center font-medium transition-all duration-200 active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none rounded-xl";
  
  const variants = {
    primary: "bg-brand-600 text-white shadow-sm hover:bg-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-600",
    secondary: "bg-white text-ink-700 shadow-sm ring-1 ring-inset ring-ink-200 hover:bg-ink-50",
    danger: "bg-red-600 text-white shadow-sm hover:bg-red-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600",
    ghost: "text-ink-600 hover:bg-ink-100 hover:text-ink-900",
  };
  
  const sizes = {
    sm: "h-8 px-3 text-xs gap-1.5",
    md: "h-10 px-4 text-sm gap-2",
    lg: "h-12 px-6 text-base gap-2",
  };

  return (
    <button type={type} disabled={disabled} className={clsx(baseCls, variants[variant], sizes[size], className)} {...props}>
      {children}
    </button>
  );
}

/* ---------- card ---------- */
export function Card({ title, description, actions, children, className, bodyClassName }: {

  title?: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode;
  children: React.ReactNode; className?: string; bodyClassName?: string;
}) {
  return (
    <section className={clsx("card overflow-hidden", className)}>
      {(title || actions) && (
        <header className="card-header">
          <div className="min-w-0">
            {title && <h2 className="section-title">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-ink-500">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={clsx(bodyClassName ?? "p-5")}>{children}</div>
    </section>
  );
}

/* ---------- stat card ---------- */
const TONE: Record<string, string> = {
  neutral: "bg-ink-100 text-ink-600",
  brand: "bg-brand-50 text-brand-600",
  good: "bg-emerald-50 text-emerald-600",
  warn: "bg-amber-50 text-amber-600",
  bad: "bg-red-50 text-red-600",
};

export function StatCard({ label, value, detail, icon: Icon, tone = "neutral", index = 0 }: {
  label: string; value: React.ReactNode; detail?: React.ReactNode; icon?: LucideIcon;
  tone?: keyof typeof TONE; index?: number;
}) {
  return (
    <div className="card group relative overflow-hidden p-5 animate-fade-up hover:border-[var(--hairline-strong)] hover:shadow-[var(--shadow-md)]"
         style={{ animationDelay: `${index * 50}ms` }}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[12.5px] font-medium text-ink-500">{label}</p>
        {Icon && (
          <span className={clsx("flex h-8 w-8 items-center justify-center rounded-lg transition-transform duration-300 group-hover:scale-110", TONE[tone])}>
            <Icon size={16} strokeWidth={2} />
          </span>
        )}
      </div>
      <p className="num mt-2 text-[28px] font-semibold leading-none text-ink-900">{value}</p>
      {detail && <p className="mt-2.5 text-xs text-ink-500">{detail}</p>}
    </div>
  );
}

/* ---------- banner ---------- */
const BANNER = {
  info: { cls: "border-brand-200 bg-brand-50/70 text-brand-900", icon: Info },
  warn: { cls: "border-amber-200 bg-amber-50/70 text-amber-900", icon: AlertTriangle },
  error: { cls: "border-red-200 bg-red-50/70 text-red-900", icon: XCircle },
  success: { cls: "border-emerald-200 bg-emerald-50/70 text-emerald-900", icon: CheckCircle2 },
};

export function Banner({ tone = "info", children, onClose, className }: {
  tone?: keyof typeof BANNER; children: React.ReactNode; onClose?: () => void; className?: string;
}) {
  const { cls, icon: Icon } = BANNER[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"}
         className={clsx("flex items-start gap-3 rounded-xl border px-4 py-3 text-[13px] leading-5 animate-fade-in", cls, className)}>
      <Icon size={16} className="mt-0.5 shrink-0 opacity-80" />
      <div className="min-w-0 flex-1">{children}</div>
      {onClose && (
        <button onClick={onClose} aria-label="Dismiss" className="-mr-1 rounded-md p-0.5 opacity-60 hover:bg-black/5 hover:opacity-100">
          <X size={14} />
        </button>
      )}
    </div>
  );
}

/* ---------- empty state ---------- */
export function EmptyState({ icon: Icon, title, description, action }: {
  icon: LucideIcon; title: string; description?: React.ReactNode; action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-ink-200 bg-white text-ink-400 shadow-[var(--shadow-xs)]">
        <Icon size={20} />
      </span>
      <p className="text-sm font-semibold text-ink-900">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] leading-5 text-ink-500">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/* ---------- skeletons ---------- */
export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} />;
}

export function PageSkeleton({ stats = 4, rows = 6 }: { stats?: number; rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      <Skeleton className="mb-2 h-3 w-24" />
      <Skeleton className="mb-8 h-7 w-64" />
      {stats > 0 && (
        <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: stats }).map((_, i) => <Skeleton key={i} className="h-[118px] rounded-[14px]" />)}
        </div>
      )}
      <div className="card space-y-3 p-5">
        {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-9" />)}
      </div>
    </div>
  );
}

/* ---------- tabs ---------- */
export function Tabs<T extends string>({ value, onChange, items }: {
  value: T; onChange: (v: T) => void; items: { value: T; label: string; count?: number }[];
}) {
  return (
    <div role="tablist" className="tabs">
      {items.map((t) => (
        <button key={t.value} role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)} className="tab inline-flex items-center gap-1.5">
          {t.label}
          {t.count !== undefined && (
            <span className={clsx("num rounded-full px-1.5 text-[11px] font-semibold", value === t.value ? "bg-ink-100 text-ink-700" : "bg-ink-200/70 text-ink-500")}>{t.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/* ---------- switch ---------- */
export function Switch({ on, busy, label, onClick }: { on: boolean; busy?: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button" role="switch" aria-checked={on} aria-label={label} disabled={busy} onClick={onClick}
      className={clsx(
        "relative inline-flex h-[22px] w-[38px] shrink-0 items-center rounded-full p-[2px] shadow-[inset_0_1px_2px_rgb(11_18_32/0.12)] disabled:opacity-50",
        on ? "bg-emerald-500" : "bg-ink-300",
      )}
    >
      <span className={clsx("h-[18px] w-[18px] rounded-full bg-white shadow-[0_1px_3px_rgb(11_18_32/0.25)] transition-transform duration-200 ease-[cubic-bezier(0.22,1,0.36,1)]", on ? "translate-x-4" : "translate-x-0")} />
    </button>
  );
}

/* ---------- live indicator ---------- */
export function LiveIndicator({ ok, label }: { ok: boolean; label: React.ReactNode }) {
  return (
    <span className={clsx(
      "inline-flex items-center gap-2 rounded-full border bg-white px-3 py-1 text-xs font-medium shadow-[var(--shadow-xs)]",
      ok ? "border-emerald-200 text-emerald-700" : "border-red-200 text-red-700",
    )}>
      <span className="relative flex h-2 w-2">
        {ok && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />}
        <span className={clsx("relative inline-flex h-2 w-2 rounded-full", ok ? "bg-emerald-500" : "bg-red-500")} />
      </span>
      {label}
    </span>
  );
}

/* ---------- copy button ---------- */
export function CopyButton({ value, className }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <button type="button"
      onClick={() => navigator.clipboard.writeText(value).then(() => setCopied(true))}
      className={clsx("inline-flex h-8 items-center gap-1.5 rounded-lg border border-ink-200 bg-white px-2.5 text-xs font-medium text-ink-700 shadow-[var(--shadow-xs)] hover:bg-ink-50", className)}>
      {copied ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

/* ---------- secret link (shown once) ---------- */
export function OneTimeLink({ link, onClose }: { link: string; onClose?: () => void }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-gradient-to-b from-amber-50 to-amber-50/40 p-4 animate-scale-in">
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <p className="text-[13px] font-semibold text-amber-900">Copy this link now. It is shown only once.</p>
        {onClose && <button onClick={onClose} aria-label="Dismiss" className="rounded-md p-0.5 text-amber-700/70 hover:bg-amber-100 hover:text-amber-900"><X size={14} /></button>}
      </div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg border border-amber-200/70 bg-white px-3 py-2 font-mono text-xs text-ink-700">{link}</code>
        <CopyButton value={link} />
      </div>
    </div>
  );
}

/* ---------- drawer ---------- */
export function Drawer({ open, onClose, title, children }: {
  open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-ink-950/25 backdrop-blur-[2px] animate-fade-in" onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l border-ink-200 bg-white shadow-[var(--shadow-lg)] animate-[drawer-in_260ms_cubic-bezier(0.22,1,0.36,1)_both]">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-ink-100 px-5">
          <h2 className="section-title">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-ink-500 hover:bg-ink-100 hover:text-ink-900"><X size={16} /></button>
        </header>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </aside>
    </div>
  );
}

/* ---------- modal ---------- */
export function Modal({ isOpen, onClose, title, children, maxWidth = "max-w-lg" }: {
  isOpen: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode; maxWidth?: string;
}) {
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="fixed inset-0 bg-ink-950/30 backdrop-blur-xs animate-fade-in" onClick={onClose} />
      <div className={clsx("relative w-full rounded-2xl border border-ink-100 bg-white p-6 shadow-xl animate-fade-up max-h-[90vh] overflow-y-auto", maxWidth)}>
        <div className="flex items-center justify-between border-b border-ink-100 pb-3 mb-4">
          <h3 className="text-base font-semibold text-ink-900">{title}</h3>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-ink-400 hover:bg-ink-100 hover:text-ink-900">
            <X size={16} />
          </button>
        </div>
        <div>{children}</div>
      </div>
    </div>
  );
}

/* ---------- small helpers ---------- */
export function Avatar({ name, size = 32, className }: { name: string; size?: number; className?: string }) {
  const initials = name.trim().split(/\s+/).slice(0, 2).map((s) => s[0]?.toUpperCase() ?? "").join("") || "U";
  return (
    <span style={{ width: size, height: size, fontSize: size * 0.38 }}
          className={clsx("inline-flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-600 font-semibold text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.2)]", className)}>
      {initials}
    </span>
  );
}

export function Pill({ children, tone = "neutral", className }: { children: React.ReactNode; tone?: "neutral" | "brand" | "good" | "warn" | "bad"; className?: string }) {
  const map = {
    neutral: "bg-ink-100 text-ink-600 ring-ink-500/10",
    brand: "bg-brand-50 text-brand-700 ring-brand-600/15",
    good: "bg-emerald-50 text-emerald-700 ring-emerald-600/15",
    warn: "bg-amber-50 text-amber-700 ring-amber-600/15",
    bad: "bg-red-50 text-red-700 ring-red-600/15",
  };
  return <span className={clsx("inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-semibold capitalize ring-1 ring-inset", map[tone], className)}>{children}</span>;
}

